"""FastAPI 应用入口。

职责：
    1. 统一日志与目录初始化；
    2. 挂载 5 组 API 路由；
    3. 静态页（聊天前端）托管；
    4. 统一异常体 + 健康检查（含各依赖降级状态）。
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import chat, counterparty, documents, knowledge, reports
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.services.cache import cache
from app.services.db import db
from app.services.events import bus
from app.services.llm import llm
from app.services.mcp_client import mcp
from app.services.rag import ingest, stats
from app.services.vector_store import kb_store

setup_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时确保知识库已入库（空库自动建索引），并打印降级状态。"""
    settings.ensure_dirs()
    st = stats()
    if st["chunks"] == 0:
        log.info("向量库为空，自动构建知识库索引……")
        try:
            res = ingest()
            log.info("知识库索引完成：%s", res)
        except Exception as exc:  # noqa: BLE001
            log.error("知识库构建失败：%s", exc)
    else:
        log.info("知识库已就绪：%s 条 / %s chunks", st["documents"], st["chunks"])
    log.info("依赖状态：cache=%s db=%s llm=%s mcp=%s",
             cache.backend, db.backend,
             "degraded" if llm.degraded else "ok",
             "configured" if mcp.configured else "placeholder")
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="多 Agent 智能体应用：交易知识问答 + 对手方画像报告 + 对话式录入",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------------- 路由 ----------------
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(documents.router)
app.include_router(counterparty.router)
app.include_router(reports.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """统一错误体，避免把堆栈直接暴露给前端。"""
    log.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": str(exc)[:200],
                 "detail": {"path": request.url.path}},
    )


@app.get("/health", tags=["system"], summary="健康检查")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "ts": int(time.time()),
        "components": {
            "llm": llm.health(),
            "chroma": kb_store.health(),
            "redis": cache.health(),
            "oracle": db.health(),
            "mcp": mcp.health(),
            "kafka": bus.health(),
        },
        "knowledge": stats(),
    }


@app.get("/api/meta", tags=["system"], summary="能力清单（前端展示用）")
def meta() -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "modules": [
            {"key": "rag", "title": "交易知识问答", "desc": "向量+BM25 混合检索，RRF 融合，低相关直接拒答"},
            {"key": "report", "title": "对手方画像报告", "desc": "4 Agent 协作：MCP 取数→读缓存→生成→校验回环"},
            {"key": "intake", "title": "对话录入对手方", "desc": "LLM 抽取 + 中断确认 + 落库"},
            {"key": "comment", "title": "对手方评论", "desc": "滑窗短期记忆 + 带时效长期记忆"},
            {"key": "doc", "title": "PDF/图片解析", "desc": "文本+表格抽取，扫描页视觉 OCR，结果进缓存"},
        ],
        "demo_counterparties": ["中国银行", "工商银行", "中信证券"],
    }


# ---------------- 静态前端 ----------------
_STATIC_DIR = settings.base_dir / "app" / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
