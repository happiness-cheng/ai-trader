"""全面回测系统
多股票 × 多时间周期 × 多信号类别 → 统计准确率 → 生成对比图
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import pandas as pd
import logging
logging.basicConfig(level=logging.WARNING)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 中文字体
for font_path in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf',
                  r'C:\Windows\Fonts\simsun.ttc']:
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        plt.rcParams['font.sans-serif'] = [fm.FontProperties(fname=font_path).get_name()]
        break
plt.rcParams['axes.unicode_minus'] = False


# ========== 股票池 ==========
STOCKS = {
    '600519': '贵州茅台',
    '300750': '宁德时代',
    '601318': '中国平安',
    '000858': '五粮液',
    '000001': '平安银行',
    '601166': '兴业银行',
    '600036': '招商银行',
    '002594': '比亚迪',
}

# ========== 时间周期 ==========
PERIODS = {
    '1M': 20,      # 1个月 ≈ 20个交易日
    '3M': 60,      # 3个月
    '6M': 120,     # 6个月
    '9M': 180,     # 9个月
    '1Y': 240,     # 1年
    '2Y': 480,     # 2年
    '3Y': 720,     # 3年
}


def extract_all_signals(indicators):
    """提取所有信号（扩展版）"""
    s = {}
    rsi = indicators.get('rsi', 50)
    macd_hist = indicators.get('macd_hist', 0)
    macd_hist_prev = indicators.get('macd_hist_prev', 0)
    ma5 = indicators.get('ma5', 0)
    ma20 = indicators.get('ma20', 0)
    close = indicators.get('close', 0)
    pct = indicators.get('pct_change', 0)
    vol_ratio = indicators.get('vol_ratio', 1)

    # RSI 信号
    s['RSI<30超卖'] = rsi < 30
    s['RSI<25极度超卖'] = rsi < 25
    s['RSI>70超买'] = rsi > 70
    s['RSI>75极度超买'] = rsi > 75
    s['RSI 30-40弱势回升'] = 30 <= rsi < 40

    # MACD 信号
    s['MACD金叉'] = indicators.get('macd_golden_cross', False)
    s['MACD死叉'] = indicators.get('macd_death_cross', False)
    s['MACD绿柱缩短'] = macd_hist < 0 and macd_hist > macd_hist_prev
    s['MACD红柱放大'] = macd_hist > 0 and macd_hist > macd_hist_prev
    s['MACD红柱缩短'] = macd_hist > 0 and macd_hist < macd_hist_prev
    s['MACD绿柱放大'] = macd_hist < 0 and macd_hist < macd_hist_prev
    s['MACD转正(柱>0)'] = macd_hist > 0
    s['MACD转负(柱<0)'] = macd_hist < 0

    # 均线信号
    s['价格在MA5上方'] = close > ma5
    s['价格在MA20上方'] = close > ma20
    s['价格在MA5+MA20上方'] = close > ma5 and close > ma20
    s['价格在MA5+MA20下方'] = close < ma5 and close < ma20
    s['MA5>MA20多头'] = ma5 > ma20
    s['MA5<MA20空头'] = ma5 < ma20
    s['均线金叉'] = indicators.get('ma_golden_cross', False)

    # 成交量信号
    s['放量(>2倍)'] = vol_ratio > 2.0
    s['放量上涨'] = vol_ratio > 2.0 and pct > 0
    s['放量下跌'] = vol_ratio > 2.0 and pct < 0
    s['缩量(<0.7倍)'] = vol_ratio < 0.7

    # 涨跌幅信号
    s['当日跌>2%'] = pct < -2
    s['当日涨>2%'] = pct > 2
    s['当日跌>3%'] = pct < -3
    s['当日涨>3%'] = pct > 3

    return s


def run_backtest(stock_code, stock_name, lookback_days):
    """对一只股票跑回测"""
    df = md.get_stock_history(stock_code, days=1000)
    if df.empty or len(df) < 60:
        return []

    # 需要至少lookback天的后续数据来验证
    cutoff = len(df) - lookback_days - 1
    if cutoff < 60:
        return []

    results = []

    for i in range(60, cutoff):
        row = df.iloc[i]
        date_str = str(row['date'].date())

        historical = df.iloc[:i+1]
        if len(historical) < 30:
            continue

        indicators = md.get_technical_indicators(historical)
        if not indicators:
            continue

        signals = extract_all_signals(indicators)

        # 后续收益
        future = df.iloc[i+1:]
        start_price = row['close']

        def get_ret(days):
            if len(future) < days:
                return None
            return round((future.iloc[days-1]['close'] - start_price) / start_price * 100, 2)

        ret = get_ret(lookback_days)
        if ret is None:
            continue

        results.append({
            'stock': stock_code,
            'date': date_str,
            'signals': signals,
            'return': ret,
            'direction': 'up' if ret > 1 else ('down' if ret < -1 else 'flat'),
        })

    return results


def analyze_all(all_results):
    """分析所有结果"""
    # 按信号统计
    signal_stats = {}

    for r in all_results:
        for sig_name, active in r['signals'].items():
            if sig_name not in signal_stats:
                signal_stats[sig_name] = {
                    'total': 0, 'correct': 0,
                    'up_when_active': 0, 'down_when_active': 0, 'flat_when_active': 0
                }

            if active:
                signal_stats[sig_name]['total'] += 1

                # 信号分类：看涨 or 看跌
                bullish_signals = ['RSI<30超卖', 'RSI<25极度超卖', 'RSI 30-40弱势回升',
                                   'MACD金叉', 'MACD绿柱缩短', 'MACD红柱放大',
                                   '价格在MA5上方', '价格在MA20上方', '价格在MA5+MA20上方',
                                   'MA5>MA20多头', '均线金叉',
                                   '放量上涨', '当日涨>2%', '当日涨>3%']
                is_bullish = sig_name in bullish_signals

                if r['direction'] == 'up':
                    signal_stats[sig_name]['up_when_active'] += 1
                    if is_bullish:
                        signal_stats[sig_name]['correct'] += 1
                elif r['direction'] == 'down':
                    signal_stats[sig_name]['down_when_active'] += 1
                    if not is_bullish:
                        signal_stats[sig_name]['correct'] += 1
                else:
                    signal_stats[sig_name]['flat_when_active'] += 1

    # 计算准确率
    for name, stats in signal_stats.items():
        if stats['total'] > 0:
            stats['accuracy'] = round(stats['correct'] / stats['total'] * 100, 1)
            stats['up_pct'] = round(stats['up_when_active'] / stats['total'] * 100, 1)
            stats['down_pct'] = round(stats['down_when_active'] / stats['total'] * 100, 1)
        else:
            stats['accuracy'] = 0
            stats['up_pct'] = 0
            stats['down_pct'] = 0

    return signal_stats


def generate_charts(period_results):
    """生成多图对比"""
    # 选择有足够数据的周期
    available_periods = [p for p, r in period_results.items() if len(r) > 50]

    if not available_periods:
        print("No sufficient data for charts")
        return

    # 收集每个周期的信号准确率
    period_accuracies = {}
    for period in available_periods:
        stats = analyze_all(period_results[period])
        period_accuracies[period] = stats

    # 选出出现次数>=5的信号
    all_signals = set()
    for period, stats in period_accuracies.items():
        for sig, data in stats.items():
            if data['total'] >= 5:
                all_signals.add(sig)

    signals_list = sorted(all_signals)

    # 图1: 各信号在不同周期的准确率热力图
    fig, axes = plt.subplots(2, 2, figsize=(20, 16))

    # 子图1: 准确率热力图
    ax = axes[0][0]
    heatmap_data = []
    for sig in signals_list:
        row = []
        for period in available_periods:
            acc = period_accuracies[period].get(sig, {}).get('accuracy', 50)
            row.append(acc)
        heatmap_data.append(row)

    im = ax.imshow(heatmap_data, cmap='RdYlGn', aspect='auto', vmin=30, vmax=80)
    ax.set_xticks(range(len(available_periods)))
    ax.set_xticklabels(available_periods)
    ax.set_yticks(range(len(signals_list)))
    ax.set_yticklabels(signals_list, fontsize=7)
    ax.set_title('Signal Accuracy by Period (%)', fontsize=12)
    plt.colorbar(im, ax=ax, shrink=0.8)

    # 标注数值
    for i, sig in enumerate(signals_list):
        for j, period in enumerate(available_periods):
            val = heatmap_data[i][j]
            ax.text(j, i, f'{val:.0f}', ha='center', va='center', fontsize=6,
                   color='white' if val < 40 or val > 70 else 'black')

    # 子图2: 最佳信号 TOP10（取3年数据的准确率）
    ax = axes[0][1]
    long_period = available_periods[-1]  # 最长周期
    long_stats = period_accuracies[long_period]
    top_signals = sorted(
        [(sig, data) for sig, data in long_stats.items() if data['total'] >= 10],
        key=lambda x: -x[1]['accuracy']
    )[:15]

    if top_signals:
        names = [s[0] for s in top_signals]
        accs = [s[1]['accuracy'] for s in top_signals]
        counts = [s[1]['total'] for s in top_signals]
        colors = ['#50C878' if a > 55 else '#FF6B6B' if a < 45 else '#FFD700' for a in accs]

        bars = ax.barh(range(len(names)), accs, color=colors)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=8)
        ax.set_xlabel('Accuracy %')
        ax.set_title(f'Top Signals ({long_period}, n>={10})', fontsize=12)
        ax.axvline(x=50, color='gray', linestyle='--', alpha=0.5)
        ax.set_xlim(20, 90)

        for bar, acc, count in zip(bars, accs, counts):
            ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2,
                   f'{acc:.0f}% (n={count})', va='center', fontsize=8)

    # 子图3: 各股票的信号准确率对比
    ax = axes[1][0]
    stock_names = list(STOCKS.values())
    stock_codes = list(STOCKS.keys())

    # 用最长周期的数据
    stock_signal_accs = {}
    for code in stock_codes:
        stock_results = [r for r in period_results[long_period] if r['stock'] == code]
        if stock_results:
            stats = analyze_all(stock_results)
            stock_signal_accs[code] = stats

    # 选几个关键信号做对比
    key_signals = ['RSI<30超卖', 'MACD绿柱缩短', 'MACD金叉', 'MACD死叉', '价格在MA5+MA20下方']
    key_signals = [s for s in key_signals if s in all_signals]

    if key_signals and stock_signal_accs:
        x = range(len(key_signals))
        width = 0.1
        for i, code in enumerate(stock_codes[:6]):  # 最多6只
            if code not in stock_signal_accs:
                continue
            accs = [stock_signal_accs[code].get(sig, {}).get('accuracy', 0) for sig in key_signals]
            ax.bar([xi + i * width for xi in x], accs, width,
                  label=STOCKS[code], alpha=0.8)

        ax.set_xticks([xi + width * 2.5 for xi in x])
        ax.set_xticklabels(key_signals, rotation=30, ha='right', fontsize=8)
        ax.set_ylabel('Accuracy %')
        ax.set_title('Key Signals Across Stocks', fontsize=12)
        ax.axhline(y=50, color='gray', linestyle='--', alpha=0.5)
        ax.legend(fontsize=7, loc='upper right')

    # 子图4: 信号出现时实际涨跌分布
    ax = axes[1][1]
    dist_signals = [s for s, data in long_stats.items()
                    if data['total'] >= 10 and s in ['RSI<30超卖', 'MACD绿柱缩短',
                    'MACD金叉', 'MACD死叉', '价格在MA5+MA20下方', '放量上涨', '放量下跌']]

    if dist_signals:
        up_pcts = [long_stats[s]['up_pct'] for s in dist_signals]
        down_pcts = [long_stats[s]['down_pct'] for s in dist_signals]

        x = range(len(dist_signals))
        ax.bar([xi - 0.2 for xi in x], up_pcts, 0.4, label='Up >1%', color='#50C878')
        ax.bar([xi + 0.2 for xi in x], down_pcts, 0.4, label='Down >1%', color='#FF6B6B')
        ax.set_xticks(x)
        ax.set_xticklabels(dist_signals, rotation=30, ha='right', fontsize=8)
        ax.set_ylabel('% of occurrences')
        ax.set_title(f'When Signal Active: Actual Outcome ({long_period})', fontsize=12)
        ax.legend()

    plt.suptitle('AI Trader Backtest - Multi-Stock Multi-Period Analysis', fontsize=14, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    chart_path = os.path.join(os.path.dirname(__file__), 'full_backtest_chart.png')
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Chart saved: {chart_path}")
    return chart_path


def print_summary_table(period_results):
    """打印汇总表"""
    print("\n" + "=" * 90)
    print("COMPREHENSIVE BACKTEST SUMMARY")
    print("=" * 90)

    available_periods = sorted(period_results.keys(), key=lambda p: PERIODS.get(p, 0))

    for period in available_periods:
        results = period_results[period]
        if len(results) < 10:
            continue

        stats = analyze_all(results)
        n_samples = len(results)
        n_stocks = len(set(r['stock'] for r in results))

        print(f"\n--- Period: {period} | Samples: {n_samples} | Stocks: {n_stocks} ---")
        print(f"{'Signal':<25} {'Count':>6} {'Accuracy':>9} {'Up%':>6} {'Down%':>6} {'Verdict':<10}")
        print("-" * 70)

        sorted_stats = sorted(
            [(sig, data) for sig, data in stats.items() if data['total'] >= 5],
            key=lambda x: -x[1]['accuracy']
        )

        for sig, data in sorted_stats:
            verdict = "STRONG" if data['accuracy'] > 60 else ("WEAK" if data['accuracy'] < 45 else "Moderate")
            print(f"{sig:<25} {data['total']:>6} {data['accuracy']:>8.1f}% {data['up_pct']:>5.1f}% "
                  f"{data['down_pct']:>5.1f}% {verdict:<10}")


# ========== Main ==========
if __name__ == "__main__":
    print("Starting comprehensive backtest...")
    print(f"Stocks: {len(STOCKS)}")
    print(f"Periods: {', '.join(PERIODS.keys())}")
    print()

    period_results = {}

    for period_name, lookback_days in PERIODS.items():
        print(f"[{period_name}] Running backtest (lookback={lookback_days} days)...")
        all_results = []

        for code, name in STOCKS.items():
            results = run_backtest(code, name, lookback_days)
            all_results.extend(results)

        period_results[period_name] = all_results
        print(f"  Total samples: {len(all_results)}")

    # 打印汇总表
    print_summary_table(period_results)

    # 生成图表
    print("\nGenerating charts...")
    chart_path = generate_charts(period_results)

    # 保存数据
    output_path = os.path.join(os.path.dirname(__file__), 'full_backtest_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(period_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"Data saved: {output_path}")
    print("\nDone!")
