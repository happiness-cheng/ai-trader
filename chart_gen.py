"""K线图生成模块
生成带技术指标的股票走势图
"""
import os
import logging
import matplotlib
matplotlib.use('Agg')  # 无GUI后端
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import ticker
import numpy as np

import config

logger = logging.getLogger(__name__)

# 中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun']
plt.rcParams['axes.unicode_minus'] = False

CHART_DIR = os.path.join(config.LOG_DIR, 'charts')


def generate_stock_chart(code, name, df, indicators=None, save_path=None):
    """生成单只股票的K线+指标图
    Args:
        code: 股票代码
        name: 股票名称
        df: 历史数据DataFrame（需含date/open/close/high/low/volume）
        indicators: 技术指标字典（可选）
        save_path: 保存路径（可选）
    Returns:
        str: 图片保存路径
    """
    if len(df) < 20:
        logger.warning(f"{name} 数据不足，跳过画图")
        return None

    os.makedirs(CHART_DIR, exist_ok=True)
    if save_path is None:
        save_path = os.path.join(CHART_DIR, f'{code}_{name}.png')

    # 只取最近60天
    df = df.tail(60).reset_index(drop=True)
    n = len(df)

    # 计算均线
    close = df['close'].values
    ma5 = np.convolve(close, np.ones(5)/5, mode='full')[:n]
    ma20 = np.convolve(close, np.ones(20)/20, mode='full')[:n]

    # 计算MACD
    ema12 = np.zeros(n)
    ema26 = np.zeros(n)
    ema12[0] = close[0]
    ema26[0] = close[0]
    for i in range(1, n):
        ema12[i] = ema12[i-1] * 11/13 + close[i] * 2/13
        ema26[i] = ema26[i-1] * 25/27 + close[i] * 2/27
    dif = ema12 - ema26
    dea = np.zeros(n)
    dea[0] = dif[0]
    for i in range(1, n):
        dea[i] = dea[i-1] * 8/10 + dif[i] * 2/10
    macd_bar = (dif - dea) * 2

    # 计算RSI
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.convolve(gain, np.ones(14)/14, mode='full')[:n]
    avg_loss = np.convolve(loss, np.ones(14)/14, mode='full')[:n]
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    rsi = 100 - 100 / (1 + rs)

    # 创建图表（4个子图：K线、成交量、MACD、RSI）
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(12, 10),
                                              gridspec_kw={'height_ratios': [4, 1.5, 1.5, 1.5]},
                                              sharex=True)

    dates = range(n)
    x_labels = [str(df['date'].iloc[i])[:10] for i in range(n)]

    # === K线图 ===
    for i in range(n):
        o, c, h, l = df['open'].iloc[i], df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]
        color = '#e74c3c' if c >= o else '#2ecc71'  # 红涨绿跌
        ax1.plot([i, i], [l, h], color=color, linewidth=0.8)
        rect = Rectangle((i-0.3, min(o, c)), 0.6, abs(c-o) or 0.1,
                         facecolor=color, edgecolor=color)
        ax1.add_patch(rect)

    # 均线
    ax1.plot(dates, ma5, color='#f39c12', linewidth=1.2, label='MA5', alpha=0.8)
    ax1.plot(dates, ma20, color='#3498db', linewidth=1.2, label='MA20', alpha=0.8)
    ax1.fill_between(dates, df['low'].min()*0.98, df['high'].max()*1.02,
                     where=ma5 < ma20, alpha=0.05, color='green')
    ax1.fill_between(dates, df['low'].min()*0.98, df['high'].max()*1.02,
                     where=ma5 > ma20, alpha=0.05, color='red')

    ax1.set_title(f'{name} ({code})', fontsize=14, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylabel('价格', fontsize=9)

    # 最新价标注
    last_price = close[-1]
    ax1.axhline(y=last_price, color='gray', linestyle='--', alpha=0.5)
    ax1.text(n+0.5, last_price, f'{last_price:.2f}', fontsize=9, va='center')

    # === 成交量 ===
    vol = df['volume'].values
    for i in range(n):
        color = '#e74c3c' if df['close'].iloc[i] >= df['open'].iloc[i] else '#2ecc71'
        ax2.bar(i, vol[i], color=color, alpha=0.7, width=0.6)
    ax2.set_ylabel('成交量', fontsize=9)
    ax2.grid(True, alpha=0.3)

    # === MACD ===
    colors = ['#e74c3c' if v >= 0 else '#2ecc71' for v in macd_bar]
    ax3.bar(dates, macd_bar, color=colors, alpha=0.7, width=0.6)
    ax3.plot(dates, dif, color='#3498db', linewidth=1, label='DIF')
    ax3.plot(dates, dea, color='#f39c12', linewidth=1, label='DEA')
    ax3.axhline(y=0, color='gray', linewidth=0.5)
    ax3.legend(loc='upper left', fontsize=8)
    ax3.set_ylabel('MACD', fontsize=9)
    ax3.grid(True, alpha=0.3)

    # === RSI ===
    ax4.plot(dates, rsi, color='#9b59b6', linewidth=1.2)
    ax4.axhline(y=70, color='red', linestyle='--', alpha=0.5, label='超买(70)')
    ax4.axhline(y=30, color='green', linestyle='--', alpha=0.5, label='超卖(30)')
    ax4.fill_between(dates, 0, 30, alpha=0.1, color='green')
    ax4.fill_between(dates, 70, 100, alpha=0.1, color='red')
    ax4.set_ylim(0, 100)
    ax4.legend(loc='upper left', fontsize=8)
    ax4.set_ylabel('RSI', fontsize=9)
    ax4.grid(True, alpha=0.3)

    # X轴标签
    step = max(1, n // 10)
    ax4.set_xticks(range(0, n, step))
    ax4.set_xticklabels([x_labels[i] for i in range(0, n, step)], rotation=45, fontsize=8)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    logger.info(f"生成图表: {save_path}")
    return save_path


def generate_watchlist_charts():
    """为关注列表所有股票生成图表"""
    import market_data as md
    import strategy

    paths = []
    watchlist = strategy.get_watchlist()
    for stock in watchlist:
        code = stock['code']
        name = stock.get('name', '')
        df = md.get_stock_history(code, days=120)
        if len(df) >= 30:
            path = generate_stock_chart(code, name, df)
            if path:
                paths.append(path)
    return paths


# 测试
if __name__ == "__main__":
    import market_data as md
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    for code, name in [('600519', '贵州茅台'), ('000858', '五粮液')]:
        df = md.get_stock_history(code, days=120)
        if len(df) > 0:
            path = generate_stock_chart(code, name, df)
            print(f'{name}: {path}')
