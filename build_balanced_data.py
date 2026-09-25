"""构造均衡训练数据（解决mode collapse）
核心改动：
1. 对每个方向（涨/跌/平）采样相等数量
2. 增加更多特征让模型能区分不同情况
3. 去掉actual_字段（不让模型抄答案）
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import pandas as pd
import random
import logging
import math
logging.basicConfig(level=logging.WARNING)

STOCKS = {
    '600519': '贵州茅台', '300750': '宁德时代', '601318': '中国平安',
    '000858': '五粮液', '000001': '平安银行', '601166': '兴业银行',
    '600036': '招商银行', '002594': '比亚迪',
}


def safe_float(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    return round(float(v), 4)


def build_sample(df, idx, stock_code, stock_name):
    row = df.iloc[idx]
    date_str = str(row['date'].date())
    if idx < 30 or idx >= len(df) - 20:
        return None

    historical = df.iloc[:idx+1]
    indicators = md.get_technical_indicators(historical)
    if not indicators:
        return None

    rsi = safe_float(indicators.get('rsi'))
    macd_hist = safe_float(indicators.get('macd_hist'))
    macd_hist_prev = safe_float(indicators.get('macd_hist_prev'))
    ma5 = safe_float(indicators.get('ma5'))
    ma20 = safe_float(indicators.get('ma20'))
    close = safe_float(indicators.get('close'))
    vol_ratio = safe_float(indicators.get('vol_ratio'))
    pct = safe_float(indicators.get('pct_change'))

    # 5天涨跌序列
    start_i = max(0, idx - 4)
    recent_closes = [safe_float(df.iloc[i]['close']) for i in range(start_i, idx + 1)]
    recent_changes = []
    for i in range(1, len(recent_closes)):
        if recent_closes[i-1] > 0:
            recent_changes.append(round((recent_closes[i] - recent_closes[i-1]) / recent_closes[i-1] * 100, 2))
    if not recent_changes:
        recent_changes = [0.0]

    # 后续结果
    future = df.iloc[idx + 1:]
    start_price = row['close']

    def get_ret(days):
        if len(future) < days:
            return None
        return round((future.iloc[days - 1]['close'] - start_price) / start_price * 100, 2)

    ret_1d = get_ret(1)
    ret_1w = get_ret(5)
    if ret_1d is None or ret_1w is None:
        return None

    # 分类标签（用三分类而非概率分布，更容易学）
    def classify(ret):
        if ret > 1.5:
            return "上涨"
        elif ret < -1.5:
            return "下跌"
        else:
            return "横盘"

    label_1d = classify(ret_1d)
    label_1w = classify(ret_1w)

    # 构造更丰富的输入
    input_text = (
        f"股票: {stock_name}\n"
        f"当前价: {close:.2f}\n"
        f"RSI: {rsi:.1f}\n"
        f"MACD柱: {macd_hist:.4f}(前值{macd_hist_prev:.4f})\n"
        f"MA5: {ma5:.2f}, MA20: {ma20:.2f}\n"
        f"量比: {vol_ratio:.2f}\n"
        f"当日涨跌: {pct:+.2f}%\n"
        f"5日涨跌: {recent_changes}\n"
    )

    # 输出简化为三分类
    output_text = json.dumps({
        "1d": label_1d,
        "1w": label_1w,
    }, ensure_ascii=False)

    system_msg = "你是A股技术分析助手。根据技术指标判断后续1天和1周的走势方向。只输出JSON。"

    return {
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"分析以下数据，预测后续走势:\n\n{input_text}"},
            {"role": "assistant", "content": output_text},
        ],
        "label_1d": label_1d,
        "label_1w": label_1w,
    }


def main():
    print("=" * 60)
    print("  Balanced Training Data Builder")
    print("=" * 60)

    all_samples = []
    for code, name in STOCKS.items():
        print(f"Processing {name}...")
        df = md.get_stock_history(code, days=1000)
        if df.empty or len(df) < 60:
            continue
        for idx in range(30, len(df) - 20, 3):
            sample = build_sample(df, idx, code, name)
            if sample:
                all_samples.append(sample)

    print(f"\nTotal raw samples: {len(all_samples)}")

    # 按1w标签分组
    up_samples = [s for s in all_samples if s['label_1w'] == '上涨']
    down_samples = [s for s in all_samples if s['label_1w'] == '下跌']
    flat_samples = [s for s in all_samples if s['label_1w'] == '横盘']

    print(f"上涨: {len(up_samples)}, 下跌: {len(down_samples)}, 横盘: {len(flat_samples)}")

    # 均衡采样：每个方向取 min(count) 个
    min_count = min(len(up_samples), len(down_samples), len(flat_samples))
    min_count = max(min_count, 50)  # 至少50个

    random.seed(42)
    balanced = (
        random.sample(up_samples, min(len(up_samples), min_count)) +
        random.sample(down_samples, min(len(down_samples), min_count)) +
        random.sample(flat_samples, min(len(flat_samples), min_count))
    )
    random.shuffle(balanced)

    print(f"\nBalanced samples: {len(balanced)} (each class: ~{min_count})")

    # 划分
    n = len(balanced)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)
    train = balanced[:train_end]
    val = balanced[train_end:val_end]
    test = balanced[val_end:]

    print(f"Train: {len(train)}, Val: {len(val)}, Test: {len(test)}")

    # 保存
    data_dir = os.path.join(os.path.dirname(__file__), 'finetune_data_v2')
    os.makedirs(data_dir, exist_ok=True)

    for split_name, split_data in [('train', train), ('val', val), ('test', test)]:
        path = os.path.join(data_dir, f'{split_name}.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            for sample in split_data:
                f.write(json.dumps(sample['messages'], ensure_ascii=False) + '\n')
        print(f"Saved: {path}")

    # 验证均衡性
    print(f"\nTrain distribution:")
    for label in ['上涨', '下跌', '横盘']:
        count = sum(1 for s in train if s['label_1w'] == label)
        print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
