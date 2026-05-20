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

def check_stop_loss(positions, current_prices):
    """检查是否触发止损
    Args:
        positions: 持仓列表
        current_prices: {code: price} 字典
    Returns:
        list: 需要止损的持仓
    """
    stop_loss_list = []
    for pos in positions:
        code = pos['code']
        if code in current_prices:
            current = current_prices[code]
            buy_price = pos['buy_price']
            change_pct = (current - buy_price) / buy_price
            if change_pct <= -config.STOP_LOSS:
                stop_loss_list.append({
                    **pos,
                    'current_price': current,
                    'change_pct': round(change_pct * 100, 2),
                    'reason': f'触发止损线({-config.STOP_LOSS*100:.0f}%)',
                })
    return stop_loss_list


def check_take_profit(positions, current_prices):
    """检查是否触发止盈"""
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
                    'reason': f'触发止盈线({config.TAKE_PROFIT*100:.0f}%)',
                })
    return tp_list


def calculate_position_size(total_capital, current_positions_count):
    """计算单笔可买金额
    Args:
        total_capital: 总资金
        current_positions_count: 当前持仓数
    Returns:
        float: 可用于单笔买入的金额
    """
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

    # 判断
    if buy_signals >= 4:
        return 'buy'
    elif sell_signals >= 4:
        return 'sell'
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
