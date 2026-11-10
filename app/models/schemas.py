"""所有接口的请求/响应模型（Pydantic v2）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------- 通用 ----------------
class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------- 会话 ----------------
class SessionCreateIn(BaseModel):
    title: str = "新会话"
    user_id: str = "anonymous"


class SessionOut(BaseModel):
    session_id: str
    title: str
    created_at: str


class ChatMessageIn(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    content: str = Field(..., min_length=1, max_length=4000)
    stream: bool = False


class ChatMessageOut(BaseModel):
    reply: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0


# ---------------- 知识库 ----------------
class KnowledgeAskIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    top_k: int = 6
    rewrite: bool = True


class ReindexIn(BaseModel):
    force: bool = False


class EvalIn(BaseModel):
    limit: int = 30


# ---------------- 文档 ----------------
class SummarizeIn(BaseModel):
    focus: str = "综合"


# ---------------- 对手方 ----------------
class IntakeIn(BaseModel):
    session_id: str = ""
    text: str = Field(..., min_length=5, max_length=4000)


class IntakeConfirmIn(BaseModel):
    thread_id: str
    action: str = Field("confirm", pattern="^(confirm|cancel)$")
    patched_fields: dict[str, Any] = Field(default_factory=dict)


class CommentIn(BaseModel):
    session_id: str = ""
    cp_id: str = ""
    style: str = ""
    role: str = ""
    extra: str = ""
    ttl_days: int = 30


class SyncIn(BaseModel):
    cp_id: str


# ---------------- 报告 ----------------
class ReportGenerateIn(BaseModel):
    counterparty: str = Field(..., min_length=2, max_length=80)
    session_id: str = ""
    max_retry: int = 2
    include_docs: bool = True
