"""v5: 回归预测（直接输出涨跌幅数字，不做分类）
解决mode collapse的根本方案：回归没有多数类可以作弊
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
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
    '002007': '华兰生物', '002415': '海康威视',
    '603501': '韦尔股份', '002230': '科大讯飞',
    '600741': '华域汽车', '000002': '万科A',
}


def safe_float(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    return round(float(v), 4)


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

    # 10日涨跌
    start_i = max(0, idx - 9)
    recent = [safe_float(df.iloc[i]['close']) for i in range(start_i, idx + 1)]
    changes = []
    for i in range(1, len(recent)):
        if recent[i - 1] > 0:
            changes.append(round((recent[i] - recent[i - 1]) / recent[i - 1] * 100, 2))
    if not changes:
        changes = [0.0]

    # RSI趋势斜率
    rsi_vals = []
    for i in range(max(0, idx - 9), idx + 1):
        h = df.iloc[:i + 1]
        if len(h) >= 30:
            ind = md.get_technical_indicators(h)
            if ind:
                v = safe_float(ind.get('rsi'))
                if v > 0:
                    rsi_vals.append(v)
    rsi_slope = round(rsi_vals[-1] - rsi_vals[0], 1) if len(rsi_vals) >= 2 else 0.0

    macd_speed = round(macd_hist - macd_hist_prev, 4)

    # 后续结果（直接用数字，不做分类）
    future = df.iloc[idx + 1:]
    start_price = row['close']

    def get_ret(days):
        if len(future) < days:
            return None
        return round((future.iloc[days - 1]['close'] - start_price) / start_price * 100, 2)

    ret_1d = get_ret(1)
    ret_1w = get_ret(5)
    ret_1m = get_ret(20)
    if ret_1d is None or ret_1w is None or ret_1m is None:
        return None

    # 限制在 -15% ~ +15% 范围内
    ret_1d = max(-15, min(15, ret_1d))
    ret_1w = max(-15, min(15, ret_1w))
    ret_1m = max(-15, min(15, ret_1m))

    input_text = (
        f"股票: {stock_name}\n"
        f"当前价: {close:.2f}\n"
        f"RSI: {rsi:.1f}(趋势:{rsi_slope:+.1f})\n"
        f"MACD柱: {macd_hist:.4f}(变化:{macd_speed:+.4f})\n"
        f"MA5: {ma5:.2f}, MA20: {ma20:.2f}\n"
        f"量比: {vol_ratio:.2f}\n"
        f"当日涨跌: {pct:+.2f}%\n"
        f"10日涨跌: {changes}\n"
    )

    # 输出是数字而不是分类
    output_text = json.dumps({
        "1d": ret_1d,
        "1w": ret_1w,
        "1m": ret_1m,
    }, ensure_ascii=False)

    system_msg = (
        "你是A股技术分析助手。根据技术指标预测后续涨跌幅(百分比)。"
        "只输出JSON如{\"1d\":-0.5,\"1w\":2.3,\"1m\":-1.8}"
    )

    return {
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"分析以下数据，预测后续涨跌幅:\n\n{input_text}"},
            {"role": "assistant", "content": output_text},
        ],
        "ret_1d": ret_1d,
        "ret_1w": ret_1w,
        "ret_1m": ret_1m,
    }


def main():
    print("v5 Regression Data Builder")
    print("=" * 50)

    all_samples = []
    for code, name in STOCKS.items():
        print(f"  {name}...", end=" ")
        df = md.get_stock_history(code, days=1000)
        if df.empty or len(df) < 60:
            print("skip")
            continue
        count = 0
        for idx in range(30, len(df) - 20, 2):  # 每2天取一次（更多数据）
            s = build_sample(df, idx, code, name)
            if s:
                all_samples.append(s)
                count += 1
        print(f"{count}")

    print(f"\nTotal: {len(all_samples)}")

    # 统计分布
    rets_1d = [s['ret_1d'] for s in all_samples]
    rets_1w = [s['ret_1w'] for s in all_samples]
    rets_1m = [s['ret_1m'] for s in all_samples]
    print(f"1d: mean={sum(rets_1d)/len(rets_1d):.2f}%, range=[{min(rets_1d):.1f}, {max(rets_1d):.1f}]")
    print(f"1w: mean={sum(rets_1w)/len(rets_1w):.2f}%, range=[{min(rets_1w):.1f}, {max(rets_1w):.1f}]")
    print(f"1m: mean={sum(rets_1m)/len(rets_1m):.2f}%, range=[{min(rets_1m):.1f}, {max(rets_1m):.1f}]")

    # 不做均衡采样（回归不需要）
    random.seed(42)
    random.shuffle(all_samples)

    n = len(all_samples)
    train, val, test = all_samples[:int(n * .7)], all_samples[int(n * .7):int(n * .85)], all_samples[int(n * .85):]
    print(f"Train: {len(train)}, Val: {len(val)}, Test: {len(test)}")

    data_dir = os.path.join(os.path.dirname(__file__), 'finetune_data_v5')
    os.makedirs(data_dir, exist_ok=True)

    for name, data in [('train', train), ('val', val), ('test', test)]:
        path = os.path.join(data_dir, f'{name}.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            for s in data:
                f.write(json.dumps(s['messages'], ensure_ascii=False) + '\n')
        print(f"  {name}: {len(data)}")


if __name__ == "__main__":
    main()
