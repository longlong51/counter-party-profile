"""记忆服务：短期滑动窗口 + 摘要压缩 + 带时效的长期记忆。

- 短期记忆：取会话最近 N 轮原始消息；超过阈值时把更早内容压成摘要（session_summary）。
- 长期记忆：用户指定的风格/角色/关注点，写入 LT_MEMORY（带 expire_at）；
  结构化内容落关系库，非结构化原话落 Chroma（counterparty_memory collection）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.db import db, now_str
from app.services.llm import LLMError, llm
from app.services.vector_store import memory_store

log = get_logger(__name__)


# ================= 短期记忆 =================
def get_short_memory(session_id: str) -> dict[str, Any]:
    """返回「摘要 + 最近 N 轮对话」，并在超过阈值时触发压缩。"""
    messages = db.list_messages(session_id, limit=200)
    session = db.get_session(session_id) or {}
    summary = str(session.get("summary") or "")

    turns = len([m for m in messages if m.get("role") == "user"])
    if turns > settings.summary_trigger_turns:
        summary = _compress(session_id, messages, summary)
        messages = messages[-settings.short_window_turns * 2:]

    return {
        "summary": summary,
        "messages": messages[-settings.short_window_turns * 2:],
        "turns": turns,
        "window": settings.short_window_turns,
    }


def _compress(session_id: str, messages: list[dict[str, Any]], old_summary: str) -> str:
    """把早期对话压缩为 ≤200 字摘要（LLM 不可用时用拼接截断兜底）。"""
    early = messages[:-settings.short_window_turns * 2]
    if not early:
        return old_summary
    raw = "\n".join(f"{m.get('role')}: {str(m.get('content'))[:200]}" for m in early)[-4000:]
    try:
        new_summary = llm.chat(
            "你是会话记忆压缩器。把以下对话压缩成不超过 200 字的事实摘要，"
            "保留对手方名称、金额、评级、结论等关键事实，去掉寒暄。",
            f"已有摘要：{old_summary or '无'}\n\n新增对话：\n{raw}",
            temperature=0.2, max_tokens=300,
        ).strip()[:300]
    except LLMError as exc:
        log.warning("摘要压缩降级：%s", exc)
        new_summary = (old_summary + "；" + raw[:160])[-260:]

    db.set_session_summary(session_id, new_summary)
    return new_summary


# ================= 长期记忆 =================
def add_long_memory(session_id: str, cp_id: str, mem_type: str,
                    content: str, ttl_days: int | None = None) -> dict[str, Any]:
    """写入长期记忆：关系库存结构化标签，向量库存原始语义。"""
    ttl = ttl_days or settings.long_memory_ttl_days
    rec = db.add_lt_memory(session_id, cp_id, mem_type, content, ttl)

    # 非结构化原话进向量库，便于后续语义召回
    try:
        memory_store.add(
            ids=[rec["mem_id"]],
            documents=[content],
            metadatas=[{
                "source": "lt_memory", "session_id": session_id, "cp_id": cp_id,
                "mem_type": mem_type, "expire_at": rec["expire_at"], "created_at": now_str(),
            }],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("长期记忆向量化失败：%s", exc)
    return rec


def get_long_memory(session_id: str, cp_id: str | None = None) -> list[dict[str, Any]]:
    """取未过期的长期记忆，并惰性刷新（last_refreshed 超过 24h 时重算摘要）。"""
    rows = db.list_lt_memory(session_id, cp_id, valid_only=True)
    refreshed: list[dict[str, Any]] = []
    for r in rows:
        last = str(r.get("last_refreshed") or "")
        try:
            stale = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                     ).total_seconds() > settings.long_memory_refresh_hours * 3600
        except ValueError:
            stale = True
        if stale:
            # 轻量刷新：重写 last_refreshed（真正的内容重建留给离线任务，避免请求链路过重）
            db.execute(
                "UPDATE LT_MEMORY SET LAST_REFRESHED = :ts, VERSION = :v WHERE MEM_ID = :mid",
                {"ts": now_str(), "v": str(int(r.get("version") or 1) + 1), "mid": r["mem_id"]},
            )
            r["last_refreshed"] = now_str()
        refreshed.append(r)
    return refreshed


def search_long_memory(query: str, session_id: str, top_k: int = 3) -> list[dict[str, Any]]:
    """语义召回长期记忆（只保留未过期项）。"""
    hits = memory_store.query(query, top_k=top_k * 2, where={"session_id": session_id})
    now = now_str()
    return [h for h in hits if str((h.get("metadata") or {}).get("expire_at", "9999")) >= now][:top_k]


# ================= 评论上下文组装 =================
def build_comment_context(session_id: str, cp_id: str = "") -> dict[str, Any]:
    """把短期记忆、长期记忆、对手方档案组装成评论生成的上下文。"""
    short = get_short_memory(session_id)
    long_terms = get_long_memory(session_id, cp_id or None)

    profile_text = ""
    if cp_id:
        cp = db.get_counterparty(cp_id)
        if cp:
            profile_text = (
                f"名称：{cp.get('full_name')}；类型：{cp.get('inst_type')}；"
                f"评级：{(db.list_ratings(cp_id) or [{}])[0].get('issuer_rating', '')}；"
                f"风险等级：{cp.get('risk_level')}"
            )

    return {
        "summary": short["summary"],
        "messages": short["messages"],
        "turns": short["turns"],
        "long_terms": long_terms,
        "profile_text": profile_text,
    }
