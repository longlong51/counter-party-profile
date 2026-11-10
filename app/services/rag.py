"""RAG 核心：语义分块 → 向量化入库 → 混合检索（向量 + BM25）→ RRF 融合 → 阈值拒答 → 生成。

检索流程图：
    question ──► [可选] LLM 问题改写 ──┬─► 向量召回 top20 ─┐
                                      └─► BM25 召回 top20 ─┴─► RRF(k=60) ──► top_k ──► 阈值判定 ──► LLM 生成
"""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any

import jieba

from app.core.config import settings
from app.core.logging import get_logger
from app.services.cache import cache
from app.services.llm import LLMError, llm
from app.services.vector_store import kb_store

log = get_logger(__name__)

_STOPWORDS = set(
    "的 了 和 与 及 或 是 在 对 从 为 以 其 之 而 也 都 有 无 这 那 请问 什么 如何 怎么 "
    "哪些 多少 是否 可以 需要 应该 一下 简要 解释 介绍".split()
)

# 检索改写用的同义词/领域词扩展（规则改写兜底）
_SYNONYMS = {
    "dvp": "货银对付 券款对付",
    "ccp": "中央对手方 共同对手方",
    "rrf": "倒数排名融合",
    "巨额赎回": "净赎回比例 10%",
    "担保交收": "担保交收资金 不得强制执行",
    "违约": "违约处置 担保物 互保金 风险基金",
    "大宗交易": "协议大宗交易 交易单元 约定号",
    "估值": "公允价值 估值原则 第三方估值",
}


# ============================ 分块与入库 ============================
def load_points(path: str | None = None) -> list[dict[str, Any]]:
    """读取第 1 项交付的 100 条知识点（JSONL，一行一条）。"""
    p = path or str(settings.kb_jsonl)
    points: list[dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                points.append(json.loads(line))
    return points


def _split_sentences(text: str) -> list[str]:
    """按中文句末标点与换行切句，保留标点。"""
    parts = re.split(r"(?<=[。！？；\n])", text)
    return [p.strip() for p in parts if p and p.strip()]


def build_chunks(point: dict[str, Any]) -> list[dict[str, Any]]:
    """把一条知识点切成若干语义块（目标 320 字、重叠 64 字）。

    额外把 key_points 单独成块：要点句更短、信息密度高，能显著提升精确命中率。
    """
    size = settings.chunk_size
    overlap = settings.chunk_overlap

    base_text = "\n".join([
        f"【{point.get('title', '')}】",
        str(point.get("content", "")),
    ])
    sentences = _split_sentences(base_text)

    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) <= size:
            buf += sent
        else:
            if buf:
                chunks.append(buf)
            buf = buf[-overlap:] + sent if overlap and buf else sent
    if buf.strip():
        chunks.append(buf)

    key_points = point.get("key_points") or []
    if isinstance(key_points, list):
        for kp in key_points:
            if kp and len(str(kp)) > 8:
                chunks.append(f"【要点】{point.get('title', '')}：{kp}")

    metas: list[dict[str, Any]] = []
    for idx, _ in enumerate(chunks):
        metas.append({
            "source": "kb",
            "knowledge_id": str(point.get("id", "")),
            "title": str(point.get("title", ""))[:200],
            "category": str(point.get("category", "")),
            "sub_category": str(point.get("sub_category", "")),
            "source_name": str(point.get("source_name", ""))[:120],
            "source_url": str(point.get("source_url", ""))[:300],
            "publish_date": str(point.get("publish_date", "")),
            "effective_date": str(point.get("effective_date", "")),
            "counterparty_relevance": str(point.get("counterparty_relevance", "")),
            "chunk_index": str(idx),
        })
    return [{"id": f"{point.get('id', 'unk')}#{i}", "content": c, "metadata": metas[i]}
            for i, c in enumerate(chunks)]


def ingest(force: bool = False) -> dict[str, Any]:
    """全量重建知识库索引（幂等：先按 source=kb 清理再写入）。"""
    t0 = time.time()
    points = load_points()
    if not points:
        return {"indexed_chunks": 0, "collection_count": 0, "elapsed_ms": 0,
                "message": "knowledge_points.jsonl 为空"}

    if force or kb_store.count() == 0:
        kb_store.delete_where({"source": "kb"})

    all_chunks: list[dict[str, Any]] = []
    for p in points:
        all_chunks.extend(build_chunks(p))

    indexed = kb_store.add(
        ids=[c["id"] for c in all_chunks],
        documents=[c["content"] for c in all_chunks],
        metadatas=[c["metadata"] for c in all_chunks],
    )

    # 索引变化后清掉 BM25 缓存
    cache.delete("rag:bm25:_sig")
    _BM25_CACHE.clear()

    return {
        "indexed_chunks": indexed,
        "collection_count": kb_store.count(),
        "points": len(points),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "degraded": kb_store.degraded,
    }


# ============================ 关键词路：BM25 ============================
def tokenize(text: str) -> list[str]:
    """jieba 分词 + 去停用词 + 保留英文/数字。"""
    tokens = [t.strip().lower() for t in jieba.lcut(text)]
    return [t for t in tokens if t and t not in _STOPWORDS and len(t) > 1]


class BM25:
    """标准 BM25（k1=1.5, b=0.75），语料为知识库全部 chunk。"""

    def __init__(self, corpus: list[dict[str, Any]]) -> None:
        self.docs = corpus
        self.tokens = [tokenize(d["document"]) for d in corpus]
        self.doc_len = [len(t) for t in self.tokens]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.n = len(self.tokens)
        self.df: dict[str, int] = {}
        self.tf: list[dict[str, int]] = []
        for toks in self.tokens:
            freq: dict[str, int] = {}
            for t in toks:
                freq[t] = freq.get(t, 0) + 1
            self.tf.append(freq)
            for t in freq:
                self.df[t] = self.df.get(t, 0) + 1

    def idf(self, term: str) -> float:
        n = self.n
        df = self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, top_k: int = 20) -> list[dict[str, Any]]:
        q_terms = tokenize(query)
        if not q_terms or not self.docs:
            return []
        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for i, freq in enumerate(self.tf):
            s = 0.0
            dl = self.doc_len[i] or 1
            for term in q_terms:
                f = freq.get(term, 0)
                if not f:
                    continue
                s += self.idf(term) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / (self.avg_len or 1)))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        out = []
        for s, i in scored[:top_k]:
            d = self.docs[i]
            out.append({"id": d["id"], "document": d["document"],
                        "metadata": d["metadata"], "bm25": round(s, 4)})
        return out


_BM25_CACHE: dict[str, Any] = {}


def _get_bm25() -> BM25 | None:
    """懒加载 BM25，按知识库条数做失效判断。"""
    key = str(kb_store.count())
    if _BM25_CACHE.get("key") == key and _BM25_CACHE.get("index") is not None:
        return _BM25_CACHE["index"]
    corpus = kb_store.get_all(where={"source": "kb"})
    if not corpus:
        return None
    index = BM25(corpus)
    _BM25_CACHE.update({"key": key, "index": index})
    return index


# ============================ 问题改写 ============================
def rewrite_query(question: str) -> str:
    """把口语化问题改写为检索式。

    优先用 LLM；LLM 不可用或超时则走规则（去停用词 + 领域同义词扩展）。
    """
    rule_based = _rule_rewrite(question)
    try:
        rewritten = llm.chat(
            "你是检索查询改写器。把用户的金融问题改写为一行检索式：保留全部实体、"
            "术语、数字与日期，补充同义术语，去掉寒暄。只输出改写结果，不要解释。",
            question, temperature=0.1, max_tokens=120,
        ).strip().splitlines()[0]
        if 3 <= len(rewritten) <= 300:
            return rewritten
    except Exception as exc:  # noqa: BLE001 - 改写失败不影响主链路
        log.debug("问题改写降级为规则：%s", exc)
    return rule_based


def _rule_rewrite(question: str) -> str:
    tokens = tokenize(question)
    expanded = [question]
    for t in tokens:
        for key, syn in _SYNONYMS.items():
            if key in question.lower() or key in tokens:
                expanded.append(syn)
                break
    return " ".join(dict.fromkeys(expanded))


# ============================ 混合检索 + RRF ============================
def retrieve(question: str, top_k: int | None = None,
             rewritten: str | None = None) -> dict[str, Any]:
    """双路召回 + RRF 融合。"""
    top_k = top_k or settings.retrieval_top_k
    query = rewritten or rewrite_query(question)

    vector_hits = kb_store.query(query, top_k=settings.recall_size, where={"source": "kb"})
    bm25 = _get_bm25()
    keyword_hits = bm25.score(query, top_k=settings.recall_size) if bm25 else []

    # ---- RRF 融合：score = Σ 1/(k + rank) ----
    k = settings.rrf_k
    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(vector_hits, start=1):
        item = fused.setdefault(hit["id"], {**hit, "rrf": 0.0, "ranks": {}})
        item["rrf"] += 1.0 / (k + rank)
        item["ranks"]["vector"] = rank
        item["similarity"] = hit.get("similarity", 0.0)
    for rank, hit in enumerate(keyword_hits, start=1):
        item = fused.setdefault(hit["id"], {**hit, "rrf": 0.0, "ranks": {}})
        item["rrf"] += 1.0 / (k + rank)
        item["ranks"]["keyword"] = rank
        item["bm25"] = hit.get("bm25", 0.0)

    ranked = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)[:top_k]
    best_sim = max([h.get("similarity", 0.0) for h in vector_hits], default=0.0)
    best_bm25 = max([h.get("bm25", 0.0) for h in keyword_hits], default=0.0)

    return {
        "query": query,
        "chunks": ranked,
        "vector_hits": len(vector_hits),
        "keyword_hits": len(keyword_hits),
        "fused": len(fused),
        "best_similarity": round(best_sim, 4),
        "best_bm25": round(best_bm25, 4),
        "grounded": (best_sim >= settings.min_cosine) or
                    (best_bm25 >= settings.min_bm25 and best_sim >= settings.weak_similarity),
    }


# ============================ 生成 ============================
_SYSTEM_QA = (
    "你是金融市场交易与结算领域的知识助手，服务对象是交易员与风险管理岗。\n"
    "规则：\n"
    "1. 只依据【参考资料】回答，不得引入材料之外的事实、数据或案例；\n"
    "2. 每个结论后用 [来源: 文档标题] 标注出处；\n"
    "3. 材料不足以回答时，明确说明缺什么，不要猜测；\n"
    "4. 涉及金额、期限、比例、日期等，必须与材料完全一致；\n"
    "5. 回答用中文，先给结论，再给 2-4 条要点，控制在 350 字以内。"
)


def answer(question: str, top_k: int | None = None,
           use_rewrite: bool = True) -> dict[str, Any]:
    """知识问答主入口：检索 → 阈值判定 → LLM 生成（带引用）。"""
    t0 = time.time()
    retrieval = retrieve(question, top_k=top_k,
                         rewritten=rewrite_query(question) if use_rewrite else None)

    citations = [
        {
            "doc_id": c["id"],
            "knowledge_id": c["metadata"].get("knowledge_id", ""),
            "title": c["metadata"].get("title", ""),
            "source_name": c["metadata"].get("source_name", ""),
            "source_url": c["metadata"].get("source_url", ""),
            "category": c["metadata"].get("category", ""),
            "score": round(c["rrf"], 6),
            # 命中片段前 200 字：前端可展开查看，评测时用于校验要点是否被召回
            "snippet": c["document"][:400],
        }
        for c in retrieval["chunks"]
    ]

    # ---- 阈值拒答：两路都很弱时不调用大模型，避免幻觉 ----
    if not retrieval["grounded"] or not retrieval["chunks"]:
        return {
            "answer": "知识库暂无相关信息",
            "grounded": False,
            "citations": [],
            "retrieval": {
                "vector_hits": retrieval["vector_hits"],
                "keyword_hits": retrieval["keyword_hits"],
                "fused": retrieval["fused"],
                "best_similarity": retrieval["best_similarity"],
                "best_bm25": retrieval["best_bm25"],
            },
            "latency_ms": int((time.time() - t0) * 1000),
        }

    # ---- 命中答案缓存（同问题直接返回，省 token）----
    cache_key = f"qa:answer:{abs(hash((question, top_k))) % 10**12}"
    cached = cache.get_json(cache_key)
    if cached:
        cached["cached"] = True
        cached["latency_ms"] = int((time.time() - t0) * 1000)
        return cached

    context = "\n\n".join(
        f"[资料{i + 1} | {c['metadata'].get('title', '')} | "
        f"来源:{c['metadata'].get('source_name', '')} | 生效:{c['metadata'].get('effective_date', '')}]\n"
        f"{c['document']}"
        for i, c in enumerate(retrieval["chunks"])
    )
    user_prompt = f"【参考资料】\n{context}\n\n【用户问题】\n{question}"

    try:
        text = llm.chat(_SYSTEM_QA, user_prompt, temperature=0.2, max_tokens=900)
    except LLMError as exc:
        # LLM 不可用：直接给出检索到的原文摘录，保证可用性
        log.warning("生成失败，返回抽取式答案：%s", exc)
        text = "（大模型不可用，以下为知识库原文摘录）\n" + "\n".join(
            f"- {c['metadata'].get('title', '')}：{c['document'][:120]}…"
            for c in retrieval["chunks"][:3]
        )

    result = {
        "answer": text,
        "grounded": True,
        "citations": citations,
        "retrieval": {
            "vector_hits": retrieval["vector_hits"],
            "keyword_hits": retrieval["keyword_hits"],
            "fused": retrieval["fused"],
            "best_similarity": retrieval["best_similarity"],
            "best_bm25": retrieval["best_bm25"],
            "query_used": retrieval["query"],
        },
        "latency_ms": int((time.time() - t0) * 1000),
    }
    cache.set_json(cache_key, result, ttl=settings.answer_cache_ttl)
    return result


def stats() -> dict[str, Any]:
    """知识库统计：条数、chunk 数、主题分布。"""
    points = load_points()
    cats: dict[str, int] = {}
    for p in points:
        cats[str(p.get("category", "未分类"))] = cats.get(str(p.get("category", "未分类")), 0) + 1
    return {
        "documents": len(points),
        "chunks": kb_store.count(),
        "categories": cats,
        "degraded": kb_store.degraded,
    }
