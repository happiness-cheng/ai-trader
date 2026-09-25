"""批量回测 + 可视化
遍历多天数据，统计每个信号的成功率，生成对比图
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import pandas as pd
import logging
logging.basicConfig(level=logging.WARNING)


def batch_backtest(stock_code, stock_name, start_date, end_date):
    """批量回测：遍历日期区间，每天提取信号+查后续结果"""

    df = md.get_stock_history(stock_code, days=400)
    if df.empty:
        print("无法获取数据")
        return []

    # 筛选日期范围
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    trading_days = df[(df['date'] >= start) & (df['date'] <= end)]

    print(f"回测 {stock_name}({stock_code})")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"交易日数: {len(trading_days)}")
    print()

    results = []

    for _, row in trading_days.iterrows():
        date_str = str(row['date'].date())

        # 取到该天为止的数据计算指标
        historical = df[df['date'] <= row['date']]
        if len(historical) < 30:
            continue

        indicators = md.get_technical_indicators(historical)
        if not indicators:
            continue

        # 提取信号
        signals = {}
        rsi = indicators.get('rsi', 50)
        signals['RSI超卖'] = rsi < 30
        signals['RSI超买'] = rsi > 70

        macd_hist = indicators.get('macd_hist', 0)
        macd_hist_prev = indicators.get('macd_hist_prev', 0)
        signals['MACD金叉'] = indicators.get('macd_golden_cross', False)
        signals['MACD死叉'] = indicators.get('macd_death_cross', False)
        signals['MACD绿柱缩短'] = macd_hist < 0 and macd_hist > macd_hist_prev

        signals['价格均线下方'] = not indicators.get('price_above_ma5') and not indicators.get('price_above_ma20')
        signals['放量上涨'] = indicators.get('volume_surge', False) and indicators.get('pct_change', 0) > 0

        # 查后续结果
        future = df[df['date'] > row['date']]

        def get_return(days):
            if len(future) < days:
                return None
            start_price = row['close']
            end_price = future.iloc[days - 1]['close']
            return round((end_price - start_price) / start_price * 100, 2)

        ret_1d = get_return(1)
        ret_3d = get_return(3)
        ret_1w = get_return(5)
        ret_1m = get_return(20)

        # 规则打分
        bull_score = 0
        bear_score = 0
        if signals['RSI超卖']: bull_score += 2
        if signals['RSI超买']: bear_score += 2
        if signals['MACD金叉']: bull_score += 2
        if signals['MACD死叉']: bear_score += 2
        if signals['MACD绿柱缩短']: bull_score += 1
        if signals['价格均线下方']: bear_score += 1
        if signals['放量上涨']: bull_score += 1

        results.append({
            'date': date_str,
            'close': row['close'],
            'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4),
            'signals': signals,
            'bull_score': bull_score,
            'bear_score': bear_score,
            'ret_1d': ret_1d,
            'ret_3d': ret_3d,
            'ret_1w': ret_1w,
            'ret_1m': ret_1m,
        })

    return results


def analyze_accuracy(results):
    """分析各信号的成功率"""

    signal_stats = {}

    for r in results:
        for signal_name, active in r['signals'].items():
            if signal_name not in signal_stats:
                signal_stats[signal_name] = {'active_count': 0, 'correct_1d': 0, 'correct_1w': 0, 'total_with_result': 0}

            if active:
                signal_stats[signal_name]['active_count'] += 1

                # 1天后的方向判断
                if r['ret_1d'] is not None:
                    signal_stats[signal_name]['total_with_result'] += 1
                    # 看涨信号 + 涨了 = 正确，看跌信号 + 跌了 = 正确
                    is_bullish = signal_name in ['RSI超卖', 'MACD金叉', 'MACD绿柱缩短', '放量上涨']
                    if is_bullish and r['ret_1d'] > 0:
                        signal_stats[signal_name]['correct_1d'] += 1
                    elif not is_bullish and r['ret_1d'] < 0:
                        signal_stats[signal_name]['correct_1d'] += 1

                # 1周后的方向判断
                if r['ret_1w'] is not None:
                    is_bullish = signal_name in ['RSI超卖', 'MACD金叉', 'MACD绿柱缩短', '放量上涨']
                    if is_bullish and r['ret_1w'] > 0:
                        signal_stats[signal_name]['correct_1w'] += 1
                    elif not is_bullish and r['ret_1w'] < 0:
                        signal_stats[signal_name]['correct_1w'] += 1

    # 计算准确率
    for name, stats in signal_stats.items():
        total = stats['total_with_result']
        if total > 0:
            stats['accuracy_1d'] = round(stats['correct_1d'] / total * 100, 1)
            stats['accuracy_1w'] = round(stats['correct_1w'] / total * 100, 1)
        else:
            stats['accuracy_1d'] = 0
            stats['accuracy_1w'] = 0

    return signal_stats


def generate_chart(signal_stats, stock_name):
    """生成柱状图"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        # 过滤有数据的信号
        active_signals = {k: v for k, v in signal_stats.items() if v['active_count'] > 2}
        if not active_signals:
            print("数据不足，无法生成图表")
            return None

        names = list(active_signals.keys())
        counts = [v['active_count'] for v in active_signals.values()]
        acc_1d = [v['accuracy_1d'] for v in active_signals.values()]
        acc_1w = [v['accuracy_1w'] for v in active_signals.values()]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

        # 左图：各信号出现次数
        bars1 = ax1.barh(names, counts, color='#4A90D9', alpha=0.8)
        ax1.set_xlabel('Occurrences')
        ax1.set_title(f'{stock_name} - Signal Frequency')
        for bar, count in zip(bars1, counts):
            ax1.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                    str(count), va='center', fontsize=10)

        # 右图：各信号准确率（1天 vs 1周）
        x = range(len(names))
        width = 0.35
        bars_1d = ax2.bar([i - width/2 for i in x], acc_1d, width, label='1-Day Accuracy', color='#50C878', alpha=0.8)
        bars_1w = ax2.bar([i + width/2 for i in x], acc_1w, width, label='1-Week Accuracy', color='#FF6B6B', alpha=0.8)

        ax2.set_ylabel('Accuracy %')
        ax2.set_title(f'{stock_name} - Signal Accuracy (Before Tuning)')
        ax2.set_xticks(x)
        ax2.set_xticklabels(names, rotation=45, ha='right', fontsize=9)
        ax2.legend()
        ax2.axhline(y=50, color='gray', linestyle='--', alpha=0.5, label='Random (50%)')
        ax2.set_ylim(0, 100)

        # 在柱子上标注数值
        for bar in bars_1d:
            ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                    f'{bar.get_height():.0f}%', ha='center', va='bottom', fontsize=8)
        for bar in bars_1w:
            ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                    f'{bar.get_height():.0f}%', ha='center', va='bottom', fontsize=8)

        plt.tight_layout()
        chart_path = os.path.join(os.path.dirname(__file__), 'backtest_chart.png')
        plt.savefig(chart_path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"Chart saved: {chart_path}")
        return chart_path

    except ImportError:
        print("matplotlib not installed, skipping chart")
        return None


def generate_factor_accuracy_table(results):
    """生成各因素准确率汇总表"""
    print("\n" + "=" * 70)
    print("Factor Accuracy Summary (Which signals work, which don't)")
    print("=" * 70)

    signal_stats = analyze_accuracy(results)

    print(f"\n{'Signal':<20} {'Count':>6} {'1D Acc':>8} {'1W Acc':>8} {'Verdict':<15}")
    print("-" * 65)

    for name, stats in sorted(signal_stats.items(), key=lambda x: -x[1]['active_count']):
        if stats['active_count'] < 2:
            continue
        count = stats['active_count']
        acc_1d = stats['accuracy_1d']
        acc_1w = stats['accuracy_1w']

        if acc_1w > 60:
            verdict = "STRONG"
        elif acc_1w > 50:
            verdict = "Moderate"
        elif acc_1w > 0:
            verdict = "Weak"
        else:
            verdict = "N/A"

        print(f"{name:<20} {count:>6} {acc_1d:>7.1f}% {acc_1w:>7.1f}% {verdict:<15}")

    # 组合信号分析
    print(f"\n{'Combined Signal':<30} {'Count':>6} {'1W Acc':>8}")
    print("-" * 50)

    combo_stats = {}
    for r in results:
        # 组合1: RSI超卖 + MACD绿柱缩短
        if r['signals'].get('RSI超卖') and r['signals'].get('MACD绿柱缩短'):
            key = 'RSI超卖+MACD绿柱缩短'
            combo_stats.setdefault(key, {'count': 0, 'correct': 0})
            combo_stats[key]['count'] += 1
            if r['ret_1w'] and r['ret_1w'] > 0:
                combo_stats[key]['correct'] += 1

        # 组合2: MACD死叉 + 价格均线下方
        if r['signals'].get('MACD死叉') and r['signals'].get('价格均线下方'):
            key = 'MACD死叉+均线下方'
            combo_stats.setdefault(key, {'count': 0, 'correct': 0})
            combo_stats[key]['count'] += 1
            if r['ret_1w'] and r['ret_1w'] < 0:
                combo_stats[key]['correct'] += 1

        # 组合3: MACD金叉 + 放量上涨
        if r['signals'].get('MACD金叉') and r['signals'].get('放量上涨'):
            key = 'MACD金叉+放量上涨'
            combo_stats.setdefault(key, {'count': 0, 'correct': 0})
            combo_stats[key]['count'] += 1
            if r['ret_1w'] and r['ret_1w'] > 0:
                combo_stats[key]['correct'] += 1

    for name, stats in sorted(combo_stats.items(), key=lambda x: -x[1]['count']):
        if stats['count'] < 1:
            continue
        acc = round(stats['correct'] / stats['count'] * 100, 1)
        print(f"{name:<30} {stats['count']:>6} {acc:>7.1f}%")

    return signal_stats


# ========== Main ==========
if __name__ == "__main__":
    # Run backtest: Jan 1 to Apr 30, 2026
    results = batch_backtest('600519', 'Kweichow Moutai', '2026-01-01', '2026-04-30')

    if results:
        print(f"Total backtest samples: {len(results)}")

        # Factor accuracy analysis
        signal_stats = generate_factor_accuracy_table(results)

        # Generate chart
        chart_path = generate_chart(signal_stats, 'Kweichow Moutai')

        # Save raw results
        output_path = os.path.join(os.path.dirname(__file__), 'backtest_results.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nRaw data saved: {output_path}")
    else:
        print("No data available for backtesting")
