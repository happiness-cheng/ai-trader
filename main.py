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

def analyze_and_trade():
    """完整一轮分析"""
    now = datetime.now()
    logger.info(f"{'='*40} {now.strftime('%H:%M')} {'='*40}")

    # 1. 大盘概况
    overview = md.get_market_overview()
    tlog.log_market(overview)

    # 2. 全市场扫描（每小时扫一次就够了）
    scan_candidates = []
    if now.minute < 5:  # 每小时的前5分钟做一次全市场扫描
        scan_candidates = stock_pool.scan_market(5)
        for c in scan_candidates:
            tlog.log_signal(c['code'], c['name'], 'scan',
                           f"扫描发现: {', '.join(c['signals'])} (得分{c['score']})")

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

        if rule_signal not in ('buy', 'sell'):
            logger.info(f"  {name}: {rule_signal} ({signal_detail})")
            continue

        # 有信号 → 调AI
        logger.info(f"  {name}: {rule_signal} ({signal_detail}) → 调AI确认")
        realtime = md.get_realtime_quote(code)
        ai_result = ai_analyzer.analyze_stock(
            stock_info=realtime,
            indicators=indicators,
            market_overview=overview,
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

        # 根据AI决策执行
        if rec == '买入' and conf >= 0.6:
            buy_price, order_type = calculate_buy_price(
                indicators['close'], indicators, 'auto')
            _execute_buy(code, name, buy_price, order_type, reasoning, conf)

            # 设置条件预警
            if stop_loss:
                price_alert.add_alert(code, name, stop_loss, 'below', f'止损线: 亏损约5%')
            if take_profit:
                price_alert.add_alert(code, name, take_profit, 'above', f'止盈线: 盈利约15%')

        elif rec == '卖出' and conf >= 0.6:
            _execute_sell(code, name, indicators['close'], reasoning, conf)

        elif rec == '观望' and signals:
            # 有信号但AI建议观望 → 设条件预警
            if 'RSI超卖' in signal_detail:
                # AI说等突破MA5再入场
                ma5 = indicators.get('ma5', 0)
                if ma5:
                    price_alert.add_alert(code, name, ma5, 'above',
                        f'AI建议: RSI超卖但等突破MA5({ma5})再入场')
                    notifier.send(f"条件预警已设: {name}",
                        f"{name}({code}) RSI超卖\nAI建议等突破MA5({ma5})再入场\n已设价格预警，突破时通知你")

        else:
            logger.info(f"  {name}: AI{rec}(置信度{conf:.0%})，不操作")

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


def _check_positions():
    """检查持仓止损止盈"""
    positions = strategy.get_local_positions()
    if not positions:
        return

    current_prices = {}
    for pos in positions:
        quote = md.get_realtime_quote(pos['code'])
        if quote:
            current_prices[pos['code']] = quote['price']

    for sl in strategy.check_stop_loss(positions, current_prices):
        logger.warning(f"止损: {sl['name']} 亏损{sl['change_pct']}%")
        _execute_sell(sl['code'], sl['name'], sl['current_price'],
                      f"止损触发: 亏损{sl['change_pct']}%", 1.0)

    for tp in strategy.check_take_profit(positions, current_prices):
        logger.info(f"止盈: {tp['name']} 盈利{tp['change_pct']}%")
        _execute_sell(tp['code'], tp['name'], tp['current_price'],
                      f"止盈触发: 盈利{tp['change_pct']}%", 1.0)


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


def main():
    logger.info("AI Trader v2 启动")
    logger.info(f"  股票池: {len(stock_pool.get_stock_universe())} 只")
    logger.info(f"  关注列表: {len(strategy.get_watchlist())} 只")
    logger.info(f"  活跃预警: {len(price_alert.get_active_alerts())} 条")

    notifier.send("AI Trader 启动",
        f"监控系统启动\n股票池: {len(stock_pool.get_stock_universe())}只\n关注列表: {len(strategy.get_watchlist())}只")

    last_minute = None
    morning_reported = False
    daily_reported = False

    while True:
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        current_minute = now.strftime('%H:%M')

        if now.weekday() >= 5:
            time.sleep(600)
            continue

        # 交易时间：每5分钟分析
        if is_trading_time() and last_minute != current_minute and now.minute % 5 == 0:
            try:
                analyze_and_trade()
            except Exception as e:
                logger.error(f"分析异常: {e}", exc_info=True)
            last_minute = current_minute
            morning_reported = False  # 重置

        # 午间总结
        if is_morning_end() and not morning_reported:
            _send_summary("午间总结")
            morning_reported = True

        # 收盘日报
        if is_afternoon_end() and not daily_reported:
            _send_summary("全天复盘")
            daily_reported = True

        # 新的一天重置
        if current_minute == '09:00':
            morning_reported = False
            daily_reported = False

        time.sleep(30)


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
