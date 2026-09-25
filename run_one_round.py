"""单轮模拟运行：非交易时段验证全链路（行情→规则→AI分析→预测落盘）
不触发下单（ths_trader 不被调用），不推飞书。
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

import main  # noqa: E402  复用主引擎的分析管线

print("=" * 50)
print("单轮模拟运行开始（不交易、不推送）")
print("=" * 50)

main.analyze_and_trade()

print("=" * 50)
print("单轮完成。检查点：")
print("  1. logs/ 下今日 market/indicators/ai_decisions 是否新增")
print("  2. data/predictions.json 是否新增预测")
print("  3. ai_analyzer 日志中是否出现经验注入（experience_ctx 非空时）")
print("=" * 50)
