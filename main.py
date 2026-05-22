"""AI Trader 完整调度引擎 v2
全市场扫描 → 规则筛选 → AI确认 → 智能定价 → 自动下单 → 推飞书
"""
import json
import logging
import os
import time
from datetime import datetime

import config
import market_data as md
import strategy
import ai_analyzer
import notifier
import trade_logger as tlog
import price_alert
import stock_pool
import prediction_tracker
import kb_integration
import html_report

from ths_trader import THSTrader

# 日志配置
log_file = os.path.join(config.LOG_DIR, f'engine_{datetime.now().strftime("%Y%m%d")}.log')
os.makedirs(os.path.dirname(log_file), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

_trader = None


def get_trader():
    global _trader
    if _trader is None:
        _trader = THSTrader()
        if not _trader.connect():
            logger.error("THS连接失败")
            _trader = None
    return _trader


# ========== 买入策略 ==========

def _calc_buy_price_strategy_a(current_price, indicators):
    """策略A：限价单等回调
    在支撑位附近挂单，能买到就买，买不到不追
    """
    ma5 = indicators.get('ma5', current_price)
    bb_lower = indicators.get('bb_lower', current_price * 0.97)

    # 价格在MA5和布林下轨之间取中点
    support = max(ma5 * 0.995, bb_lower * 1.005)
    limit_price = round(support, 2)

    # 如果当前价已经很低（离支撑位<1%），直接用当前价
    if current_price <= limit_price * 1.01:
        limit_price = round(current_price * 0.999, 2)  # 略低于现价

    return limit_price, 'limit'


def _calc_buy_price_strategy_b(current_price, indicators):
    """策略B：市价单直接买
    不等回调，确认信号后直接以当前价买入
    """
    return round(current_price, 2), 'market'


def calculate_buy_price(current_price, indicators, strategy_name='auto'):
    """计算买入价格
    Returns: (price, order_type)
    """
    if strategy_name == 'limit':
        return _calc_buy_price_strategy_a(current_price, indicators)
    elif strategy_name == 'market':
        return _calc_buy_price_strategy_b(current_price, indicators)
    else:
        # auto：根据市场情况选择
        pct = indicators.get('pct_change', 0)
        vol_ratio = indicators.get('vol_ratio', 1)
        # 放量上涨时用市价（怕错过），缩量震荡时用限价（等便宜）
        if vol_ratio > 2.0 and pct > 1:
            return _calc_buy_price_strategy_b(current_price, indicators)
        else:
            return _calc_buy_price_strategy_a(current_price, indicators)


# ========== 核心分析管线 ==========

SIGNALS_FILE = os.path.join(config.DATA_DIR, 'dashboard_signals.json')


def _save_dashboard_signal(action, code, name, price, quantity, stop_loss, take_profit, confidence, reasoning, chart_file=None):
    """保存信号到仪表盘显示文件"""
    signals = []
    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE, 'r', encoding='utf-8') as f:
            signals = json.load(f)

    signals.append({
        'time': datetime.now().strftime('%H:%M:%S'),
        'action': action,
        'code': code,
        'name': name,
        'price': price,
        'quantity': quantity,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'confidence': confidence,
        'reasoning': reasoning[:500],
        'chart': chart_file,
    })

    # 只保留今天的信号
    today = datetime.now().strftime('%Y-%m-%d')
    signals = [s for s in signals if s.get('date', today) == today]
    for s in signals:
        s['date'] = today

    with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)

    logger.info(f"信号已保存到仪表盘: {action} {name}({code})")


def analyze_and_trade():
    """完整一轮分析"""
    now = datetime.now()
    logger.info(f"{'='*40} {now.strftime('%H:%M')} {'='*40}")

    # 1. 大盘概况 + 热门板块
    overview = md.get_market_overview()
    tlog.log_market(overview)

    # 1.5 获取热门板块
    hot_sectors = md.get_hot_sectors()
    if hot_sectors:
        top3 = ', '.join(f"{s['name']}({s['change_pct']:+.1f}%)" for s in hot_sectors[:3])
        logger.info(f"热门板块: {top3}")

    # 1.6 大盘环境AI判断（每轮调用1次）
    positions = strategy.get_local_positions()
    market_env = ai_analyzer.assess_market_environment(overview, hot_sectors, len(positions))
    aggressiveness = market_env.get('aggressiveness', 0.5)
    buy_confidence_threshold = 1.0 if aggressiveness < 0.3 else (0.75 if aggressiveness < 0.6 else 0.6)

    if aggressiveness < 0.3:
        logger.info(f"大盘环境差(aggressiveness={aggressiveness})，暂停买入推荐")
    elif aggressiveness < 0.6:
        logger.info(f"大盘环境偏弱(aggressiveness={aggressiveness})，提高买入门槛至{buy_confidence_threshold}")

    # 2. 全市场扫描 + AI批量筛选（三层管线）
    scan_candidates = []
    if now.minute < 5:  # 每小时的前5分钟做一次全市场扫描
        # Tier 1: 规则打分（无AI）
        raw_candidates = stock_pool.scan_market(10)
        for c in raw_candidates:
            tlog.log_signal(c['code'], c['name'], 'scan',
                           f"扫描发现: {', '.join(c['signals'])} (得分{c['score']})")

        # Tier 2: AI批量筛选（1次API调用，选出最多5只）
        if raw_candidates:
            screened = ai_analyzer.screen_stocks(raw_candidates, overview)
            if screened:
                # screen_stocks返回的格式是 {stock_code, stock_name, ...}
                for s in screened:
                    scan_candidates.append({
                        'code': s.get('stock_code', s.get('code', '')),
                        'name': s.get('stock_name', s.get('name', '')),
                        'score': s.get('score', 0),
                        'signals': s.get('signals', []),
                    })
                logger.info(f"AI筛选: {len(raw_candidates)}只 → {len(scan_candidates)}只")
            else:
                logger.warning("AI筛选返回空，使用原始候选")
                scan_candidates = raw_candidates[:5]

    # 3. 关注列表（每轮必查）
    watchlist = strategy.get_watchlist()
    all_targets = list(watchlist)

    # 把扫描发现的候选也加进来（去重）
    watch_codes = {s['code'] for s in watchlist}
    for c in scan_candidates:
        if c['code'] not in watch_codes:
            all_targets.append(c)

    # 4. 逐只分析
    for stock in all_targets:
        code = stock['code']
        name = stock.get('name', '')

        df = md.get_stock_history(code, days=120)
        if len(df) < 30:
            continue

        indicators = md.get_technical_indicators(df)
        tlog.log_indicators(code, name, indicators)

        # 规则筛选
        rule_signal = strategy.rule_signal(indicators)

        # 收集信号
        signals = []
        if indicators.get('macd_golden_cross'): signals.append('MACD金叉')
        if indicators.get('rsi_oversold'): signals.append(f'RSI超卖({indicators["rsi"]:.0f})')
        if indicators.get('ma_golden_cross'): signals.append('均线金叉')
        if indicators.get('volume_surge'): signals.append('放量')
        if indicators.get('macd_death_cross'): signals.append('MACD死叉')
        if indicators.get('rsi_overbought'): signals.append('RSI超买')
        signal_detail = ', '.join(signals) if signals else '无'

        tlog.log_signal(code, name, rule_signal, signal_detail)

        if rule_signal not in ('buy', 'sell', 'watch'):
            logger.info(f"  {name}: {rule_signal} ({signal_detail})")
            continue

        # watch 信号也需要AI分析（看是否值得买）
        if rule_signal == 'watch':
            logger.info(f"  {name}: watch ({signal_detail}) → 调AI评估")

        # 有信号 → 调AI
        logger.info(f"  {name}: {rule_signal} ({signal_detail}) → 调AI确认")
        realtime = md.get_realtime_quote(code)
        ai_result = ai_analyzer.analyze_stock(
            stock_info=realtime,
            indicators=indicators,
            market_overview=overview,
            hot_sectors=hot_sectors,
        )

        if 'error' in ai_result:
            logger.error(f"  AI失败: {ai_result['error']}")
            continue

        rec = ai_result.get('recommendation', '观望')
        conf = ai_result.get('confidence', 0)
        reasoning = ai_result.get('reasoning', '')
        risk = ai_result.get('risk_level', '中')
        stop_loss = ai_result.get('stop_loss_price')
        take_profit = ai_result.get('take_profit_price')

        tlog.log_ai_decision(code, name, rec, conf, reasoning, risk, stop_loss, take_profit)

        # 不自动下单，只推飞书推荐
        if rec == '买入' and conf >= buy_confidence_threshold:
            # 买入前仓位检查
            current_positions = strategy.get_local_positions()

            # 已达最大持仓数
            if len(current_positions) >= config.MAX_POSITIONS:
                logger.info(f"  跳过买入: 已达最大持仓数({config.MAX_POSITIONS})")
                continue

            # 已持有该股票
            if any(p['code'] == code for p in current_positions):
                logger.info(f"  跳过买入: 已持有 {name}")
                continue

            # 同板块已持有2只
            stock_sector = strategy.SECTOR_MAP.get(code, '其他')
            held_same_sector = sum(1 for p in current_positions
                                   if strategy.SECTOR_MAP.get(p['code'], '其他') == stock_sector)
            if held_same_sector >= 2:
                logger.info(f"  跳过买入: {stock_sector}板块已持有{held_same_sector}只")
                continue

            # 可用资金检查
            total_invested = sum(p.get('cost', 0) for p in current_positions)
            available = config.TOTAL_CAPITAL - total_invested
            if available < config.TOTAL_CAPITAL * config.POSITION_RATIO:
                logger.info(f"  跳过买入: 可用资金不足")
                continue
            buy_price, order_type = calculate_buy_price(indicators['close'], indicators, 'auto')
            suggest_qty = int(config.TOTAL_CAPITAL * config.POSITION_RATIO / buy_price)
            suggest_qty = (suggest_qty // 100) * 100
            suggest_cost = round(suggest_qty * buy_price, 2)

            # 生成图表
            import chart_gen
            chart_path = chart_gen.generate_stock_chart(code, name, df)
            chart_file = os.path.basename(chart_path) if chart_path else None

            # 保存到仪表盘信号文件
            _save_dashboard_signal('buy', code, name, buy_price, suggest_qty,
                                   stop_loss, take_profit, conf, reasoning, chart_file)

            notifier.send(
                f"买入推荐: {name}({code})",
                f"**建议买入**\n"
                f"股票: {name} ({code})\n"
                f"建议价格: {buy_price} ({order_type}单)\n"
                f"建议数量: {suggest_qty}股 ({suggest_cost}元)\n"
                f"止损: {stop_loss} | 止盈: {take_profit}\n"
                f"置信度: {conf:.0%}\n\n"
                f"**操作指引**:\n"
                f"- 如果开盘就涨了2%以上，不追高，等回调\n"
                f"- 跌破{stop_loss}元止损\n"
                f"- 涨到{take_profit}元考虑减仓一半锁利润\n\n"
                f"**分析**: {reasoning[:200]}"
            )

            if stop_loss:
                price_alert.add_alert(code, name, stop_loss, 'below', f'止损线: 亏损约5%')
            if take_profit:
                price_alert.add_alert(code, name, take_profit, 'above', f'止盈线: 盈利约15%')

        elif rec == '卖出' and conf >= 0.6:
            positions = strategy.get_local_positions()
            pos = next((p for p in positions if p['code'] == code), None)
            sell_qty = pos['quantity'] if pos else '未知'

            # 生成图表
            import chart_gen
            chart_path = chart_gen.generate_stock_chart(code, name, df)
            chart_file = os.path.basename(chart_path) if chart_path else None

            _save_dashboard_signal('sell', code, name, indicators['close'], sell_qty,
                                   stop_loss, take_profit, conf, reasoning, chart_file)

            notifier.send(
                f"卖出推荐: {name}({code})",
                f"**建议卖出**\n"
                f"股票: {name} ({code})\n"
                f"当前价: {indicators['close']}\n"
                f"建议数量: {sell_qty}股\n"
                f"置信度: {conf:.0%}\n\n"
                f"**分析**: {reasoning[:200]}"
            )

        elif rec == '观望' and 'RSI超卖' in signal_detail:
            ma5 = indicators.get('ma5', 0)
            if ma5:
                existing = [a for a in price_alert.get_active_alerts()
                            if a['code'] == code and a['direction'] == 'above']
                if not existing:
                    price_alert.add_alert(code, name, ma5, 'above',
                        f'AI建议: RSI超卖但等突破MA5({ma5})再入场')

    # 5. 检查价格预警
    _check_price_alerts()

    # 6. 检查持仓止损止盈
    _check_positions()

    logger.info(f"{'='*40} {now.strftime('%H:%M')} 完成 {'='*40}")


def _execute_buy(code, name, price, order_type, reason, confidence):
    """执行买入"""
    trader = get_trader()
    if not trader:
        logger.error(f"  买入失败: THS未连接")
        notifier.send(f"买入失败: {name}", f"THS未连接，无法自动下单\n原因: {reason[:100]}")
        return

    balance = trader.get_balance()
    available_str = balance.get('_可用金额值', '0')
    available = float(available_str.replace(',', '') if available_str else 0) or config.TOTAL_CAPITAL * config.POSITION_RATIO

    quantity = strategy.calculate_quantity(price, available)
    if quantity <= 0:
        logger.warning(f"  资金不足")
        return

    logger.info(f"  执行买入: {name}({code}) {quantity}股 @ {price} ({order_type}单)")
    success = trader.buy(code, quantity)

    tlog.log_trade('buy', code, name, price, quantity,
                   f"AI置信度{confidence:.0%} | {order_type}单 | {reason[:200]}", success)

    if success:
        strategy.save_position(code, name, quantity, price)
        notifier.send_buy_signal(code, name, price, quantity,
            f"已自动买入({order_type}单)。AI分析: {reason[:200]}")
    else:
        notifier.send(f"买入失败: {name}", f"{name}({code}) 委托提交失败")


def _execute_sell(code, name, price, reason, confidence):
    """执行卖出"""
    trader = get_trader()
    if not trader:
        return

    positions = strategy.get_local_positions()
    pos = next((p for p in positions if p['code'] == code), None)
    if not pos:
        return

    quantity = pos['quantity']
    logger.info(f"  执行卖出: {name}({code}) {quantity}股 @ {price}")
    success = trader.sell(code, quantity)

    tlog.log_trade('sell', code, name, price, quantity,
                   f"AI置信度{confidence:.0%} | {reason[:200]}", success)

    if success:
        strategy.remove_position(code, price)
        notifier.send_sell_signal(code, name, price, f"已自动卖出。AI分析: {reason[:200]}")


def _check_price_alerts():
    """检查条件预警"""
    positions = strategy.get_local_positions()
    active_alerts = price_alert.get_active_alerts()
    if not active_alerts:
        return

    # 获取所有预警涉及的股票价格
    codes_to_check = set(a['code'] for a in active_alerts)
    for pos in positions:
        codes_to_check.add(pos['code'])

    current_prices = {}
    for code in codes_to_check:
        quote = md.get_realtime_quote(code)
        if quote:
            current_prices[code] = quote['price']

    triggered = price_alert.check_alerts(current_prices)
    for alert in triggered:
        direction = '上穿' if alert['direction'] == 'above' else '下破'
        notifier.send(
            f"价格预警触发: {alert['name']}",
            f"{alert['name']}({alert['code']})\n"
            f"现价 {alert.get('triggered_price', '?')} {direction} {alert['target']}\n"
            f"原因: {alert['reason']}\n\n"
            f"请关注是否需要操作"
        )


def _handle_single_position_risk(risk):
    """单只股票仓位过重：分析行情后给出具体减仓建议"""
    pos = risk['position']
    code = pos['code']
    name = pos['name']
    weight = risk['weight']
    current_price = risk['current_price']
    gain_pct = risk['gain_pct']
    total_value = risk['total_value']

    # 检查通知去重
    if not notifier._can_send(code, 'concentration'):
        return

    # 获取技术指标分析当前行情
    df = md.get_stock_history(code, days=60)
    indicators = md.get_technical_indicators(df) if len(df) >= 30 else {}

    # 根据技术面判断操作建议
    if indicators:
        ma5 = indicators.get('ma5', 0)
        ma20 = indicators.get('ma20', 0)
        rsi = indicators.get('rsi', 50)
        macd_hist = indicators.get('macd_hist', 0)
        macd_hist_prev = indicators.get('macd_hist_prev', 0)
        trend_up = ma5 > ma20 if ma5 and ma20 else False

        # 技术面评分：决定"减多少"
        tech_score = 0
        tech_details = []

        if trend_up:
            tech_score += 1
            tech_details.append(f"趋势向上(MA5>{ma5:.2f}>MA20>{ma20:.2f})")
        else:
            tech_score -= 1
            tech_details.append(f"趋势向下(MA5<{ma5:.2f}<MA20<{ma20:.2f})")

        if rsi > 70:
            tech_score -= 1
            tech_details.append(f"RSI={rsi:.0f}超买")
        elif rsi < 30:
            tech_score += 1
            tech_details.append(f"RSI={rsi:.0f}超卖(可能反弹)")

        if macd_hist > 0 and macd_hist > macd_hist_prev:
            tech_score += 1
            tech_details.append("MACD红柱放大")
        elif macd_hist < 0 and macd_hist < macd_hist_prev:
            tech_score -= 1
            tech_details.append("MACD绿柱放大(下跌加速)")

        # 根据技术面+盈亏决定减仓比例
        if gain_pct > 15 and tech_score <= -1:
            # 高盈利 + 技术面恶化 → 大幅减仓
            sell_pct = 50
            reason = "盈利丰厚但技术面转弱，建议锁定一半利润"
            sell_price = round(current_price * 0.999, 2)  # 略低于现价挂单
            action_type = "减仓一半"
        elif gain_pct > 5 and tech_score <= 0:
            # 有盈利 + 技术面偏弱 → 中等减仓
            sell_pct = 30
            reason = "盈利尚可但集中度过高，先减一部分降风险"
            sell_price = round(current_price * 0.999, 2)
            action_type = "减仓30%"
        elif gain_pct > 0:
            # 小盈利或技术面还行 → 小幅减仓
            sell_pct = 20
            reason = "集中度过高需要分散，小幅减仓降低风险"
            sell_price = round(current_price * 0.998, 2)
            action_type = "减仓20%"
        elif gain_pct > -5:
            # 小亏 → 考虑减仓但不急
            sell_pct = 20
            reason = "仓位过重且小幅亏损，减仓降低风险敞口"
            sell_price = round(current_price * 0.998, 2)
            action_type = "减仓20%"
        else:
            # 亏损较大 → 看止损线
            sell_pct = 0
            reason = "亏损较大，先看是否触发止损，止损前不主动减仓"
            sell_price = current_price
            action_type = "暂不减仓"
    else:
        # 无技术指标数据
        sell_pct = 20 if gain_pct > 0 else 0
        reason = "集中度过高，建议适度减仓分散风险"
        sell_price = round(current_price * 0.999, 2)
        action_type = f"减仓{sell_pct}%" if sell_pct > 0 else "暂不减仓"
        tech_details = ["数据不足，无法深度分析"]

    # 计算减仓数量
    sell_qty = int(pos['quantity'] * sell_pct / 100)
    sell_qty = (sell_qty // 100) * 100  # 取整到100股
    sell_value = round(sell_qty * sell_price, 2)

    # 目标仓位比例
    target_weight = 12  # config.POSITION_RATIO
    current_value = current_price * pos['quantity']
    target_value = total_value * target_weight / 100
    target_qty = int(target_value / current_price / 100) * 100

    # 构建通知内容
    content = (
        f"**{name}({code}) 持仓过重**\n"
        f"当前仓位占比: {weight:.0f}%（上限{target_weight}%）\n"
        f"买入价: {pos['buy_price']} → 现价: {current_price}\n"
        f"盈亏: {gain_pct:+.1f}%\n\n"
        f"**行情分析**:\n"
        f"{' | '.join(tech_details)}\n\n"
        f"**操作建议: {action_type}**\n"
    )

    if sell_qty > 0:
        content += (
            f"- 卖出 {sell_qty} 股 @ {sell_price}元（约{sell_value}元）\n"
            f"- 卖出后仓位降至约{weight * (1 - sell_pct/100):.0f}%\n"
            f"- 目标仓位: {target_weight}%（需减到约{target_qty}股）\n"
        )
    else:
        content += f"- {reason}\n"

    content += (
        f"\n**依据**: {reason}\n"
        f"{' | '.join(tech_details)}"
    )

    notifier.send(f"持仓集中度: {name}({weight:.0f}%)", content)
    logger.info(f"集中度预警: {name} 占{weight:.0f}%，建议{action_type}")


def _handle_sector_risk(risk):
    """板块仓位过重：分析板块内各股，建议减掉最弱的"""
    sector = risk['sector']
    weight = risk['weight']
    positions_in_sector = risk['positions']
    total_value = risk['total_value']

    if not notifier._can_send(f'sector_{sector}', 'concentration'):
        return

    # 分析板块内每只股票的技术面
    stock_analyses = []
    for pos in positions_in_sector:
        code = pos['code']
        current = md.get_realtime_quote(code)
        current_price = current.get('price', pos['buy_price']) if current else pos['buy_price']
        gain_pct = (current_price - pos['buy_price']) / pos['buy_price'] * 100 if pos['buy_price'] else 0

        # 技术面强度
        df = md.get_stock_history(code, days=60)
        if len(df) >= 30:
            indicators = md.get_technical_indicators(df)
            strength = 0
            if indicators.get('price_above_ma5'): strength += 1
            if indicators.get('price_above_ma20'): strength += 1
            if indicators.get('macd_hist', 0) > 0: strength += 1
            if indicators.get('rsi', 50) < 70: strength += 1
            trend = "偏强" if strength >= 3 else ("中性" if strength >= 2 else "偏弱")
        else:
            strength = 0
            trend = "数据不足"

        stock_analyses.append({
            'name': pos['name'],
            'code': code,
            'gain_pct': round(gain_pct, 2),
            'strength': strength,
            'trend': trend,
            'quantity': pos['quantity'],
            'current_price': current_price,
        })

    # 按强度排序，最弱的建议先减
    stock_analyses.sort(key=lambda x: (x['strength'], x['gain_pct']))

    content = (
        f"**{sector}板块仓位过重**\n"
        f"板块占比: {weight:.0f}%（上限40%）\n"
        f"包含 {len(positions_in_sector)} 只股票\n\n"
        f"**板块内各股对比**:\n"
    )

    weakest = stock_analyses[0]
    for s in stock_analyses:
        tag = " ← 建议先减这只" if s == weakest else ""
        content += f"- {s['name']}: 盈亏{s['gain_pct']:+.1f}% 技术面{s['trend']}{tag}\n"

    content += (
        f"\n**建议**: 优先减仓 **{weakest['name']}**（技术面最弱）\n"
        f"理由: 板块集中度过高时，保留强势股、减掉弱势股，降低板块风险的同时不丢强势收益"
    )

    notifier.send(f"板块集中度: {sector}({weight:.0f}%)", content)


def _check_positions():
    """全面持仓监控（跟踪止损 + 止盈 + 技术恶化 + 大盘暴跌）"""
    positions = strategy.get_local_positions()
    if not positions:
        return

    # 获取大盘概况
    overview = md.get_market_overview()
    crash_level = strategy.check_market_crash(overview)

    # 获取所有持仓当前价格
    current_prices = {}
    for pos in positions:
        quote = md.get_realtime_quote(pos['code'])
        if quote:
            current_prices[pos['code']] = quote['price']

    # 1. 跟踪止损检查
    for sl in strategy.check_stop_loss(positions, current_prices):
        logger.warning(f"止损: {sl['name']} {sl['change_pct']:+.1f}%")
        notifier.send_stop_loss_alert(
            sl['name'], sl['code'], sl['buy_price'],
            sl['current_price'], sl['change_pct'])

    # 2. 止盈提示
    for tp in strategy.check_take_profit(positions, current_prices):
        logger.info(f"止盈: {tp['name']} 盈利{tp['change_pct']:.1f}%")
        notifier.send_take_profit_alert(
            tp['name'], tp['code'], tp['buy_price'],
            tp['current_price'], tp['change_pct'])

    # 3. 技术面恶化检测
    for pos in positions:
        df = md.get_stock_history(pos['code'], days=60)
        if len(df) < 30:
            continue
        indicators = md.get_technical_indicators(df)
        det_signals = strategy.check_technical_deterioration(pos, indicators)
        for sig_type, severity, message in det_signals:
            current = current_prices.get(pos['code'], pos['buy_price'])
            gain_pct = (current - pos['buy_price']) / pos['buy_price'] * 100
            notifier.send_position_alert(pos, sig_type, severity, message, current, gain_pct)

    # 4. 集中度风险（带行情分析和操作建议）
    concentration_risks = strategy.check_concentration_risk(positions, current_prices)
    for risk in concentration_risks:
        if risk['type'] == 'single_position':
            _handle_single_position_risk(risk)
        elif risk['type'] == 'sector_concentration':
            _handle_sector_risk(risk)

    # 5. 大盘暴跌预警
    if crash_level in ('warning', 'crash'):
        notifier.send_market_alert(crash_level, overview, positions)


# ========== Agent 模式 ==========

AGENT_MODE = os.environ.get('AI_TRADER_AGENT_MODE', 'pipeline')  # 'pipeline' 或 'agent'


def analyze_and_trade_agent():
    """Agent 模式的完整分析（替代 analyze_and_trade）

    AI 自主决定：先检查大盘 → 再看持仓 → 有信号才分析个股 → 结论
    """
    from agent_tools import create_default_registry
    from agent_planner import AgentPlanner

    now = datetime.now()
    logger.info(f"{'='*40} {now.strftime('%H:%M')} [Agent] {'='*40}")

    registry = create_default_registry()
    planner = AgentPlanner(registry, max_steps=8)

    # 获取当前持仓和关注列表信息
    positions = strategy.get_local_positions()
    watchlist = strategy.get_watchlist()

    context = {
        "positions_count": len(positions),
        "positions": [{"code": p['code'], "name": p['name'], "buy_price": p['buy_price']}
                      for p in positions],
        "watchlist": [{"code": w['code'], "name": w.get('name', '')} for w in watchlist],
    }

    # 构建目标（包含当前时间信息）
    is_scan_time = now.minute < 5
    goal = (
        f"执行一轮市场分析（当前时间 {now.strftime('%H:%M')}）：\n"
        f"1. 检查大盘环境，如果环境很差可以提前结束\n"
        f"2. 检查当前 {len(positions)} 只持仓的风控状态\n"
        f"3. 分析关注列表中的股票是否有买入信号\n"
        f"{"4. 扫描市场发现新机会" if is_scan_time else ""}\n"
        f"5. 给出今日操作建议，包含具体价格和理由"
    )

    trace = planner.plan(goal=goal, context=context)

    logger.info(f"Agent 完成: {trace.total_tool_calls}次工具调用, "
                f"耗时{trace.end_time - trace.start_time:.1f}s")

    # 发送 Agent 结论
    if trace.final_answer:
        notifier.send("Agent 分析报告", trace.final_answer[:2000])
    else:
        logger.warning("Agent 未产生结论")


# ========== 调度 ==========

def is_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.strftime('%H:%M')
    return ('09:30' <= t <= '11:30') or ('13:00' <= t <= '15:00')


def is_morning_end():
    return datetime.now().strftime('%H:%M') == '11:30'


def is_afternoon_end():
    return '15:05' <= datetime.now().strftime('%H:%M') <= '15:10'


def _ensure_kb_running():
    """检查知识库是否在运行，没有就自动启动"""
    import subprocess
    try:
        import requests
        resp = requests.get('http://127.0.0.1:8766/', timeout=3)
        logger.info("知识库已在运行")
        return True
    except Exception:
        pass

    # 知识库没跑，启动它
    logger.info("知识库未运行，正在启动...")
    kb_dir = r'C:\Users\陈独秀\knowledge-base\backend'
    python = r'D:\Users\陈独秀\AppData\Local\Programs\Python\Python314\python.exe'
    try:
        subprocess.Popen(
            [python, '-m', 'uvicorn', 'app.main:app', '--port', '8766'],
            cwd=kb_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        time.sleep(5)
        logger.info("知识库已启动")
        return True
    except Exception as e:
        logger.error(f"知识库启动失败: {e}")
        return False


def main():
    logger.info("AI Trader v2 启动")

    # 自动启动知识库
    _ensure_kb_running()

    logger.info(f"  股票池: {len(stock_pool.get_stock_universe())} 只")
    logger.info(f"  关注列表: {len(strategy.get_watchlist())} 只")
    logger.info(f"  活跃预警: {len(price_alert.get_active_alerts())} 条")

    # 自动结算过期预测
    resolved = prediction_tracker.auto_resolve_stale_predictions()
    if resolved:
        logger.info(f"启动时自动结算 {resolved} 条过期预测")

    notifier.send("AI Trader 启动",
        f"监控系统启动\n股票池: {len(stock_pool.get_stock_universe())}只\n关注列表: {len(strategy.get_watchlist())}只")

    last_minute = None
    morning_reported = False
    daily_reported = False

    while True:
        try:
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')
            current_minute = now.strftime('%H:%M')

            # 周末：睡10分钟
            if now.weekday() >= 5:
                time.sleep(600)
                continue

            # 午休（11:30-13:00）：睡5分钟
            if '11:31' <= current_minute <= '12:59':
                if not morning_reported:
                    _send_summary("午间总结")
                    morning_reported = True
                time.sleep(300)
                continue

            # 盘前（<9:30）：睡2分钟
            if current_minute < '09:30':
                time.sleep(120)
                continue

            # 收盘后（>15:05）：睡到明天
            if current_minute > '15:10':
                if not daily_reported:
                    _send_summary("全天复盘")
                    daily_reported = True
                time.sleep(3600)  # 睡1小时然后退出循环（下次任务会重启）
                break

            # 交易时间：每5分钟分析
            if is_trading_time() and last_minute != current_minute and now.minute % 5 == 0:
                if AGENT_MODE == 'agent':
                    analyze_and_trade_agent()
                else:
                    analyze_and_trade()
                last_minute = current_minute

            # 新的一天重置
            if current_minute == '09:00':
                morning_reported = False
                daily_reported = False

            time.sleep(30)

        except Exception as e:
            logger.error(f"循环异常: {e}", exc_info=True)
            time.sleep(60)

    logger.info("监控结束，等待下次定时任务重启")


def _send_summary(title):
    """发送阶段总结 + 生成知识库笔记 + HTML报告"""
    trades = tlog.read_recent_trades(50)
    decisions = tlog.read_recent_ai_decisions(50)

    text = f"**{title}**\n\n"
    text += f"交易: {len(trades)} 笔\n"
    text += f"AI决策: {len(decisions)} 次\n\n"

    if trades:
        text += "**交易记录**\n"
        for t in trades[-10:]:
            status = '✓' if t.get('success') else '✗'
            text += f"{t.get('time','')[-8:]} {t['action']} {t['name']} {t['quantity']}股@{t['price']} {status}\n"

    if decisions:
        text += "\n**AI决策**\n"
        for d in decisions[-10:]:
            t = d.get('time', '')[-8:]
            name = d.get('name', '')
            rec = d.get('recommendation', '')
            conf = d.get('confidence', 0)
            text += f"{t} {name} → {rec}({conf:.0%})\n"

    # 全天复盘时额外操作
    if '复盘' in title:
        # 0. 自动结算过期预测
        try:
            resolved = prediction_tracker.auto_resolve_stale_predictions()
            if resolved:
                text += f"\n自动结算 {resolved} 条过期预测"
        except Exception as e:
            logger.error(f"预测结算失败: {e}")

        # 1. 保存到知识库
        try:
            kb_path = kb_integration.save_daily_note_to_kb()
            text += f"\n笔记已存入知识库"
        except Exception as e:
            logger.error(f"知识库写入失败: {e}")

        # 2. 生成 HTML 报告
        try:
            html_path = html_report.generate_daily_html()
            text += f"\nHTML报告: {html_path}"
        except Exception as e:
            logger.error(f"HTML报告生成失败: {e}")

    notifier.send(title, text)
    logger.info(f"已发送: {title}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("用户中断")
        notifier.send("AI Trader 停止", "监控系统已停止")
