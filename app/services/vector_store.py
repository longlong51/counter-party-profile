"""ChromaDB 向量库封装。

设计要点：
- 使用 PersistentClient 落盘到 data/chroma，进程重启不丢；
- 向量由本项目的 embeddings 模块显式算出后传入（不依赖 Chroma 默认 EF，避免联网下载模型）；
- 提供 count / get_all，供 BM25 关键词路复用同一份语料。
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.embeddings import embed_batch, embed_text

log = get_logger(__name__)

KB_COLLECTION = "trading_knowledge"
CP_COLLECTION = "counterparty_memory"


class VectorStore:
    """对 Chroma 的薄封装，失败可被上层捕获后降级为内存检索。"""

    def __init__(self, collection_name: str = KB_COLLECTION) -> None:
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self.degraded = True
        self._fallback: list[dict[str, Any]] = []  # 降级时的内存存储
        self._init()

    # ---------- 初始化 ----------
    def _init(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self.degraded = False
            log.info("Chroma 就绪：%s（%s 条）", self.collection_name, self._collection.count())
        except Exception as exc:  # noqa: BLE001
            log.warning("Chroma 初始化失败，降级内存向量检索：%s", exc)

    # ---------- 写入 ----------
    def add(self, ids: list[str], documents: list[str],
            metadatas: list[dict[str, Any]]) -> int:
        """批量写入（自动生成向量）。"""
        if not ids:
            return 0
        embeddings = embed_batch(documents)
        if self.degraded:
            for i, doc, meta in zip(ids, documents, metadatas):
                self._fallback.append(
                    {"id": i, "document": doc, "metadata": meta, "embedding": embed_text(doc)}
                )
            return len(ids)

        # Chroma 对重复 id 会报错，先删后加保证幂等
        try:
            self._collection.delete(ids=ids)
        except Exception:  # noqa: BLE001 - 不存在时忽略
            pass
        self._collection.add(
            ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
        )
        return len(ids)

    # ---------- 查询 ----------
    def query(self, text: str, top_k: int = 20,
              where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """向量检索，返回 [{id, document, metadata, similarity}]，similarity ∈ [0,1]。"""
        if self.degraded:
            return self._fallback_query(text, top_k, where)

        qvec = embed_text(text)
        try:
            res = self._collection.query(
                query_embeddings=[qvec],
                n_results=max(1, top_k),
                where=where or None,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Chroma 查询失败，回落内存：%s", exc)
            return self._fallback_query(text, top_k, where)

        out: list[dict[str, Any]] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc, meta, dist in zip(ids, docs, metas, dists):
            # cosine 距离 = 1 - 相似度
            sim = max(0.0, min(1.0, 1.0 - float(dist)))
            out.append({"id": i, "document": doc, "metadata": meta or {}, "similarity": sim})
        return out

    def _fallback_query(self, text: str, top_k: int,
                        where: dict[str, Any] | None) -> list[dict[str, Any]]:
        qvec = embed_text(text)
        scored = []
        for item in self._fallback:
            meta = item["metadata"]
            if where and any(meta.get(k) != v for k, v in where.items()):
                continue
            sim = sum(a * b for a, b in zip(qvec, item["embedding"]))
            scored.append({"id": item["id"], "document": item["document"],
                           "metadata": meta, "similarity": sim})
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    # ---------- 其他 ----------
    def get_all(self, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """取出全部文档（供 BM25 构建统计量）。"""
        if self.degraded:
            return [{"id": i["id"], "document": i["document"], "metadata": i["metadata"]}
                    for i in self._fallback
                    if not where or all(i["metadata"].get(k) == v for k, v in where.items())]

        try:
            res = self._collection.get(where=where or None,
                                       include=["documents", "metadatas"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Chroma get 失败：%s", exc)
            return []
        return [
            {"id": i, "document": d, "metadata": m or {}}
            for i, d, m in zip(res.get("ids") or [], res.get("documents") or [],
                               res.get("metadatas") or [])
        ]

    def delete_where(self, where: dict[str, Any]) -> None:
        if self.degraded:
            self._fallback = [
                i for i in self._fallback
                if any(i["metadata"].get(k) != v for k, v in where.items())
            ]
            return
        try:
            self._collection.delete(where=where)
        except Exception as exc:  # noqa: BLE001
            log.warning("Chroma delete 失败：%s", exc)

    def count(self) -> int:
        if self.degraded:
            return len(self._fallback)
        try:
            return self._collection.count()
        except Exception:  # noqa: BLE001
            return 0

    def health(self) -> dict[str, Any]:
        return {"collection": self.collection_name, "count": self.count(),
                "degraded": self.degraded}


kb_store = VectorStore(KB_COLLECTION)
memory_store = VectorStore(CP_COLLECTION)
