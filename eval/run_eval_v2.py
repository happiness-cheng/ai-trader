"""分层评测脚本 v2
- 常规样本（无近邻干扰）：Recall@3 + MRR —— 反映基本盘
- 近邻组样本：Recall@1 + 组内排名 —— 反映细粒度消歧能力
两组分开报告，不混算总分
"""
import json
import chromadb

NEIGHBOR_GROUPS = {
    "茅台超卖组": ["pred_43", "pred_83", "pred_106", "pred_40"],
    "平安力度组": ["pred_8", "pred_72"],
}


def load_eval_set():
    with open('eval/eval_set.json', 'r', encoding='utf-8') as f:
        return json.load(f)['cases']


def run(cases, collection_name='predictions'):
    client = chromadb.PersistentClient(path='data/chroma_db')
    col = client.get_collection(collection_name)
    n_total = col.count()

    normal = {"recall@3": 0, "mrr": 0.0, "n": 0, "details": []}
    neighbor = {"recall@1": 0, "n": 0, "details": []}

    for case in cases:
        res = col.query(query_texts=[case['query']], n_results=n_total)
        ids = res['ids'][0]
        dists = res['distances'][0]
        exp = case['expected_id']
        rank = ids.index(exp) + 1 if exp in ids else None

        grp = case.get('neighbor_group')
        if grp:
            # 近邻组：重点看 Recall@1 + 组内所有成员的排名
            member_ranks = {}
            for m in NEIGHBOR_GROUPS.get(grp, []):
                if m in ids:
                    member_ranks[m] = ids.index(m) + 1
            hit1 = (rank == 1)
            if hit1:
                neighbor["recall@1"] += 1
            neighbor["n"] += 1
            neighbor["details"].append({
                "id": case['id'], "expected": exp, "rank": rank,
                "recall1": hit1, "group": grp,
                "group_member_ranks": member_ranks,
            })
        else:
            mrr = 0.0 if rank is None else 1.0 / rank
            recall3 = exp in ids[:3]
            if recall3:
                normal["recall@3"] += 1
            normal["mrr"] += mrr
            normal["n"] += 1
            normal["details"].append({
                "id": case['id'], "expected": exp, "rank": rank,
                "recall3": recall3, "mrr": round(mrr, 3),
                "top1_dist": round(dists[0], 4),
            })

    n_n = normal["n"] or 1
    n_g = neighbor["n"] or 1
    return {
        "collection": collection_name,
        "corpus_size": n_total,
        "常规样本": {
            "n": normal["n"],
            "recall@3": round(normal["recall@3"] / n_n, 3),
            "mrr": round(normal["mrr"] / n_n, 3),
            "details": normal["details"],
        },
        "近邻组样本": {
            "n": neighbor["n"],
            "recall@1": round(neighbor["recall@1"] / n_g, 3),
            "details": neighbor["details"],
        },
    }


if __name__ == '__main__':
    result = run(load_eval_set())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    with open('eval/baseline_before_v2.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('\n已保存: eval/baseline_before_v2.json')
