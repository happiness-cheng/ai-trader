"""核对生成结果完整性和抽样质量"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
from collections import Counter

regs = Counter()
preds = set()
samples = {}
with open('data/corpus_diverse.jsonl', encoding='utf-8') as f:
    for line in f:
        r = json.loads(line)
        regs[r['register']] += 1
        preds.add(r['pred_id'])
        if r['register'] not in samples:
            samples[r['register']] = r

target = 246 * 3
print('register分布:', dict(regs))
print('覆盖预测数:', len(preds), '/ 246')
print(f'生成 {sum(regs.values())} / 目标 {target}（放弃 {target - sum(regs.values())} 条）')
for reg, s in samples.items():
    print(f"\n[{reg}] {s['metadata']['name']} {s['metadata']['recommendation']}")
    print(' ', s['text'][:90])
