"""RAG 向量检索模块
基于 ChromaDB 的语义搜索，替代传统的"取最近N条"检索方式
索引3类数据：AI决策、预测记录、交易经验
"""
import json
import logging
import os

import chromadb
from chromadb.config import Settings

import config

logger = logging.getLogger(__name__)

PERSIST_DIR = os.path.join(config.DATA_DIR, 'chroma_db')


class RagStore:
    """基于 ChromaDB 的向量知识库"""

    def __init__(self, persist_dir=None):
        if persist_dir is None:
            persist_dir = PERSIST_DIR

        os.makedirs(persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=persist_dir)

        # 使用 ChromaDB 内置默认 embedding（ONNX，无需 PyTorch）
        # 对中英文混合文本效果足够好
        self.decisions = self.client.get_or_create_collection(
            name="ai_decisions",
            metadata={"hnsw:space": "cosine"},
        )
        self.predictions = self.client.get_or_create_collection(
            name="predictions",
            metadata={"hnsw:space": "cosine"},
        )
        self.lessons = self.client.get_or_create_collection(
            name="lessons",
            metadata={"hnsw:space": "cosine"},
        )

    # ========== 索引操作 ==========

    def index_decision(self, code, name, date, recommendation, confidence,
                       reasoning, key_signals, outcome=None):
        """索引一条AI决策"""
        doc_id = f"decision_{code}_{date}_{recommendation}"
        doc_text = f"{name}({code}) {recommendation} 置信度{confidence:.0%} {reasoning}"

        self.decisions.upsert(
            ids=[doc_id],
            documents=[doc_text],
            metadatas=[{
                "code": code,
                "name": name,
                "date": date,
                "recommendation": recommendation,
                "confidence": float(confidence) if confidence else 0,
                "outcome": outcome or "pending",
                "key_signals": json.dumps(key_signals or [], ensure_ascii=False),
            }],
        )

    def index_prediction(self, pred):
        """索引一条预测记录"""
        doc_id = f"pred_{pred['id']}"
        reasoning = pred.get('reasoning', '')
        outcome = pred.get('outcome', '待验证')
        doc_text = (
            f"{pred.get('name', '')}({pred.get('code', '')}) "
            f"{pred.get('recommendation', '')} 置信度{pred.get('confidence', 0):.0%} "
            f"{reasoning} 结果:{outcome}"
        )

        self.predictions.upsert(
            ids=[doc_id],
            documents=[doc_text],
            metadatas=[{
                "code": pred.get('code', ''),
                "name": pred.get('name', ''),
                "date": pred.get('date', '')[:10],
                "recommendation": pred.get('recommendation', ''),
                "confidence": float(pred.get('confidence', 0)),
                "outcome": outcome,
                "actual_change_pct": float(pred.get('actual_change_pct') or 0),
            }],
        )

    def index_lesson(self, date, title, content):
        """索引一条交易经验"""
        doc_id = f"lesson_{date}_{title}"
        self.lessons.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[{"date": date, "title": title}],
        )

    # ========== 检索操作 ==========

    def search(self, query, top_k=3, collections=None):
        """语义搜索

        Args:
            query: 查询文本，如 "RSI超卖时买入的历史表现"
            top_k: 每个集合返回的结果数
            collections: 搜索哪些集合，默认全部

        Returns:
            [{collection, document, metadata, distance}, ...]
        """
        if collections is None:
            collections = ["ai_decisions", "predictions", "lessons"]

        coll_map = {
            "ai_decisions": self.decisions,
            "predictions": self.predictions,
            "lessons": self.lessons,
        }

        results = []
        for coll_name in collections:
            coll = coll_map.get(coll_name)
            if not coll or coll.count() == 0:
                continue
            try:
                res = coll.query(query_texts=[query], n_results=top_k)
                for i in range(len(res['ids'][0])):
                    results.append({
                        "collection": coll_name,
                        "document": res['documents'][0][i],
                        "metadata": res['metadatas'][0][i],
                        "distance": res['distances'][0][i] if 'distances' in res else None,
                    })
            except Exception as e:
                logger.warning(f"RAG搜索失败 [{coll_name}]: {e}")

        # 按相似度排序（distance越小越相关）
        results.sort(key=lambda x: x.get('distance', 999))
        return results[:top_k * len(collections)]

    def search_for_stock(self, code, name, signals, top_k=3):
        """针对股票分析的专用搜索

        Returns: 格式化的上下文字符串，可直接注入prompt
        """
        query = f"{name}({code}) 股票分析 信号:{', '.join(signals)}"
        results = self.search(query, top_k=top_k)

        if not results:
            return ""

        context = "\n## 相关历史经验（语义检索）\n"
        for r in results:
            meta = r['metadata']
            date = meta.get('date', '')
            stock_name = meta.get('name', '')
            dist = r.get('distance', 0)
            doc = r['document'][:200]
            context += f"- [{date}] {stock_name}: {doc}\n"
        return context

    # ========== 全量重建 ==========

    def rebuild_index(self):
        """从源文件全量重建索引（启动时调用）"""
        import prediction_tracker

        logger.info("开始重建RAG索引...")

        # 索引预测记录
        try:
            preds = prediction_tracker._load()
            for p in preds:
                self.index_prediction(p)
            logger.info(f"已索引 {len(preds)} 条预测记录")
        except Exception as e:
            logger.error(f"索引预测记录失败: {e}")

        # 索引交易经验
        try:
            lessons_file = os.path.join(config.DATA_DIR, 'lessons.json')
            if os.path.exists(lessons_file):
                with open(lessons_file, 'r', encoding='utf-8') as f:
                    lessons = json.load(f)
                for l in lessons:
                    self.index_lesson(l['date'], l['title'], l['content'])
                logger.info(f"已索引 {len(lessons)} 条交易经验")
        except Exception as e:
            logger.error(f"索引交易经验失败: {e}")

        # 索引AI决策日志
        try:
            decisions_dir = os.path.join(config.LOG_DIR, 'ai_decisions')
            count = 0
            if os.path.exists(decisions_dir):
                for fname in sorted(os.listdir(decisions_dir)):
                    if not fname.endswith('.jsonl'):
                        continue
                    with open(os.path.join(decisions_dir, fname), 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                d = json.loads(line)
                                self.index_decision(
                                    d.get('code', ''),
                                    d.get('name', ''),
                                    d.get('time', '')[:10],
                                    d.get('recommendation', ''),
                                    d.get('confidence', 0),
                                    d.get('reasoning', ''),
                                    d.get('key_signals', []),
                                )
                                count += 1
                            except (json.JSONDecodeError, KeyError):
                                continue
            logger.info(f"已索引 {count} 条AI决策记录")
        except Exception as e:
            logger.error(f"索引AI决策失败: {e}")

        # 统计
        logger.info(f"RAG索引重建完成: "
                     f"决策{self.decisions.count()}条, "
                     f"预测{self.predictions.count()}条, "
                     f"经验{self.lessons.count()}条")


# 全局单例
_instance = None


def get_store():
    """获取 RagStore 单例"""
    global _instance
    if _instance is None:
        _instance = RagStore()
    return _instance


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    store = RagStore()
    store.rebuild_index()

    print("\n=== 测试搜索 ===")
    results = store.search("MACD金叉买入", top_k=3)
    for r in results:
        print(f"  [{r['collection']}] 距离{r.get('distance', '?'):.3f} {r['document'][:100]}")

    print("\n=== 测试股票搜索 ===")
    context = store.search_for_stock('600519', '贵州茅台', ['MACD金叉', 'RSI超卖'])
    print(context or "无结果")
