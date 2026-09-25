"""重建多样化语料集合 + 同质性复测
1) 861 条（729 新 + 132 teaching 原文）→ predictions__diverse 集合（bge 编码）
2) 新语料两两相似度分布 + 0.9 阈值并查集聚类 → 对比旧库基线（均值0.842/6簇）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import logging

import chromadb
import numpy as np
from sentence_transformers import SentenceTransformer

import prediction_tracker

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

MODEL_NAME = 'BAAI/bge-large-zh-v1.5'
NEW_COLLECTION = 'predictions__diverse_v1'
TEACHING_REGISTER = 'teaching'


def load_all_docs():
    """729 条多样化 + 132 条 teaching 原文，统一格式"""
    docs = []  # {doc_id, text, metadata}

    # 多样化语料
    with open('data/corpus_diverse.jsonl', encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            meta = dict(r['metadata'])
            meta['register'] = r['register']
            meta['pred_id'] = r['pred_id']
            docs.append({"doc_id": r['doc_id'], "text": r['text'], "metadata": meta})

    # teaching 原文（从 prediction_tracker 读，保证和旧集合一致）
    for p in prediction_tracker._load():
        if not p.get('name') or p.get('name') == '未知':
            continue
        doc_text = (
            f"{p.get('name','')}({p.get('code','')}) "
            f"{p.get('recommendation','')} 置信度{p.get('confidence',0):.0%} "
            f"{p.get('reasoning','')}"
        )
        docs.append({
            "doc_id": f"pred_{p['id']}__teaching",
            "text": doc_text,
            "metadata": {
                "code": p.get('code', ''),
                "name": p.get('name', ''),
                "date": p.get('date', '')[:10],
                "recommendation": p.get('recommendation', ''),
                "confidence": float(p.get('confidence') or 0),
                "outcome": p.get('outcome', '待验证'),
                "pred_id": p['id'],
                "register": TEACHING_REGISTER,
            },
        })
    return docs


def build_collection(docs, model):
    client = chromadb.PersistentClient(path='data/chroma_db')
    # 重建式创建：先删旧测试集合（幂等）
    try:
        client.delete_collection(NEW_COLLECTION)
        logger.info(f"已删除旧集合 {NEW_COLLECTION}")
    except Exception:
        pass
    col = client.create_collection(
        name=NEW_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    texts = [d['text'] for d in docs]
    logger.info(f"编码 {len(texts)} 条（{MODEL_NAME}）...")
    embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    col.upsert(
        ids=[d['doc_id'] for d in docs],
        embeddings=[e.tolist() for e in embs],
        documents=texts,
        metadatas=[d['metadata'] for d in docs],
    )
    logger.info(f"集合 {NEW_COLLECTION} 建立: {col.count()} 条")
    return col


def homogeneity_report(docs, model, label):
    """两两相似度分布 + 0.9 并查集聚类"""
    embs = model.encode([d['text'] for d in docs], normalize_embeddings=True,
                        show_progress_bar=False)
    sims = embs @ embs.T
    n = len(docs)
    mask = ~np.eye(n, dtype=bool)
    off = sims[mask]
    total_pairs = n * (n - 1) // 2

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    high_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= 0.9:
                high_pairs += 1
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb

    from collections import defaultdict
    clusters = defaultdict(int)
    for i in range(n):
        clusters[find(i)] += 1
    sizes = sorted(clusters.values(), reverse=True)

    report = {
        "label": label,
        "n_docs": n,
        "mean_sim": round(float(off.mean()), 3),
        "p90_sim": round(float(np.percentile(off, 90)), 3),
        "max_sim": round(float(off.max()), 3),
        "pairs_over_0.7": round(float((off > 0.7).sum() / total_pairs), 3),
        "clusters_at_0.9": len(clusters),
        "cluster_top5_sizes": sizes[:5],
    }
    return report


if __name__ == '__main__':
    docs = load_all_docs()
    logger.info(f"语料合计 {len(docs)} 条 "
                f"(teaching={sum(1 for d in docs if d['metadata']['register']=='teaching')}, "
                f"多样化={sum(1 for d in docs if d['metadata']['register']!='teaching')})")

    model = SentenceTransformer(MODEL_NAME)

    # 1) 同质性复测（先测，再建集合）
    report = homogeneity_report(docs, model, "多样化语料 v1")
    logger.info("同质性报告: %s", json.dumps(report, ensure_ascii=False))

    # 2) 建集合
    build_collection(docs, model)

    with open('eval/homogeneity_diverse.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("已保存: eval/homogeneity_diverse.json")
