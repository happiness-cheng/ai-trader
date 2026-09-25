"""改造前基线：用当前 ChromaDB（默认 embedding）跑评测集
改造④（embedding 换型）第一步——先给旧模型做体检，数字存档供面试讲
"""
import json
import chromadb


def load_eval_set():
    with open('eval/eval_set.json', 'r', encoding='utf-8') as f:
        return json.load(f)['cases']


def run_eval(cases):
    client = chromadb.PersistentClient(path='data/chroma_db')
    col = client.get_collection('predictions')
    metrics = {"recall@3": 0, "mrr": 0.0}
    details = []
    for case in cases:
        res = col.query(query_texts=[case['query']], n_results=10)
        ids = res['ids'][0]
        dists = res['distances'][0]

        # expected_id 排名（1-based）；不在结果里 rank 保持 None
        if case['expected_id'] in ids:
            rank = ids.index(case['expected_id']) + 1
        else:
            rank = None

        # recall@3: 标准答案是否出现在前 3
        recall3 = case['expected_id'] in ids[:3]

        mrr = 0.0 if rank is None else 1.0 / rank
        if recall3:
            metrics["recall@3"] += 1
        metrics["mrr"] += mrr
        details.append({
            "id": case['id'],
            "query": case['query'],
            "expected": case['expected_id'],
            "rank": rank,
            "recall3": recall3,
            "top1_dist": round(dists[0], 4),
            "dist_of_expected": round(dists[rank - 1], 4) if rank else None,
            "top3_ids": ids[:3],
        })

    n = len(cases)
    return {"recall@3": metrics["recall@3"] / n,
            "mrr": metrics["mrr"] / n,
            "n_cases": n,
            "details": details}


if __name__ == '__main__':
    result = run_eval(load_eval_set())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    with open('eval/baseline_before.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('\n已保存: eval/baseline_before.json')
