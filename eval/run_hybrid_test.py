"""混合检索快检：BM25 / dense(bge) / RRF融合 三路对比
离线实验，不动库。评测集 v0.2（10条，分层报告）
"""
import json
import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
import chromadb

INSTRUCTION = '为这个句子生成表示以用于检索相关文章： '
NEIGHBOR_GROUPS = {
    "茅台超卖组": ["pred_43", "pred_83", "pred_106", "pred_40"],
    "平安力度组": ["pred_8", "pred_72"],
}


def tokenize(text):
    return list(jieba.cut_for_search(text))


def rrf_fuse(rankings, k=60):
    """rankings: 多个 [doc_id] 排名列表 → RRF 分数字典"""
    scores = {}
    for ranking in rankings:
        for r, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + r)
    return sorted(scores.items(), key=lambda x: -x[1])


def report(name, ranks_by_case):
    """ranks_by_case: [(case, rank)]"""
    normal = [r for c, r in ranks_by_case if not c.get('neighbor_group')]
    neigh = [r for c, r in ranks_by_case if c.get('neighbor_group')]
    n_recall3 = sum(1 for c, r in ranks_by_case
                    if not c.get('neighbor_group') and r and r <= 3)
    n_mrr = sum(1.0 / r for c, r in ranks_by_case
                if not c.get('neighbor_group') and r)
    g_recall1 = sum(1 for c, r in ranks_by_case
                    if c.get('neighbor_group') and r == 1)
    print(f'\n=== {name} ===')
    print(f'常规样本(n={len(normal)}): Recall@3={n_recall3}/{len(normal)} '
          f'MRR={n_mrr/len(normal):.3f}')
    print(f'近邻组(n={len(neigh)}): Recall@1={g_recall1}/{len(neigh)}')
    line = ' '.join(f"{c['id']}:{r if r else '—'}" for c, r in ranks_by_case)
    print(f'排名明细: {line}')
    return ranks_by_case


def main():
    with open('eval/eval_set.json', 'r', encoding='utf-8') as f:
        cases = json.load(f)['cases']

    c = chromadb.PersistentClient(path='data/chroma_db')
    col = c.get_collection('predictions')
    docs = col.get(include=['documents'])
    ids = docs['ids']
    texts = docs['documents']
    n = len(ids)

    # ===== BM25 路 =====
    tokenized = [tokenize(t) for t in texts]
    bm25 = BM25Okapi(tokenized)

    # ===== dense 路（bge + 官方查询前缀）=====
    m = SentenceTransformer('BAAI/bge-large-zh-v1.5')
    embs_docs = m.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    results = {"bm25": [], "dense": [], "rrf": []}

    for case in cases:
        q = case['query']

        # BM25 排名（全库）
        scores = bm25.get_scores(tokenize(q))
        bm25_rank = [ids[i] for i in np.argsort(-scores)]

        # dense 排名（全库）
        qv = m.encode([INSTRUCTION + q], normalize_embeddings=True)[0]
        sims = embs_docs @ qv
        dense_rank = [ids[i] for i in np.argsort(-sims)]

        # RRF 融合（各取前20融合）
        fused = rrf_fuse([bm25_rank[:20], dense_rank[:20]])
        rrf_rank = [d for d, _ in fused]

        exp = case['expected_id']
        for name, ranking in [('bm25', bm25_rank), ('dense', dense_rank), ('rrf', rrf_rank)]:
            rank = ranking.index(exp) + 1 if exp in ranking else None
            results[name].append((case, rank))

    report('BM25 单路', results['bm25'])
    report('Dense bge+前缀', results['dense'])
    report('RRF 融合 (k=60, 各top20)', results['rrf'])

    with open('eval/hybrid_quick_test.json', 'w', encoding='utf-8') as f:
        json.dump({k: [{'id': c['id'], 'expected': c['expected_id'], 'rank': r}
                       for c, r in v] for k, v in results.items()},
                  f, ensure_ascii=False, indent=2)
    print('\n已保存: eval/hybrid_quick_test.json')


if __name__ == '__main__':
    main()
