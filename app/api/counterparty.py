"""对手方接口：对话录入（中断确认）、列表/详情、评论生成、外部数据同步。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.agents.intake_graph import resume_intake, start_intake
from app.agents.prompts import COMMENT_SYSTEM
from app.core.logging import get_logger
from app.models.schemas import CommentIn, IntakeConfirmIn, IntakeIn, SyncIn
from app.services.db import db
from app.services.llm import LLMError, llm
from app.services.mcp_client import mcp
from app.services.memory import (add_long_memory, build_comment_context,
                                 get_long_memory, get_short_memory)

router = APIRouter(prefix="/api/counterparty", tags=["counterparty"])
log = get_logger(__name__)


@router.post("/intake", summary="对话录入对手方（中断，等待确认）")
def intake(body: IntakeIn) -> dict[str, Any]:
    return start_intake(session_id=body.session_id, text=body.text)


@router.post("/intake/confirm", summary="确认/修正/取消录入")
def intake_confirm(body: IntakeConfirmIn) -> dict[str, Any]:
    result = resume_intake(body.thread_id, body.action, body.patched_fields)
    if result.get("status") == "failed":
        raise HTTPException(status_code=422, detail=result)
    return result


@router.get("", summary="对手方列表")
def list_cp(keyword: str = "", limit: int = 20) -> dict[str, Any]:
    return {"items": db.list_counterparties(keyword=keyword, limit=limit)}


@router.get("/{cp_id}", summary="对手方详情")
def get_cp(cp_id: str) -> dict[str, Any]:
    cp = db.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(status_code=404, detail="对手方不存在")
    return {
        "profile": cp,
        "rating": db.list_ratings(cp_id),
        "finance": db.list_finance(cp_id),
        "risks": db.list_risk_events(cp_id),
        "comments": db.list_comments(cp_id),
    }


@router.post("/sync", summary="从 MCP 同步对手方数据")
def sync_cp(body: SyncIn) -> dict[str, Any]:
    cp = db.get_counterparty(body.cp_id)
    if not cp:
        raise HTTPException(status_code=404, detail="对手方不存在")
    return mcp.sync(body.cp_id, cp.get("full_name") or "")


@router.post("/comment", summary="生成对手方评论（短期滑窗 + 带时效长期记忆）")
def comment(body: CommentIn) -> dict[str, Any]:
    # 1) 把用户指定的风格/角色写入长期记忆（带 TTL），供后续复用
    if body.style:
        add_long_memory(body.session_id, body.cp_id, "style", f"风格偏好：{body.style}",
                        ttl_days=body.ttl_days)
    if body.role:
        add_long_memory(body.session_id, body.cp_id, "role", f"角色视角：{body.role}",
                        ttl_days=body.ttl_days)
    if body.extra:
        add_long_memory(body.session_id, body.cp_id, "note", body.extra,
                        ttl_days=body.ttl_days)

    ctx = build_comment_context(body.session_id, body.cp_id)
    long_terms = ctx["long_terms"]
    style_used = body.style or next(
        (str(t["content"]).replace("风格偏好：", "") for t in long_terms if t.get("mem_type") == "style"),
        "中性专业",
    )
    role_used = body.role or next(
        (str(t["content"]).replace("角色视角：", "") for t in long_terms if t.get("mem_type") == "role"),
        "交易员",
    )

    history = "\n".join(f"{m['role']}: {str(m['content'])[:150]}" for m in ctx["messages"])
    long_text = "\n".join(f"- [{t.get('mem_type')}] {t.get('content')}" for t in long_terms[:6])

    user_prompt = (
        f"【对手方档案】\n{ctx['profile_text'] or '未提供结构化档案'}\n\n"
        f"【会话摘要】\n{ctx['summary'] or '无'}\n\n"
        f"【近期对话（滑动窗口 {ctx['window'] if 'window' in ctx else ''}）】\n{history or '无'}\n\n"
        f"【长期偏好（带时效）】\n{long_text or '无'}\n\n"
        f"【本次要求】风格：{style_used}；角色视角：{role_used}"
    )

    degraded = False
    try:
        text = llm.chat(COMMENT_SYSTEM, user_prompt, temperature=0.4, max_tokens=700)
        text = text[-800:]
    except LLMError as exc:
        log.warning("评论生成降级：%s", exc)
        degraded = True
        text = (f"{role_used}视角下对对手方的简要评论：当前掌握信息有限，"
                f"建议先完成主体与评级核查。已知风险等级："
                f"{(db.get_counterparty(body.cp_id) or {}).get('risk_level', '未评估')}。"
                f"（大模型不可用，以上为模板输出）")

    if body.cp_id:
        db.add_comment(body.cp_id, body.session_id, text, style=style_used, role=role_used)
    # 评论本身作为短期记忆的一部分写入消息流，便于下一轮滑窗
    db.add_message(body.session_id, "assistant", f"[对手方评论] {text}",
                   meta='{"kind":"counterparty_comment"}')

    short = get_short_memory(body.session_id)
    return {
        "comment": text,
        "style_used": style_used,
        "role_used": role_used,
        "memory_used": {
            "short_turns": short["turns"],
            "summary": short["summary"][:200],
            "long_terms": [{"type": t.get("mem_type"), "content": str(t.get("content"))[:80],
                            "expire_at": t.get("expire_at")} for t in get_long_memory(body.session_id, body.cp_id or None)],
        },
        "degraded": degraded,
    }
