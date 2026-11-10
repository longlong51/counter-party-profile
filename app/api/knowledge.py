"""知识库接口：问答、重建索引、统计、内置评测。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.models.schemas import EvalIn, KnowledgeAskIn, ReindexIn
from app.services.evaluator import run_eval
from app.services.rag import answer, ingest, stats

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/ask", summary="交易知识问答（混合检索 + RRF + 阈值拒答）")
def ask(body: KnowledgeAskIn) -> dict[str, Any]:
    return answer(body.question, top_k=body.top_k, use_rewrite=body.rewrite)


@router.post("/reindex", summary="重建知识库向量索引")
def reindex(body: ReindexIn) -> dict[str, Any]:
    return ingest(force=body.force)


@router.get("/stats", summary="知识库统计")
def kb_stats() -> dict[str, Any]:
    return stats()


@router.post("/eval", summary="运行知识问答评测集")
def eval_kb(body: EvalIn) -> dict[str, Any]:
    return run_eval(limit=body.limit)
