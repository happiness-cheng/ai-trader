"""诊断实验：失败用例的 query 与四种 register 版本的真实相似度
回答"为什么召回率低——是不是风格不一致"
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import chromadb
from sentence_transformers import SentenceTransformer

m = SentenceTransformer('BAAI/bge-large-zh-v1.5')
c = chromadb.PersistentClient(path='data/chroma_db')
col = c.get_collection('predictions__diverse_v1')

with open('eval/eval_set.json', encoding='utf-8') as f:
    cases = {x['id']: x for x in json.load(f)['cases']}

for eid in ['E01', 'E02', 'E03', 'E05']:
    case = cases[eid]
    base = int(case['expected_id'].replace('pred_', ''))
    got = col.get(where={'pred_id': base}, include=['documents', 'metadatas'])
    q = case['query']
    qv = m.encode([q], normalize_embeddings=True)[0]
    print(f'--- {eid} [{case["expected_id"]}]')
    print(f'    查询: {q[:50]}')
    rows = []
    for doc_id, doc, meta in zip(got['ids'], got['documents'], got['metadatas']):
        dv = m.encode([doc], normalize_embeddings=True)[0]
        sim = float(qv @ dv)
        rows.append((meta['register'], sim, doc))
    for reg, sim, doc in sorted(rows, key=lambda x: -x[1]):
        print(f'  {reg:>9}: sim={sim:.3f} | {doc[:42]}')
    print()
