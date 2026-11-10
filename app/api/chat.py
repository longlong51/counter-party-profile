"""会话与对话接口。

对话路由策略：
    1) 默认走 RAG 知识问答（带引用）；
    2) 知识库召回不足时，用通用对话模型回答，并明确提示「知识库无相关内容」；
    3) 流式接口使用 SSE，便于前端逐字渲染。
"""
from __future__ import annotations

import json
import time
from typing import Any, Generator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.prompts import CHAT_SYSTEM
from app.core.logging import get_logger
from app.models.schemas import (ChatMessageIn, ChatMessageOut, SessionCreateIn, SessionOut)
from app.services.db import db
from app.services.llm import LLMError, llm
from app.services.memory import get_short_memory
from app.services.rag import answer, retrieve

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = get_logger(__name__)


@router.post("/sessions", response_model=SessionOut, summary="新建会话")
def create_session(body: SessionCreateIn) -> SessionOut:
    rec = db.create_session(title=body.title, user_id=body.user_id)
    return SessionOut(**rec)


@router.get("/sessions", summary="会话列表")
def list_sessions(limit: int = 20) -> dict[str, Any]:
    return {"items": db.list_sessions(limit=limit)}


@router.get("/sessions/{session_id}/messages", summary="会话消息")
def list_messages(session_id: str, limit: int = 100) -> dict[str, Any]:
    if not db.get_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"items": db.list_messages(session_id, limit=limit)}


@router.delete("/sessions/{session_id}", summary="删除会话")
def delete_session(session_id: str) -> dict[str, Any]:
    db.delete_session(session_id)
    return {"deleted": True}


def _compose_reply(session_id: str, content: str) -> dict[str, Any]:
    """非流式对话：先 RAG，再兜底通用对话。"""
    t0 = time.time()
    rag_result = answer(content)
    citations = rag_result.get("citations", [])

    if rag_result.get("grounded"):
        reply = rag_result["answer"]
    else:
        # 知识库无相关内容：用短期记忆 + 通用提示回答
        mem = get_short_memory(session_id)
        history = "\n".join(f"{m['role']}: {m['content'][:120]}" for m in mem["messages"][-6:])
        try:
            reply = llm.chat(
                CHAT_SYSTEM + "\n注意：知识库未检索到相关内容，请基于通用知识谨慎回答，并说明这一情况。",
                f"对话历史：\n{history}\n\n用户问题：{content}",
                temperature=0.3, max_tokens=600,
            )
        except LLMError:
            reply = "知识库暂无相关信息（大模型当前不可用，请稍后重试或直接提问与交易/结算规则相关的问题）。"

    db.add_message(session_id, "assistant", reply,
                   meta=json.dumps({"citations": citations, "grounded": rag_result.get("grounded")},
                                    ensure_ascii=False))
    return {
        "reply": reply,
        "citations": citations,
        "retrieval": rag_result.get("retrieval", {}),
        "latency_ms": int((time.time() - t0) * 1000),
    }


@router.post("/messages", response_model=ChatMessageOut, summary="发送消息（非流式）")
def send_message(body: ChatMessageIn) -> ChatMessageOut:
    if not db.get_session(body.session_id):
        raise HTTPException(status_code=404, detail="会话不存在，请先创建会话")
    db.add_message(body.session_id, "user", body.content)
    return ChatMessageOut(**_compose_reply(body.session_id, body.content))


@router.post("/messages/stream", summary="发送消息（SSE 流式）")
def send_message_stream(body: ChatMessageIn) -> StreamingResponse:
    if not db.get_session(body.session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    db.add_message(body.session_id, "user", body.content)
    retrieval = retrieve(body.content)
    context = "\n\n".join(
        f"[{c['metadata'].get('title', '')}] {c['document']}" for c in retrieval["chunks"]
    )
    citations = [
        {"doc_id": c["id"], "title": c["metadata"].get("title", ""),
         "source_name": c["metadata"].get("source_name", ""),
         "source_url": c["metadata"].get("source_url", ""), "score": round(c["rrf"], 6)}
        for c in retrieval["chunks"]
    ]

    def event_stream() -> Generator[str, None, None]:
        full: list[str] = []
        try:
            if not retrieval["grounded"]:
                raise LLMError("知识库召回不足")
            system = (
                CHAT_SYSTEM + "\n只依据给定参考资料回答，并在结论后标注 [来源: 标题]。"
            )
            user = f"【参考资料】\n{context}\n\n【问题】\n{body.content}"
            for delta in llm.stream(system, user, max_tokens=800):
                full.append(delta)
                yield f"event: delta\ndata: {json.dumps({'text': delta}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - 流式失败退回一次性结果
            log.warning("流式失败，退回非流式：%s", exc)
            fallback = _compose_reply(body.session_id, body.content)
            yield f"event: delta\ndata: {json.dumps({'text': fallback['reply']}, ensure_ascii=False)}\n\n"
            full = [fallback["reply"]]

        text = "".join(full)
        if text:
            db.add_message(body.session_id, "assistant", text,
                           meta=json.dumps({"citations": citations}, ensure_ascii=False))
        yield "event: done\ndata: " + json.dumps(
            {"citations": citations, "grounded": retrieval["grounded"]}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
