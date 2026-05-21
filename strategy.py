"""策略和风控模块
规则引擎 + AI确认，管理仓位和风险
"""
import json
import logging
import os
from datetime import datetime

import config
import market_data as md
import ai_analyzer

logger = logging.getLogger(__name__)

# 持仓记录文件
POSITIONS_FILE = os.path.join(config.DATA_DIR, 'positions.json')
TRADES_FILE = os.path.join(config.DATA_DIR, 'trades.json')
WATCHLIST_FILE = os.path.join(config.DATA_DIR, 'watchlist.json')


def _load_json(filepath, default=None):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default if default is not None else []


def _save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ========== 关注列表 ==========

def get_watchlist():
    """获取关注列表"""
    return _load_json(WATCHLIST_FILE, [])


def add_to_watchlist(stock_code, stock_name=""):
    """添加到关注列表"""
    wl = get_watchlist()
    if not any(s['code'] == stock_code for s in wl):
        wl.append({'code': stock_code, 'name': stock_name, 'added_at': str(datetime.now())})
        _save_json(WATCHLIST_FILE, wl)
        logger.info(f"添加关注: {stock_code} {stock_name}")


def remove_from_watchlist(stock_code):
    """从关注列表移除"""
    wl = get_watchlist()
    wl = [s for s in wl if s['code'] != stock_code]
    _save_json(WATCHLIST_FILE, wl)


# ========== 持仓管理 ==========

def get_local_positions():
    """获取本地记录的持仓"""
    return _load_json(POSITIONS_FILE, [])


def save_position(stock_code, stock_name, quantity, buy_price, buy_date=None):
    """记录一笔买入"""
    positions = get_local_positions()
    positions.append({
        'code': stock_code,
        'name': stock_name,
        'quantity': quantity,
        'buy_price': buy_price,
        'buy_date': buy_date or str(datetime.now()),
        'cost': round(quantity * buy_price, 2),
        'trailing_stop_price': round(buy_price * (1 - config.STOP_LOSS), 2),
        'highest_price_since_buy': buy_price,
    })
    _save_json(POSITIONS_FILE, positions)


def remove_position(stock_code, sell_price, sell_date=None):
    """记录卖出，移到历史"""
    positions = get_local_positions()
    remaining = []
    sold = []
    for p in positions:
        if p['code'] == stock_code:
            p['sell_price'] = sell_price
            p['sell_date'] = sell_date or str(datetime.now())
            p['profit'] = round((sell_price - p['buy_price']) * p['quantity'], 2)
            p['profit_pct'] = round((sell_price - p['buy_price']) / p['buy_price'] * 100, 2)
            sold.append(p)
        else:
            remaining.append(p)
    _save_json(POSITIONS_FILE, remaining)

    # 保存到交易历史
    trades = _load_json(TRADES_FILE, [])
    trades.extend(sold)
    _save_json(TRADES_FILE, trades)
    return sold


# ========== 风控检查 ==========

# 板块映射（和 stock_pool.py 的 HS300_CORE 对应）
SECTOR_MAP = {
    '600519': '白酒', '000858': '白酒', '002304': '白酒', '000568': '白酒',
    '600887': '消费', '603288': '消费',
    '601318': '金融', '600036': '金融', '601166': '金融', '000001': '金融',
    '601398': '金融', '601288': '金融', '600030': '金融', '601688': '金融',
    '300750': '新能源', '601012': '新能源', '002459': '新能源', '300274': '新能源',
    '600276': '医药', '300760': '医药', '000538': '医药', '002007': '医药',
    '600048': '地产', '001979': '地产',
    '601857': '能源', '600028': '能源', '601088': '能源',
    '002594': '汽车', '600104': '汽车', '600741': '汽车',
    '300059': '互联网', '600050': '通信', '002230': 'AI',
    '603501': '芯片', '002371': '芯片', '002049': '芯片',
    '002415': '安防', '002241': '声学',
    '601888': '消费', '000002': '地产',
}


def update_trailing_stop(position, current_price):
    """更新跟踪止损价

    规则：每涨5%，止损线跟着上移5%
    涨幅 0-5%:   止损=成本-5%（原止损）
    涨幅 5-10%:  止损=成本价（保本）
    涨幅 10-15%: 止损=成本+5%
    涨幅 15-20%: 止损=成本+10%
    """
    buy_price = position['buy_price']
    gain_pct = (current_price - buy_price) / buy_price

    if gain_pct <= 0:
        new_stop = round(buy_price * (1 - config.STOP_LOSS), 2)
    else:
        completed_steps = int(gain_pct / config.TRAILING_GAIN_STEP)
        new_stop = round(
            buy_price * (1 - config.STOP_LOSS)
            + completed_steps * config.TRAILING_GAIN_STEP * buy_price,
            2,
        )

    # 只上移，不下移
    current_stop = position.get('trailing_stop_price', round(buy_price * 0.95, 2))
    new_stop = max(new_stop, current_stop)

    # 记录最高价
    highest = max(position.get('highest_price_since_buy', buy_price), current_price)

    return new_stop, highest


def check_stop_loss(positions, current_prices):
    """跟踪止损检查（替代固定止损）"""
    stop_loss_list = []
    positions_updated = False

    for pos in positions:
        code = pos['code']
        if code not in current_prices:
            continue

        current = current_prices[code]
        buy_price = pos['buy_price']

        # 更新跟踪止损
        new_stop, new_highest = update_trailing_stop(pos, current)

        if new_stop != pos.get('trailing_stop_price') or new_highest != pos.get('highest_price_since_buy'):
            pos['trailing_stop_price'] = new_stop
            pos['highest_price_since_buy'] = new_highest
            positions_updated = True

        # 检查是否跌破跟踪止损价
        if current <= new_stop:
            change_pct = round((current - buy_price) / buy_price * 100, 2)
            stop_loss_list.append({
                **pos,
                'current_price': current,
                'change_pct': change_pct,
                'reason': f'触发追踪止损(止损价{new_stop}, 盈亏{change_pct:+.1f}%)',
            })

    if positions_updated:
        _save_json(POSITIONS_FILE, positions)

    return stop_loss_list


def check_take_profit(positions, current_prices):
    """止盈提示（跟踪止损是主退出机制，止盈作为辅助提示）"""
    tp_list = []
    for pos in positions:
        code = pos['code']
        if code in current_prices:
            current = current_prices[code]
            buy_price = pos['buy_price']
            change_pct = (current - buy_price) / buy_price
            if change_pct >= config.TAKE_PROFIT:
                tp_list.append({
                    **pos,
                    'current_price': current,
                    'change_pct': round(change_pct * 100, 2),
                    'reason': f'达到止盈线({config.TAKE_PROFIT*100:.0f}%)，建议考虑部分减仓锁定利润',
                })
    return tp_list


def check_technical_deterioration(position, indicators):
    """检查持仓股技术面恶化

    Returns: [(signal_type, severity, message), ...]
    """
    signals = []
    close = indicators.get('close', 0)
    buy_price = position['buy_price']
    gain_pct = (close - buy_price) / buy_price * 100 if buy_price else 0

    # MACD死叉
    if indicators.get('macd_death_cross'):
        if gain_pct > 0:
            action = '建议减仓30%-50%锁定利润'
        elif gain_pct > -3:
            action = '密切关注，跌破止损价立即卖出'
        else:
            action = '准备执行止损'
        signals.append(('macd_death_cross', '中',
                        f'MACD死叉出现，上涨动能衰竭。{action}'))

    # 放量跌破MA20
    ma20 = indicators.get('ma20', 0)
    if (not indicators.get('price_above_ma20')
            and indicators.get('volume_surge')
            and indicators.get('pct_change', 0) < -1):
        if gain_pct > 10:
            action = '建议减仓50%，保留底仓等反弹'
        elif gain_pct > 3:
            action = '建议减仓30%，跌破止损价全部卖出'
        else:
            action = '已接近止损线，准备执行止损'
        signals.append(('break_ma20_volume', '高',
                        f'放量跌破MA20({ma20})，趋势可能反转。{action}'))

    # 价格在MA5+MA20下方
    if not indicators.get('price_above_ma5') and not indicators.get('price_above_ma20'):
        if gain_pct > 5:
            action = '可减仓30%，剩余持仓设好止损'
        else:
            action = '趋势偏弱，密切关注止损线'
        signals.append(('below_both_ma', '中',
                        f'价格在MA5和MA20下方，趋势偏弱。{action}'))

    return signals


def check_market_crash(overview):
    """大盘暴跌检测

    Returns: 'normal' / 'caution' / 'warning' / 'crash'
    """
    if not overview:
        return 'normal'

    sh = overview.get('上证指数', {}).get('change_pct', 0)
    sz = overview.get('深证成指', {}).get('change_pct', 0)
    cy = overview.get('创业板指', {}).get('change_pct', 0)

    down_count = sum(1 for x in [sh, sz, cy] if x < -2)

    if sh < -4 or all(x < -3 for x in [sh, sz, cy] if x != 0):
        return 'crash'
    if sh < -3 or down_count >= 2:
        return 'warning'
    if any(x < -2 for x in [sh, sz, cy]):
        return 'caution'
    return 'normal'


def check_concentration_risk(positions, current_prices):
    """持仓集中度检查

    Returns: [warning_message, ...]
    """
    warnings = []
    if not positions:
        return warnings

    # 计算各持仓市值
    total_value = 0
    position_values = {}
    for pos in positions:
        code = pos['code']
        price = current_prices.get(code, pos['buy_price'])
        value = price * pos['quantity']
        position_values[code] = value
        total_value += value

    if total_value <= 0:
        return warnings

    # 单只占比 > 20%
    for pos in positions:
        code = pos['code']
        weight = position_values[code] / total_value * 100
        if weight > 20:
            warnings.append(f"{pos['name']}占总仓位{weight:.0f}%，超过20%上限，建议减仓")

    # 同板块占比 > 40%
    sector_values = {}
    for pos in positions:
        sector = SECTOR_MAP.get(pos['code'], '其他')
        sector_values[sector] = sector_values.get(sector, 0) + position_values.get(pos['code'], 0)

    for sector, value in sector_values.items():
        weight = value / total_value * 100
        if weight > 40:
            warnings.append(f"{sector}板块占总仓位{weight:.0f}%，超过40%上限，建议分散到其他板块")

    return warnings


def calculate_position_size(total_capital, current_positions_count):
    """计算单笔可买金额"""
    if current_positions_count >= config.MAX_POSITIONS:
        logger.warning(f"已达最大持仓数({config.MAX_POSITIONS})，不能再买入")
        return 0
    return total_capital * config.POSITION_RATIO


def calculate_quantity(price, available_amount):
    """根据价格和可用金额计算可买股数（100的整数倍）"""
    if price <= 0:
        return 0
    max_shares = int(available_amount / price)
    return (max_shares // 100) * 100


# ========== 规则引擎 ==========

def rule_signal(indicators):
    """基于技术指标的规则信号
    Returns:
        str: 'buy' / 'sell' / 'hold' / None
    """
    if not indicators:
        return None

    buy_signals = 0
    sell_signals = 0

    # MACD 金叉
    if indicators.get('macd_golden_cross'):
        buy_signals += 2
    if indicators.get('macd_death_cross'):
        sell_signals += 2

    # MACD 柱状图趋势
    if indicators.get('macd_hist', 0) > 0 and indicators.get('macd_hist', 0) > indicators.get('macd_hist_prev', 0):
        buy_signals += 1
    elif indicators.get('macd_hist', 0) < 0 and indicators.get('macd_hist', 0) < indicators.get('macd_hist_prev', 0):
        sell_signals += 1

    # RSI
    if indicators.get('rsi_oversold'):
        buy_signals += 2
    if indicators.get('rsi_overbought'):
        sell_signals += 2

    # 均线金叉
    if indicators.get('ma_golden_cross'):
        buy_signals += 2

    # 价格在均线上方
    if indicators.get('price_above_ma5') and indicators.get('price_above_ma20'):
        buy_signals += 1
    elif not indicators.get('price_above_ma5') and not indicators.get('price_above_ma20'):
        sell_signals += 1

    # 放量
    if indicators.get('volume_surge') and indicators.get('pct_change', 0) > 0:
        buy_signals += 1

    # MACD绿柱缩短（下跌动能减弱，即使还在负值区域）
    if indicators.get('macd_hist', 0) < 0 and indicators.get('macd_hist', 0) > indicators.get('macd_hist_prev', 0):
        buy_signals += 1

    # RSI从超卖区回升
    rsi = indicators.get('rsi', 50)
    if 30 < rsi < 45:
        buy_signals += 1  # 刚从超卖出来

    # 判断（降低门槛：3个信号就值得AI分析）
    # 买卖互斥：两边都够门槛时，差值>2才确认
    if buy_signals >= 3 and sell_signals >= 3:
        if buy_signals > sell_signals + 2:
            return 'buy'
        elif sell_signals > buy_signals + 2:
            return 'sell'
        else:
            return 'hold'  # 信号冲突，观望
    elif buy_signals >= 3:
        return 'buy'
    elif sell_signals >= 3:
        return 'sell'
    elif buy_signals >= 2:
        return 'watch'  # 有趣但不够，让AI看看
    return 'hold'


# ========== 综合分析 ==========

def full_analysis(stock_code, use_ai=True):
    """对一只股票做完整分析
    Args:
        stock_code: 股票代码
        use_ai: 是否调用 AI 分析
    Returns:
        dict: 分析结果
    """
    # 获取数据
    df = md.get_stock_history(stock_code, days=120)
    if len(df) < 30:
        return {"error": f"{stock_code} 数据不足，跳过分析"}

    indicators = md.get_technical_indicators(df)
    realtime = md.get_realtime_quote(stock_code)
    market = md.get_market_overview()

    # 规则信号
    rule = rule_signal(indicators)

    result = {
        'code': stock_code,
        'name': realtime.get('name', ''),
        'price': realtime.get('price', 0),
        'indicators': indicators,
        'rule_signal': rule,
        'timestamp': str(datetime.now()),
    }

    # AI 分析
    if use_ai:
        ai_result = ai_analyzer.analyze_stock(
            stock_info=realtime,
            indicators=indicators,
            market_overview=market,
        )
        result['ai_analysis'] = ai_result

    return result


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    print("=== 测试完整分析 ===")
    result = full_analysis('600519', use_ai=True)
    print(f"股票: {result.get('name')} ({result.get('code')})")
    print(f"价格: {result.get('price')}")
    print(f"规则信号: {result.get('rule_signal')}")
    if 'ai_analysis' in result:
        ai = result['ai_analysis']
        print(f"AI建议: {ai.get('recommendation')} (置信度{ai.get('confidence')})")
        print(f"分析: {ai.get('reasoning', '')[:200]}")
