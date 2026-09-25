"""构造微调训练数据
从历史数据中提取"输入→输出"对，用于LoRA微调Qwen

输入: 某天的5天趋势 + 技术信号
输出: 后续1天/1周/1月的实际结果（概率分布标签）
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import pandas as pd
import logging
logging.basicConfig(level=logging.WARNING)

# 股票池
STOCKS = {
    '600519': '贵州茅台', '300750': '宁德时代', '601318': '中国平安',
    '000858': '五粮液', '000001': '平安银行', '601166': '兴业银行',
    '600036': '招商银行', '002594': '比亚迪',
}


def get_trend_series(df, end_idx, days=5):
    """获取到某天为止的N天趋势序列"""
    start = max(0, end_idx - days + 1)
    slice_df = df.iloc[start:end_idx+1]
    return [round(float(row['close']), 2) for _, row in slice_df.iterrows()]


def build_sample(df, idx, stock_code, stock_name):
    """为某只股票在某个日期构造一条训练样本"""
    row = df.iloc[idx]
    date_str = str(row['date'].date())

    # 需要至少30天历史 + 20天后续
    if idx < 30 or idx >= len(df) - 20:
        return None

    # 1. 取历史数据计算指标
    historical = df.iloc[:idx+1]
    indicators = md.get_technical_indicators(historical)
    if not indicators:
        return None

    # 2. 提取信号
    rsi = indicators.get('rsi', 50)
    macd_hist = indicators.get('macd_hist', 0)
    macd_hist_prev = indicators.get('macd_hist_prev', 0)
    ma5 = indicators.get('ma5', 0)
    ma20 = indicators.get('ma20', 0)
    close = indicators.get('close', 0)
    vol_ratio = indicators.get('vol_ratio', 1)
    pct = indicators.get('pct_change', 0)

    # 处理nan值
    import math
    if math.isnan(macd_hist): macd_hist = 0
    if math.isnan(macd_hist_prev): macd_hist_prev = 0
    if math.isnan(rsi): rsi = 50
    if math.isnan(ma5) or ma5 == 0: ma5 = close
    if math.isnan(ma20) or ma20 == 0: ma20 = close
    if math.isnan(vol_ratio): vol_ratio = 1.0
    if math.isnan(pct): pct = 0

    signals = {
        'rsi_oversold': rsi < 30,
        'rsi_extreme_oversold': rsi < 25,
        'rsi_overbought': rsi > 70,
        'rsi_extreme_overbought': rsi > 75,
        'macd_golden_cross': indicators.get('macd_golden_cross', False),
        'macd_death_cross': indicators.get('macd_death_cross', False),
        'macd_green_shrinking': macd_hist < 0 and macd_hist > macd_hist_prev,
        'macd_red_expanding': macd_hist > 0 and macd_hist > macd_hist_prev,
        'macd_red_shrinking': macd_hist > 0 and macd_hist < macd_hist_prev,
        'price_below_both_ma': close < ma5 and close < ma20,
        'price_above_both_ma': close > ma5 and close > ma20,
        'ma5_above_ma20': ma5 > ma20,
        'volume_surge': vol_ratio > 2.0,
        'volume_surge_up': vol_ratio > 2.0 and pct > 0,
        'volume_surge_down': vol_ratio > 2.0 and pct < 0,
        'volume_shrink': vol_ratio < 0.7,
        'drop_2pct': pct < -2,
        'rise_2pct': pct > 2,
    }

    # 3. 5天趋势序列
    close_trend = [round(float(x), 2) for x in get_trend_series(df, idx, days=5)]

    # 计算RSI趋势（需要重新算每天的RSI）
    rsi_trend = []
    for i in range(max(0, idx-4), idx+1):
        h = df.iloc[:i+1]
        if len(h) >= 30:
            ind = md.get_technical_indicators(h)
            if ind:
                val = ind.get('rsi', 50)
                if not math.isnan(val):
                    rsi_trend.append(round(float(val), 1))
    if not rsi_trend:
        rsi_trend = [round(float(rsi), 1)]

    # 4. 后续实际结果
    future = df.iloc[idx+1:]
    start_price = row['close']

    def get_ret(days):
        if len(future) < days:
            return None
        return round((future.iloc[days-1]['close'] - start_price) / start_price * 100, 2)

    ret_1d = get_ret(1)
    ret_1w = get_ret(5)
    ret_1m = get_ret(20)

    if ret_1d is None or ret_1w is None or ret_1m is None:
        return None

    # 5. 转成概率分布标签
    def to_prob_distribution(ret):
        if ret > 3:
            return {'up': 0.85, 'flat': 0.10, 'down': 0.05}
        elif ret > 1:
            return {'up': 0.65, 'flat': 0.25, 'down': 0.10}
        elif ret > 0:
            return {'up': 0.50, 'flat': 0.35, 'down': 0.15}
        elif ret > -1:
            return {'up': 0.15, 'flat': 0.35, 'down': 0.50}
        elif ret > -3:
            return {'up': 0.10, 'flat': 0.25, 'down': 0.65}
        else:
            return {'up': 0.05, 'flat': 0.10, 'down': 0.85}

    # 6. 构造 Qwen 微调格式
    # 输入描述
    active_signals = [k for k, v in signals.items() if v]
    signal_desc = ', '.join(active_signals) if active_signals else '无明显信号'

    input_text = (
        f"股票: {stock_name}({stock_code})\n"
        f"日期: {date_str}\n"
        f"当前价格: {float(close):.2f}\n"
        f"RSI: {float(rsi):.1f}, RSI趋势: {rsi_trend}\n"
        f"MACD柱: {float(macd_hist):.4f}(前值{float(macd_hist_prev):.4f})\n"
        f"MA5: {float(ma5):.2f}, MA20: {float(ma20):.2f}\n"
        f"量比: {float(vol_ratio):.2f}, 当日涨跌: {float(pct):+.2f}%\n"
        f"5日收盘价趋势: {close_trend}\n"
        f"活跃信号: {signal_desc}"
    )

    output_data = {
        '1d': to_prob_distribution(ret_1d),
        '1w': to_prob_distribution(ret_1w),
        '1m': to_prob_distribution(ret_1m),
        'actual_1d': ret_1d,
        'actual_1w': ret_1w,
        'actual_1m': ret_1m,
    }

    # 转成Qwen chat格式
    system_msg = (
        "你是A股技术分析助手。根据股票的技术指标和近期走势，"
        "预测后续走势的概率分布。请严格按照JSON格式输出。"
    )

    user_msg = f"分析以下股票数据，预测后续走势:\n\n{input_text}"

    assistant_msg = json.dumps(output_data, ensure_ascii=False)

    return {
        'messages': [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': assistant_msg},
        ],
        # 原始数据（用于分析）
        'meta': {
            'stock': stock_code,
            'date': date_str,
            'signals': active_signals,
            'actual_1d': ret_1d,
            'actual_1w': ret_1w,
            'actual_1m': ret_1m,
        }
    }


def main():
    print("=" * 60)
    print("  Training Data Builder")
    print("=" * 60)

    all_samples = []

    for code, name in STOCKS.items():
        print(f"\nProcessing {name}({code})...")
        df = md.get_stock_history(code, days=1000)
        if df.empty or len(df) < 60:
            print(f"  Skipped: insufficient data")
            continue

        count = 0
        # 每隔3天取一个样本（避免过度重叠）
        for idx in range(30, len(df) - 20, 3):
            sample = build_sample(df, idx, code, name)
            if sample:
                all_samples.append(sample)
                count += 1

        print(f"  Generated {count} samples")

    print(f"\nTotal samples: {len(all_samples)}")

    # 划分训练集/验证集/测试集（按时间）
    # 排序：按日期
    all_samples.sort(key=lambda x: x['meta']['date'])

    n = len(all_samples)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)

    train = all_samples[:train_end]
    val = all_samples[train_end:val_end]
    test = all_samples[val_end:]

    print(f"Train: {len(train)} | Val: {len(val)} | Test: {len(test)}")

    # 保存
    data_dir = os.path.join(os.path.dirname(__file__), 'finetune_data')
    os.makedirs(data_dir, exist_ok=True)

    for split_name, split_data in [('train', train), ('val', val), ('test', test)]:
        path = os.path.join(data_dir, f'{split_name}.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            for sample in split_data:
                # 只保存messages部分（微调用）
                f.write(json.dumps(sample['messages'], ensure_ascii=False) + '\n')
        print(f"Saved {split_name}: {path} ({len(split_data)} samples)")

    # 保存元数据（用于分析）
    meta_path = os.path.join(data_dir, 'all_metadata.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump([s['meta'] for s in all_samples], f, ensure_ascii=False, indent=2)
    print(f"Saved metadata: {meta_path}")

    # 统计
    print(f"\n{'='*60}")
    print("Dataset Statistics")
    print(f"{'='*60}")

    total_signals = {}
    for s in all_samples:
        for sig in s['meta']['signals']:
            total_signals[sig] = total_signals.get(sig, 0) + 1

    print(f"\nSignal frequency:")
    for sig, count in sorted(total_signals.items(), key=lambda x: -x[1]):
        print(f"  {sig}: {count} ({count/len(all_samples)*100:.1f}%)")

    # 各周期结果分布
    for period in ['1d', '1w', '1m']:
        actuals = [s['meta'][f'actual_{period}'] for s in all_samples]
        up = sum(1 for a in actuals if a > 1)
        down = sum(1 for a in actuals if a < -1)
        flat = len(actuals) - up - down
        print(f"\n{period} outcome distribution:")
        print(f"  Up (>1%): {up} ({up/len(all_samples)*100:.1f}%)")
        print(f"  Flat:     {flat} ({flat/len(all_samples)*100:.1f}%)")
        print(f"  Down:     {down} ({down/len(all_samples)*100:.1f}%)")


if __name__ == "__main__":
    main()
