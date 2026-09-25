"""v3: 降阈值(0.5%) + 双周期输出(1周+1月)
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import pandas as pd
import random
import math
import logging
logging.basicConfig(level=logging.WARNING)

STOCKS = {
    '600519': '贵州茅台', '300750': '宁德时代', '601318': '中国平安',
    '000858': '五粮液', '000001': '平安银行', '601166': '兴业银行',
    '600036': '招商银行', '002594': '比亚迪',
    '601857': '中国石油', '600028': '中国石化',
    '600887': '伊利股份', '000568': '泸州老窖',
    '300274': '阳光电源', '601012': '隆基绿能',
    '002007': '华兰生物',
}

THRESHOLD = 0.5  # 0.5%就算涨/跌


def safe_float(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    return round(float(v), 4)


def classify(ret):
    if ret > THRESHOLD:
        return "上涨"
    elif ret < -THRESHOLD:
        return "下跌"
    else:
        return "横盘"


def build_sample(df, idx, stock_code, stock_name):
    row = df.iloc[idx]
    if idx < 30 or idx >= len(df) - 20:
        return None

    historical = df.iloc[:idx + 1]
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

    # 5日涨跌
    start_i = max(0, idx - 4)
    recent = [safe_float(df.iloc[i]['close']) for i in range(start_i, idx + 1)]
    changes = []
    for i in range(1, len(recent)):
        if recent[i - 1] > 0:
            changes.append(round((recent[i] - recent[i - 1]) / recent[i - 1] * 100, 2))
    if not changes:
        changes = [0.0]

    # 后续结果
    future = df.iloc[idx + 1:]
    start_price = row['close']

    def get_ret(days):
        if len(future) < days:
            return None
        return round((future.iloc[days - 1]['close'] - start_price) / start_price * 100, 2)

    ret_1w = get_ret(5)
    ret_1m = get_ret(20)
    if ret_1w is None or ret_1m is None:
        return None

    label_1w = classify(ret_1w)
    label_1m = classify(ret_1m)

    # RSI趋势斜率
    rsi_values = []
    for i in range(max(0, idx - 4), idx + 1):
        h = df.iloc[:i + 1]
        if len(h) >= 30:
            ind = md.get_technical_indicators(h)
            if ind:
                val = safe_float(ind.get('rsi'))
                if val > 0:
                    rsi_values.append(val)
    rsi_slope = round(rsi_values[-1] - rsi_values[0], 1) if len(rsi_values) >= 2 else 0.0

    # MACD柱变化速度
    macd_speed = round(macd_hist - macd_hist_prev, 4)

    input_text = (
        f"股票: {stock_name}\n"
        f"当前价: {close:.2f}\n"
        f"RSI: {rsi:.1f}(趋势斜率:{rsi_slope:+.1f})\n"
        f"MACD柱: {macd_hist:.4f}(变化:{macd_speed:+.4f})\n"
        f"MA5: {ma5:.2f}, MA20: {ma20:.2f}\n"
        f"量比: {vol_ratio:.2f}\n"
        f"当日涨跌: {pct:+.2f}%\n"
        f"5日涨跌序列: {changes}\n"
    )

    output_text = json.dumps({"1w": label_1w, "1m": label_1m}, ensure_ascii=False)

    system_msg = "你是A股技术分析助手。根据技术指标判断后续1周和1月的走势方向。只输出JSON。"

    return {
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"分析以下数据:\n\n{input_text}"},
            {"role": "assistant", "content": output_text},
        ],
        "label_1w": label_1w,
        "label_1m": label_1m,
    }


def main():
    print(f"v3 Data Builder (threshold={THRESHOLD}%)")
    print("=" * 50)

    all_samples = []
    for code, name in STOCKS.items():
        print(f"  {name}...", end=" ")
        df = md.get_stock_history(code, days=1000)
        if df.empty or len(df) < 60:
            print("skip")
            continue
        count = 0
        for idx in range(30, len(df) - 20, 3):
            sample = build_sample(df, idx, code, name)
            if sample:
                all_samples.append(sample)
                count += 1
        print(f"{count} samples")

    print(f"\nTotal: {len(all_samples)}")

    # 均衡1w标签
    by_label = {}
    for s in all_samples:
        l = s['label_1w']
        by_label.setdefault(l, []).append(s)

    for l, samples in by_label.items():
        print(f"  1w {l}: {len(samples)}")

    min_count = min(len(v) for v in by_label.values())
    random.seed(42)
    balanced = []
    for samples in by_label.values():
        balanced.extend(random.sample(samples, min(len(samples), min_count)))
    random.shuffle(balanced)

    print(f"\nBalanced: {len(balanced)} (each ~{min_count})")

    n = len(balanced)
    train, val, test = balanced[:int(n * .7)], balanced[int(n * .7):int(n * .85)], balanced[int(n * .85):]

    data_dir = os.path.join(os.path.dirname(__file__), 'finetune_data_v3')
    os.makedirs(data_dir, exist_ok=True)

    for name, data in [('train', train), ('val', val), ('test', test)]:
        path = os.path.join(data_dir, f'{name}.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            for s in data:
                f.write(json.dumps(s['messages'], ensure_ascii=False) + '\n')
        print(f"  {name}: {len(data)} -> {path}")

    print("\nDone!")


if __name__ == "__main__":
    main()
