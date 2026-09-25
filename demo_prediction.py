"""演示：完整预测流程
1. 选一个历史日期
2. 取当天+前4天数据（趋势）
3. 提取信号
4. 查1周后实际结果
5. 归因分析：哪些因素对了、哪些错了
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import strategy

def get_daily_data(code, end_date_str, days=5):
    """获取到end_date为止的最近N天日线数据"""
    df = md.get_stock_history(code, days=200)  # 取足够的历史数据
    if df.empty:
        return []

    # 筛选到end_date为止的数据
    import pandas as pd
    end_date = pd.to_datetime(end_date_str)
    df = df[df['date'] <= end_date].tail(days)

    result = []
    for _, row in df.iterrows():
        result.append({
            'date': str(row['date'].date()),
            'close': round(row['close'], 2),
            'high': round(row['high'], 2),
            'low': round(row['low'], 2),
            'volume': row['volume'],
            'pct_change': round(row.get('pct_change', 0), 2),
        })
    return result


def get_indicators_at(code, date_str):
    """获取到某天为止的技术指标"""
    df = md.get_stock_history(code, days=300)
    if df.empty:
        return {}

    import pandas as pd
    end_date = pd.to_datetime(date_str)
    df = df[df['date'] <= end_date]
    if len(df) < 30:
        return {}

    return md.get_technical_indicators(df)


def get_future_return(code, from_date_str, days_later):
    """获取若干天后的涨跌幅"""
    df = md.get_stock_history(code, days=300)
    if df.empty:
        return None

    import pandas as pd
    from_date = pd.to_datetime(from_date_str)
    future_date = from_date + pd.Timedelta(days=days_later + 5)  # 加buffer应对非交易日

    after = df[df['date'] >= from_date]
    if len(after) < 2:
        return None

    start_price = after.iloc[0]['close']

    # 找到目标日期附近的价格
    target = df[df['date'] >= future_date - pd.Timedelta(days=3)]
    if target.empty:
        # 用最后一行
        end_price = df.iloc[-1]['close']
    else:
        end_price = target.iloc[0]['close']

    return round((end_price - start_price) / start_price * 100, 2)


def extract_signals(indicators):
    """从技术指标中提取信号"""
    signals = {}

    # RSI
    rsi = indicators.get('rsi', 50)
    signals['RSI超卖'] = rsi < 30
    signals['RSI超买'] = rsi > 70
    signals['RSI值'] = round(rsi, 1)

    # MACD
    macd_hist = indicators.get('macd_hist', 0)
    macd_hist_prev = indicators.get('macd_hist_prev', 0)
    signals['MACD金叉'] = indicators.get('macd_golden_cross', False)
    signals['MACD死叉'] = indicators.get('macd_death_cross', False)
    signals['MACD绿柱缩短'] = macd_hist < 0 and macd_hist > macd_hist_prev
    signals['MACD红柱放大'] = macd_hist > 0 and macd_hist > macd_hist_prev
    signals['MACD柱值'] = round(macd_hist, 4)

    # 均线
    ma5 = indicators.get('ma5', 0)
    ma20 = indicators.get('ma20', 0)
    signals['价格在MA5上方'] = indicators.get('price_above_ma5', False)
    signals['价格在MA20上方'] = indicators.get('price_above_ma20', False)
    signals['均线金叉'] = indicators.get('ma_golden_cross', False)
    signals['MA5>MA20'] = ma5 > ma20 if ma5 and ma20 else False
    signals['MA5值'] = round(ma5, 2)
    signals['MA20值'] = round(ma20, 2)

    # 成交量
    signals['放量'] = indicators.get('volume_surge', False)
    signals['量比'] = round(indicators.get('vol_ratio', 1), 2)

    # 涨跌幅
    signals['当日涨跌'] = round(indicators.get('pct_change', 0), 2)

    return signals


def rule_based_prediction(signals):
    """规则引擎预测（不是AI，是确定性规则）"""
    bullish = 0
    bearish = 0
    reasons_bull = []
    reasons_bear = []

    if signals.get('RSI超卖'):
        bullish += 2
        reasons_bull.append(f"RSI={signals['RSI值']}超卖，反弹概率高")

    if signals.get('RSI超买'):
        bearish += 2
        reasons_bear.append(f"RSI={signals['RSI值']}超买，回调风险大")

    if signals.get('MACD金叉'):
        bullish += 2
        reasons_bull.append("MACD金叉，上涨动能出现")

    if signals.get('MACD死叉'):
        bearish += 2
        reasons_bear.append("MACD死叉，下跌动能出现")

    if signals.get('MACD绿柱缩短'):
        bullish += 1
        reasons_bull.append("MACD绿柱缩短，下跌动能衰减")

    if signals.get('MACD红柱放大'):
        bullish += 1
        reasons_bull.append("MACD红柱放大，上涨动能增强")

    if signals.get('价格在MA5上方') and signals.get('价格在MA20上方'):
        bullish += 1
        reasons_bull.append("价格在MA5和MA20上方，趋势向上")

    if not signals.get('价格在MA5上方') and not signals.get('价格在MA20上方'):
        bearish += 1
        reasons_bear.append("价格在MA5和MA20下方，趋势向下")

    if signals.get('均线金叉'):
        bullish += 2
        reasons_bull.append("MA5上穿MA20金叉")

    if signals.get('放量') and signals.get('当日涨跌', 0) > 0:
        bullish += 1
        reasons_bull.append(f"放量上涨(量比{signals['量比']})")

    if signals.get('放量') and signals.get('当日涨跌', 0) < 0:
        bearish += 1
        reasons_bear.append(f"放量下跌(量比{signals['量比']})")

    total = bullish + bearish if bullish + bearish > 0 else 1
    bull_pct = bullish / total
    bear_pct = bearish / total

    if bull_pct > 0.6:
        direction = "看涨"
    elif bear_pct > 0.6:
        direction = "看跌"
    else:
        direction = "中性/观望"

    return {
        'direction': direction,
        'bull_score': bullish,
        'bear_score': bearish,
        'bull_reasons': reasons_bull,
        'bear_reasons': reasons_bear,
    }


def attribution_analysis(signals, actual_return, prediction):
    """归因分析：哪些因素对了，哪些错了"""
    results = []

    # RSI超卖信号
    if signals.get('RSI超卖'):
        correct = actual_return > 0
        results.append({
            'factor': f'RSI超卖({signals["RSI值"]})',
            'predicted': '利好（应该涨）',
            'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
            'correct': correct,
            'verdict': '有效' if correct else '被其他因素压制',
        })

    # MACD金叉
    if signals.get('MACD金叉'):
        correct = actual_return > 0
        results.append({
            'factor': 'MACD金叉',
            'predicted': '利好',
            'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
            'correct': correct,
            'verdict': '有效' if correct else '假金叉/被压制',
        })

    # MACD绿柱缩短
    if signals.get('MACD绿柱缩短'):
        correct = actual_return > 0
        results.append({
            'factor': 'MACD绿柱缩短',
            'predicted': '下跌动能衰减，利好',
            'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
            'correct': correct,
            'verdict': '有效' if correct else '动能衰减但未反转',
        })

    # 趋势判断
    if not signals.get('价格在MA5上方') and not signals.get('价格在MA20上方'):
        correct = actual_return < 0
        results.append({
            'factor': '价格在均线下方(趋势向下)',
            'predicted': '利空',
            'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
            'correct': correct,
            'verdict': '有效' if correct else '趋势判断失误',
        })

    # 放量信号
    if signals.get('放量'):
        if signals.get('当日涨跌', 0) > 0:
            correct = actual_return > 0
            results.append({
                'factor': f'放量上涨(量比{signals["量比"]})',
                'predicted': '利好',
                'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
                'correct': correct,
                'verdict': '有效' if correct else '放量但未持续',
            })
        else:
            correct = actual_return < 0
            results.append({
                'factor': f'放量下跌(量比{signals["量比"]})',
                'predicted': '利空',
                'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
                'correct': correct,
                'verdict': '有效' if correct else '恐慌性抛售后反弹',
            })

    if not results:
        results.append({
            'factor': '无明显信号',
            'predicted': '中性',
            'actual': f'实际{"涨" if actual_return > 0 else "跌"}了{abs(actual_return)}%',
            'correct': None,
            'verdict': '无信号可验证',
        })

    return results


# ========== 主流程 ==========

def demo(stock_code, stock_name, date_str):
    """完整演示流程"""
    print(f"{'='*60}")
    print(f"  {stock_name}({stock_code}) - {date_str} 预测演示")
    print(f"{'='*60}")

    # 1. 获取当天及前4天数据
    print(f"\n[1] 获取{date_str}及前4天数据...")
    daily_data = get_daily_data(stock_code, date_str, days=5)
    if len(daily_data) < 3:
        print(f"  数据不足（只有{len(daily_data)}天），换一个日期")
        return

    print(f"  最近{len(daily_data)}天走势:")
    for d in daily_data:
        pct = d['pct_change']
        arrow = '^' if pct > 0 else ('v' if pct < 0 else '-')
        print(f"    {d['date']}: 收盘{d['close']}  {arrow}{pct:+.2f}%")

    # 2. 计算技术指标
    print(f"\n[2] 计算{date_str}的技术指标...")
    indicators = get_indicators_at(stock_code, date_str)
    if not indicators:
        print("  指标计算失败")
        return

    print(f"  RSI: {indicators.get('rsi', 0):.1f}")
    print(f"  MACD柱: {indicators.get('macd_hist', 0):.4f} (前值{indicators.get('macd_hist_prev', 0):.4f})")
    print(f"  MA5: {indicators.get('ma5', 0):.2f} / MA20: {indicators.get('ma20', 0):.2f}")
    print(f"  量比: {indicators.get('vol_ratio', 0):.2f}")

    # 3. 提取信号
    print(f"\n[3] 提取信号...")
    signals = extract_signals(indicators)
    active_signals = {k: v for k, v in signals.items() if v is True}
    print(f"  活跃信号: {list(active_signals.keys()) if active_signals else '无'}")

    # 4. 规则预测
    print(f"\n[4] 规则引擎预测...")
    prediction = rule_based_prediction(signals)
    print(f"  方向: {prediction['direction']}")
    print(f"  看涨得分: {prediction['bull_score']} | 看跌得分: {prediction['bear_score']}")
    if prediction['bull_reasons']:
        print(f"  看涨理由: {'; '.join(prediction['bull_reasons'])}")
    if prediction['bear_reasons']:
        print(f"  看跌理由: {'; '.join(prediction['bear_reasons'])}")

    # 5. 查实际结果
    print(f"\n[5] 查实际结果...")
    ret_1d = get_future_return(stock_code, date_str, 1)
    ret_3d = get_future_return(stock_code, date_str, 3)
    ret_1w = get_future_return(stock_code, date_str, 5)
    ret_2w = get_future_return(stock_code, date_str, 10)
    ret_1m = get_future_return(stock_code, date_str, 20)

    def format_result(ret, label):
        if ret is None:
            return f"  {label}: 数据不足"
        arrow = '^' if ret > 0 else ('v' if ret < 0 else '-')
        return f"  {label}: {arrow} {ret:+.2f}%"

    print(format_result(ret_1d, "+1天"))
    print(format_result(ret_3d, "+3天"))
    print(format_result(ret_1w, "+1周"))
    print(format_result(ret_2w, "+2周"))
    print(format_result(ret_1m, "+1月"))

    # 6. 归因分析
    print(f"\n[6] 归因分析（哪些因素对了/错了）...")
    if ret_1w is not None:
        attributions = attribution_analysis(signals, ret_1w, prediction)
        for a in attributions:
            status = '[OK]' if a['correct'] else ('[XX]' if a['correct'] is False else '[??]')
            print(f"  [{status}] {a['factor']}")
            print(f"       预测: {a['predicted']}")
            print(f"       实际: {a['actual']}")
            print(f"       结论: {a['verdict']}")
    else:
        print("  1周数据不足，无法归因")

    # 7. 总结
    print(f"\n{'='*60}")
    correct_count = sum(1 for a in attributions if a.get('correct') is True) if ret_1w else 0
    wrong_count = sum(1 for a in attributions if a.get('correct') is False) if ret_1w else 0
    print(f"  总结: {correct_count}个因素正确, {wrong_count}个因素错误")
    print(f"  这就是微调要学的：不是调权重，而是让模型看过4000个这样的案例后")
    print(f"  自动学会 RSI超卖+大盘暴跌=短期大概率跌 这种多因素组合模式")
    print(f"{'='*60}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    demo('600519', '贵州茅台', '2026-01-15')
