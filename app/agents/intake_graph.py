"""对话录入交易对手方：LangGraph 中断（human-in-the-loop）。

流程：
    extract(LLM 抽取字段) → confirm(interrupt 中断，把抽取结果抛给前端)
        → 用户 confirm/cancel（Command(resume)）→ persist(写库)

中断实现：LangGraph 的 ``interrupt()`` 会把当前状态存入 checkpointer，
`invoke` 返回时带 ``__interrupt__``，前端确认后再用 ``Command(resume=...)`` 从断点继续。
"""
from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agents.prompts import EXTRACT_SYSTEM
from app.core.logging import get_logger
from app.services.db import db, new_id
from app.services.events import bus
from app.services.llm import LLMError, llm
from app.services.memory import add_long_memory

log = get_logger(__name__)

_FIELDS = [
    "full_name", "short_name", "credit_code", "inst_type", "reg_capital",
    "found_date", "legal_person", "listed_flag", "issuer_rating",
    "issuer_outlook", "agency", "business", "remark",
]


class IntakeState(TypedDict, total=False):
    session_id: str
    text: str
    extracted: dict[str, Any]
    missing: list[str]
    confidence: float
    action: str
    patched_fields: dict[str, Any]
    result: dict[str, Any]


def _rule_extract(text: str) -> dict[str, Any]:
    """LLM 不可用时的规则抽取：正则抓信用代码、注册资本、评级等显性字段。

    中文描述里字段名与取值之间常有空格（「注册资本 2943 亿元」），
    因此数值类字段统一在「去空格副本」上匹配，名称类字段仍用原文。
    """
    import re

    fields: dict[str, Any] = {k: "" for k in _FIELDS}
    norm = re.sub(r"\s+", "", text)

    m = re.search(r"[0-9A-HJ-NPQRTUWXY]{18}", norm)
    if m:
        fields["credit_code"] = m.group(0)
    m = re.search(r"注册资本[约为是:：]*([0-9.,]+(?:亿|万)?元)", norm)
    if m:
        fields["reg_capital"] = m.group(1)
    m = re.search(r"(AAA|AA\+|AA-|AA|A\+|A|BBB)[级]*", norm)
    if m:
        fields["issuer_rating"] = m.group(0)
    m = re.search(r"成立于([0-9]{4}年([0-9]{1,2}月)?)", norm)
    if m:
        fields["found_date"] = m.group(1)
    # 机构类型：优先取「机构类型：xxx」显式表述，避免名称里的「中国银行」先被匹配成「银行」
    type_words = (r"(商业银行|政策性银行|城商行|农商行|金融租赁公司|财务公司|证券公司|"
                  r"基金公司|保险公司|信托公司|资产管理公司|"
                  r"银行|证券|基金|保险|信托|资产管理|企业)")
    m = re.search(r"(?:机构类型|机构性质|类型)[:：]?" + type_words, norm)
    if not m:
        m = re.search(type_words, norm)
    if m:
        fields["inst_type"] = m.group(1)
    m = re.search(r"法定代表人[：:]?([\u4e00-\u9fa5]{2,4})", norm)
    if m:
        fields["legal_person"] = m.group(1)
    m = re.search(r"评级机构[：:]?([\u4e00-\u9fa5A-Za-z]{2,20})", norm)
    if m:
        fields["agency"] = m.group(1)
    m = re.search(r"展望[：:]?(稳定|正面|负面|列入观察)", norm)
    if m:
        fields["issuer_outlook"] = m.group(1)

    # 名称：取首句，并在出现属性描述词时截断（兼容用空格分隔字段的写法）
    first = re.split(r"[，,。；;（(]", text.strip())[0]
    for kw in ("统一社会信用代码", "机构类型", "机构性质", "注册资本", "法定代表人",
               "主体评级", "评级", "成立于", "类型"):
        pos = first.find(kw)
        if pos > 0:
            first = first[:pos].strip()
    fields["full_name"] = first[:60] if first else ""
    fields["short_name"] = fields["full_name"][:20]
    return fields


def extract_node(state: IntakeState) -> IntakeState:
    """抽取字段：LLM 优先，失败走规则。"""
    text = state["text"]
    try:
        data = llm.chat_json(
            EXTRACT_SYSTEM,
            f"用户描述：\n{text}\n\n输出 JSON（字段：{', '.join(_FIELDS)}）。",
            temperature=0.1, max_tokens=800,
        )
        fields = {k: str(data.get("fields", {}).get(k, "") or "") for k in _FIELDS}
        missing = [k for k, v in fields.items() if not v]
        return {**state, "extracted": fields, "missing": missing,
                "confidence": float(data.get("confidence") or 0.6)}
    except (LLMError, ValueError, TypeError) as exc:
        log.warning("抽取降级为规则：%s", exc)
        fields = _rule_extract(text)
        return {**state, "extracted": fields,
                "missing": [k for k, v in fields.items() if not v],
                "confidence": 0.35}


def confirm_node(state: IntakeState) -> IntakeState:
    """中断点：把抽取结果抛给前端，等待用户确认/修正。"""
    decision = interrupt({
        "title": "请确认交易对手方信息",
        "extracted": state.get("extracted", {}),
        "missing": state.get("missing", []),
        "confidence": state.get("confidence", 0),
        "hint": "确认无误请选择 confirm；如需修正请在 patched_fields 中提交修改值。",
    })
    # decision 由前端通过 Command(resume=...) 传入
    if isinstance(decision, dict):
        return {
            **state,
            "action": str(decision.get("action") or "confirm"),
            "patched_fields": decision.get("patched_fields") or {},
        }
    return {**state, "action": "cancel", "patched_fields": {}}


def persist_node(state: IntakeState) -> IntakeState:
    """确认后写库；取消则不写。"""
    if state.get("action") == "cancel":
        return {**state, "result": {"status": "cancelled"}}

    fields = dict(state.get("extracted") or {})
    fields.update(state.get("patched_fields") or {})
    if not fields.get("full_name"):
        return {**state, "result": {"status": "failed",
                                    "reason": "缺少对手方名称，无法入库"}}

    cp = db.upsert_counterparty({
        "full_name": fields["full_name"],
        "short_name": fields.get("short_name") or fields["full_name"],
        "credit_code": fields.get("credit_code"),
        "inst_type": fields.get("inst_type"),
        "reg_capital": fields.get("reg_capital"),
        "found_date": fields.get("found_date"),
        "legal_person": fields.get("legal_person"),
        "listed_flag": fields.get("listed_flag"),
        "remark": fields.get("business") or fields.get("remark"),
    })
    cp_id = cp["cp_id"]

    if fields.get("issuer_rating"):
        db.save_rating(cp_id, {
            "agency": fields.get("agency"),
            "issuer_rating": fields.get("issuer_rating"),
            "issuer_outlook": fields.get("issuer_outlook"),
            "rating_date": "",
        })

    # 原始描述作为非结构化长期记忆（便于后续评论回溯）
    try:
        add_long_memory(state["session_id"], cp_id, "counterparty_description",
                        state["text"], ttl_days=90)
    except Exception as exc:  # noqa: BLE001
        log.warning("录入记忆写入失败：%s", exc)

    bus.publish("counterparty.saved", {"cp_id": cp_id, "name": cp["full_name"]})
    return {**state, "result": {"status": "saved", "cp_id": cp_id, "record": cp}}


def _build_graph():
    g = StateGraph(IntakeState)
    g.add_node("extract", extract_node)
    g.add_node("confirm", confirm_node)
    g.add_node("persist", persist_node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "confirm")
    g.add_edge("confirm", "persist")
    g.add_edge("persist", END)
    return g.compile(checkpointer=MemorySaver())


intake_graph = _build_graph()


# ---------------- 对外封装 ----------------
def start_intake(session_id: str, text: str, thread_id: str | None = None) -> dict[str, Any]:
    """启动录入流程，返回中断内容（待用户确认）。"""
    tid = thread_id or f"intake_{new_id()}"
    cfg = {"configurable": {"thread_id": tid}}
    result = intake_graph.invoke(
        {"session_id": session_id, "text": text, "action": "pending"}, config=cfg
    )
    interrupts = result.get("__interrupt__") or []
    payload = interrupts[0].value if interrupts else {}
    return {
        "status": "pending_confirm",
        "thread_id": tid,
        "extracted": payload.get("extracted", {}),
        "missing": payload.get("missing", []),
        "confidence": payload.get("confidence", 0),
    }


def resume_intake(thread_id: str, action: str,
                  patched_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """用户确认/取消后恢复图执行。"""
    cfg = {"configurable": {"thread_id": thread_id}}
    result = intake_graph.invoke(
        Command(resume={"action": action, "patched_fields": patched_fields or {}}),
        config=cfg,
    )
    return result.get("result") or {"status": "unknown",
                                    "raw": json.dumps(result, ensure_ascii=False, default=str)[:300]}
