"""新语料集合（predictions__diverse_v1）分层评测
评测集 v0.2 十条；expected_id = pred_N → 命中该 pred 的任何 register 都算召回
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import chromadb
from sentence_transformers import SentenceTransformer

MODEL_NAME = 'BAAI/bge-large-zh-v1.5'

NEIGHBOR_GROUPS = {
    "茅台超卖组": [43, 83, 106, 40],
    "平安力度组": [8, 72],
}


def load_eval_set():
    with open('eval/eval_set.json', 'r', encoding='utf-8') as f:
        return json.load(f)['cases']


def run():
    client = chromadb.PersistentClient(path='data/chroma_db')
    col = client.get_collection('predictions__diverse_v1')
    n_total = col.count()

    normal = {"recall@3": 0, "mrr": 0.0, "n": 0, "details": []}
    neigh = {"recall@1": 0, "n": 0, "details": []}

    cases = load_eval_set()
    # 新集合用 bge(1024维) 编码入库，查询必须用同一模型
    # （ChromaDB 不校验查询向量维度一致性时才会走内置默认——上一版报错正是因为
    #   query_texts 触发了内置 384 维默认模型，与库内 1024 维不匹配）
    model = SentenceTransformer(MODEL_NAME)

    for case in cases:
        expected_base = int(case['expected_id'].replace('pred_', ''))

        q_emb = model.encode([case['query']], normalize_embeddings=True)[0].tolist()
        res = col.query(query_embeddings=[q_emb], n_results=n_total)
        ids = res['ids'][0]
        dists = res['distances'][0]

        # 命中判定：任何 register 版本
        def base_rank(pred_base):
            """该 pred 最好成绩的文档排名（1-based），不存在返回 None"""
            best = None
            for i, doc_id in enumerate(ids):
                if doc_id.startswith(f'pred_{pred_base}__'):
                    if best is None or i + 1 < best:
                        best = i + 1
            return best

        rank = base_rank(expected_base)

        grp = case.get('neighbor_group')
        if grp:
            member_ranks = {m: base_rank(m) for m in NEIGHBOR_GROUPS[grp]}
            hit1 = (rank == 1)
            if hit1:
                neigh["recall@1"] += 1
            neigh["n"] += 1
            neigh["details"].append({
                "id": case['id'], "expected": case['expected_id'],
                "rank": rank, "recall1": hit1, "group": grp,
                "group_member_ranks": member_ranks,
                "hit_register": ids[rank-1].split('__')[-1] if rank else None,
            })
        else:
            mrr = 0.0 if rank is None else 1.0 / rank
            recall3 = rank is not None and rank <= 3
            if recall3:
                normal["recall@3"] += 1
            normal["mrr"] += mrr
            normal["n"] += 1
            normal["details"].append({
                "id": case['id'], "expected": case['expected_id'],
                "rank": rank, "recall3": recall3, "mrr": round(mrr, 3),
                "top1_dist": round(dists[0], 4),
                "top1_register": ids[0].split('__')[-1] if ids else None,
            })

    n_n = normal["n"] or 1
    n_g = neigh["n"] or 1
    result = {
        "collection": "predictions__diverse_v1",
        "corpus_size": n_total,
        "常规样本": {
            "n": normal["n"],
            "recall@3": round(normal["recall@3"] / n_n, 3),
            "mrr": round(normal["mrr"] / n_n, 3),
            "details": normal["details"],
        },
        "近邻组样本": {
            "n": neigh["n"],
            "recall@1": round(neigh["recall@1"] / n_g, 3),
            "details": neigh["details"],
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    with open('eval/baseline_after.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('\n已保存: eval/baseline_after.json')


if __name__ == '__main__':
    run()
