"""批量预测脚本（在AutoDL上每天早上跑一次）
读取关注列表 → 逐只获取技术指标 → 调模型预测 → 保存结果到JSON
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import market_data as md
import strategy
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# 关注列表 + 股票池
WATCHLIST_CODES = [s['code'] for s in strategy.get_watchlist()]
EXTRA_CODES = ['300750', '601318', '000858', '601166', '600036', '002594',
               '601857', '000568', '300274', '002415']
ALL_CODES = list(set(WATCHLIST_CODES + EXTRA_CODES))


def main():
    from model_client import predict, get_model_status

    status = get_model_status()
    logger.info(f"模型状态: {status}")

    results = {}
    for code in ALL_CODES:
        # 获取股票名称
        quote = md.get_realtime_quote(code)
        name = quote.get('name', code) if quote else code

        # 获取技术指标
        df = md.get_stock_history(code, days=120)
        if len(df) < 30:
            logger.warning(f"跳过 {name}: 数据不足")
            continue

        indicators = md.get_technical_indicators(df)
        if not indicators:
            continue

        # 模型预测
        pred = predict(name, indicators)
        if pred:
            results[code] = {
                'name': name,
                'prediction': pred,
                'rsi': round(indicators.get('rsi', 0), 1),
                'macd_hist': round(indicators.get('macd_hist', 0), 4),
                'close': round(indicators.get('close', 0), 2),
            }
            logger.info(f"  {name}: 1d={pred['1d']:+.2f}% 1w={pred['1w']:+.2f}% 1m={pred['1m']:+.2f}%")
        else:
            logger.warning(f"  {name}: 预测失败")

    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), 'data', 'model_predictions.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'date': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
            'predictions': results,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"已保存 {len(results)} 只股票的预测到 {output_path}")


if __name__ == "__main__":
    main()
