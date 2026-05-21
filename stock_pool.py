"""股票池与全市场扫描
从沪深300中筛选有信号的股票
"""
import json
import logging
import subprocess
import os
from datetime import datetime

import config
import market_data as md
import strategy

logger = logging.getLogger(__name__)

# 沪海300核心成分股（部分，覆盖主要行业）
# 完整版可从文件加载
HS300_CORE = {
    # 白酒/消费
    '600519': '贵州茅台', '000858': '五粮液', '002304': '洋河股份',
    '600887': '伊利股份', '603288': '海天味业', '000568': '泸州老窖',
    # 金融
    '601318': '中国平安', '600036': '招商银行', '601166': '兴业银行',
    '000001': '平安银行', '601398': '工商银行', '601288': '农业银行',
    '600030': '中信证券', '601688': '华泰证券',
    # 新能源
    '300750': '宁德时代', '601012': '隆基绿能', '002459': '晶澳科技',
    '300274': '阳光电源', '600438': '通威股份',
    # 科技
    '000333': '美的集团', '000651': '格力电器', '002415': '海康威视',
    '000725': '京东方A', '603986': '兆易创新',
    # 医药
    '600276': '恒瑞医药', '300760': '迈瑞医疗', '000538': '云南白药',
    '002007': '华兰生物',
    # 地产/基建
    '600048': '保利发展', '000002': '万科A',
    # 能源/资源
    '601857': '中国石油', '600028': '中国石化', '601088': '中国神华',
    '600585': '海螺水泥', '000878': '云南铜业',
    # 汽车
    '600104': '上汽集团', '002594': '比亚迪', '601633': '长城汽车',
    # 互联网/软件
    '002230': '科大讯飞', '300059': '东方财富',
}


def get_stock_universe():
    """获取分析范围（当前使用核心股票池）"""
    return HS300_CORE


def scan_market(top_n=10):
    """全市场快速扫描，筛出有信号的股票
    第一层筛选：规则过滤，不调AI，速度快
    Returns:
        list: 候选股票 [{code, name, signals, score}]
    """
    logger.info("开始全市场扫描...")
    universe = get_stock_universe()
    candidates = []

    for code, name in universe.items():
        try:
            df = md.get_stock_history(code, days=60)
            if len(df) < 30:
                continue

            indicators = md.get_technical_indicators(df)
            if not indicators:
                continue

            # 基本面过滤（跳过估值过高或亏损股）
            fundamentals = md.get_fundamentals(code)
            pe = fundamentals.get('pe')
            if pe is not None:
                if pe > 100 or pe < 0:
                    logger.info(f"  跳过 {name}: PE={pe}")
                    continue

            # 打分（简单的多因子打分）
            score = 0
            signals = []

            # RSI（超卖加分，超买减分）
            rsi = indicators.get('rsi', 50)
            if rsi < 25:
                score += 30
                signals.append(f'RSI极度超卖({rsi:.0f})')
            elif rsi < 35:
                score += 15
                signals.append(f'RSI超卖({rsi:.0f})')
            elif rsi > 75:
                score -= 20
                signals.append(f'RSI超买({rsi:.0f})')

            # MACD
            if indicators.get('macd_golden_cross'):
                score += 25
                signals.append('MACD金叉')
            if indicators.get('macd_hist', 0) > 0 and indicators.get('macd_hist', 0) > indicators.get('macd_hist_prev', 0):
                score += 10
                signals.append('MACD柱放大')

            # 均线
            if indicators.get('ma_golden_cross'):
                score += 20
                signals.append('均线金叉')
            if indicators.get('price_above_ma5') and indicators.get('price_above_ma20'):
                score += 10
                signals.append('价格在均线上方')

            # 成交量
            vol_ratio = indicators.get('vol_ratio', 1)
            if vol_ratio > 2.0 and indicators.get('pct_change', 0) > 0:
                score += 15
                signals.append(f'放量上涨(量比{vol_ratio:.1f})')
            elif vol_ratio > 1.5:
                score += 5

            # 涨跌幅（跌多了可能有机会）
            pct = indicators.get('pct_change', 0)
            if pct < -3:
                score += 5  # 大跌可能有反弹机会

            if score > 20 and signals:
                candidates.append({
                    'code': code,
                    'name': name,
                    'score': score,
                    'signals': signals,
                    'close': indicators.get('close', 0),
                    'rsi': rsi,
                    'macd_hist': indicators.get('macd_hist', 0),
                })

        except Exception as e:
            logger.debug(f"扫描 {name}({code}) 跳过: {e}")
            continue

    # 按分数排序
    candidates.sort(key=lambda x: x['score'], reverse=True)
    candidates = candidates[:top_n]

    logger.info(f"扫描完成: {len(universe)}只中筛出 {len(candidates)} 只候选")
    return candidates


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    print(f"股票池: {len(get_stock_universe())} 只")
    print("\n开始扫描...")
    cands = scan_market(10)
    print(f"\n候选股 ({len(cands)}):")
    for c in cands:
        print(f"  [{c['score']}分] {c['name']}({c['code']}) 收盘{c['close']} RSI:{c['rsi']:.0f} 信号:{', '.join(c['signals'])}")
