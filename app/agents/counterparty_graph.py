"""四 Agent 协作生成对手方画像报告（LangGraph 状态图 + 校验回环）。

    START → agent1_fetch(MCP取数) → agent2_cache(读缓存) → agent3_generate(生成)
              ↑                                                     │
              └────────── fix_instructions（retry<2）←── agent4_verify(校验) ──► render(出PDF) → END

设计取舍：
- Agent1/2 为「数据准备」，不调用大模型，保证取数可复现；
- 评分（scores）用**规则法**计算，不让模型打分，避免"看起来专业但无依据"；
- 校验既有规则检查（确定性）也有 LLM 检查（语义一致性），规则项优先。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.prompts import (REPORT_FIX_SYSTEM, REPORT_GEN_SYSTEM, VERIFY_SYSTEM)
from app.core.config import settings
from app.core.logging import get_logger
from app.services.cache import cache
from app.services.db import db, is_placeholder, now_str
from app.services.events import bus
from app.services.llm import LLMError, llm
from app.services.mcp_client import mcp
from app.services.rag import retrieve
from app.services.report_pdf import render_report

log = get_logger(__name__)

SKILL_TEXT = ""
try:
    SKILL_TEXT = Path(settings.skill_path).read_text(encoding="utf-8")
except Exception as exc:  # noqa: BLE001
    log.warning("读取报告 Skill 失败：%s", exc)


class ReportState(TypedDict, total=False):
    counterparty: str
    cp_id: str
    session_id: str
    external: dict[str, Any]
    cache_data: dict[str, Any]
    rules: list[dict[str, Any]]
    report_md: str
    summary: str
    scores: dict[str, float]
    issues: list[dict[str, Any]]
    retries: int
    max_retry: int
    trace: list[dict[str, Any]]
    data_source: str
    fix_instructions: str
    status: str
    pdf_path: str


def _trace(state: ReportState, node: str, **detail: Any) -> list[dict[str, Any]]:
    """记录节点轨迹，便于前端展示与问题排查。"""
    entry = {"node": node, "ts": datetime.now().strftime("%H:%M:%S"), **detail}
    return list(state.get("trace", [])) + [entry]


# ==================== Agent1：MCP 取数 ====================
def agent1_fetch(state: ReportState) -> ReportState:
    name = state["counterparty"]
    t0 = time.time()
    data = mcp.fetch_counterparty(name)

    # 关键原始返回写缓存，供 Agent2/Agent4 回溯
    cache.set_json(f"cp:mcp:raw:{name}", data, ttl=6 * 3600)

    # 落库（身份/评级/财务/风险事件），保证后续查询有数据
    cp = db.upsert_counterparty({**data["identity"], "risk_level": _risk_level(data)})
    cp_id = cp["cp_id"]
    # MCP 降级时上游给的是「示例值」，这里用库内已确认的真实字段回填，
    # 保证报告里的注册资本/成立日期等以数据库为准，而不是示例数据。
    real = {
        key: cp.get(key) for key in
        ("full_name", "short_name", "credit_code", "inst_type", "reg_capital",
         "found_date", "legal_person", "listed_flag", "remark")
        if not is_placeholder(cp.get(key))
    }
    identity = {**data.get("identity", {}), **real}
    # 上游 mock 的占位字段直接清空，报告里显示「未获取」而不是「示例值」
    data["identity"] = {k: ("" if is_placeholder(v) else v) for k, v in identity.items()}
    # 评级同理：MCP 降级给的「AAA（示例）」不覆盖库内真实评级
    if is_placeholder((data.get("rating") or {}).get("issuer_rating")):
        stored = [r for r in db.list_ratings(cp_id)
                  if not is_placeholder(r.get("issuer_rating"))]
        if stored:
            # 优先选「评级机构」也是真实值的那条记录
            pick = next((r for r in stored if not is_placeholder(r.get("agency"))), stored[0])
            merged = dict(data.get("rating") or {})
            for key, value in pick.items():
                if not is_placeholder(value):
                    merged[key] = value
            data["rating"] = merged
    # 仍为占位的字段清空，报告里显示「未获取」
    data["rating"] = {k: ("" if is_placeholder(v) else v)
                      for k, v in (data.get("rating") or {}).items()}
    db.save_rating(cp_id, data["rating"])
    for f in data.get("finance") or []:
        db.save_finance(cp_id, f)
    for ev in data.get("risk_events") or []:
        db.save_risk_event(cp_id, ev)

    return {
        **state, "cp_id": cp_id, "external": data,
        "data_source": data.get("data_source", "mock"),
        "trace": _trace(state, "agent1_fetch",
                        tools=len(data.get("tools") or []),
                        risks=len(data.get("risk_events") or []),
                        source=data.get("data_source"),
                        elapsed_ms=int((time.time() - t0) * 1000)),
    }


# ==================== Agent2：读缓存 ====================
def agent2_cache(state: ReportState) -> ReportState:
    """汇总四类缓存：MCP 原始返回、会话上传文档、本地参考报告索引、历史画像。"""
    name = state["counterparty"]
    cache_data: dict[str, Any] = {"mcp_raw": cache.get_json(f"cp:mcp:raw:{name}")}

    # 会话内已解析文档
    docs: list[dict[str, Any]] = []
    session_id = state.get("session_id") or ""
    for key in cache.keys("doc:"):
        d = cache.get_json(key)
        if isinstance(d, dict) and d.get("doc_id"):
            docs.append({"doc_id": d["doc_id"], "filename": d.get("filename"),
                         "parser": d.get("parser"), "text_chars": d.get("text_chars")})
    cache_data["parsed_docs"] = docs[:10]

    # 本地参考报告索引（离线兜底：提供可引用的报告清单）
    index_path = Path(settings.reference_dir) / "INDEX.md"
    if index_path.exists():
        excerpt = index_path.read_text(encoding="utf-8", errors="ignore")[:2500]
        cache_data["local_reference_index"] = excerpt

    # 历史画像/评论
    cp_id = state.get("cp_id")
    if cp_id:
        cache_data["history_profile"] = db.get_counterparty(cp_id)
        cache_data["history_comments"] = db.list_comments(cp_id, limit=3)
        cache_data["history_reports"] = db.list_reports(limit=5)

    conflicts = _detect_conflicts(cache_data)
    cache_data["conflicts"] = conflicts

    return {**state, "cache_data": cache_data,
            "trace": _trace(state, "agent2_cache", docs=len(docs),
                            conflicts=len(conflicts))}


def _detect_conflicts(cache_data: dict[str, Any]) -> list[str]:
    """同一指标多来源不一致时全部保留并标注冲突（不擅自取舍）。"""
    conflicts: list[str] = []
    ext_rating = ((cache_data.get("mcp_raw") or {}).get("rating") or {}).get("issuer_rating")
    hist = cache_data.get("history_profile") or {}
    if ext_rating and hist.get("risk_level") and not ext_rating.startswith("示例"):
        pass  # 评级与风险等级口径不同，不构成冲突；此处保留扩展位
    if (cache_data.get("mcp_raw") or {}).get("data_source") == "mock":
        conflicts.append("当前为示例数据，需接入真实 MCP 后复核")
    return conflicts


# ==================== 评分（规则法）====================
_RATING_SCORE = {"AAA": 95, "AA+": 85, "AA": 75, "AA-": 65, "A+": 58, "A": 52, "BBB": 45}


def compute_scores(data: dict[str, Any]) -> dict[str, float]:
    ident = data.get("identity") or {}
    rating = data.get("rating") or {}
    fin = (data.get("finance") or [{}])[0]
    risks = data.get("risk_events") or []
    bonds = data.get("bonds") or []

    # 主体实力
    strength = 0
    strength += 20 if str(ident.get("listed_flag") or "").upper() == "Y" else 0
    strength += 20 if str(ident.get("reg_capital") or "").strip() else 0
    strength += 20 if str(ident.get("found_date") or "").strip() else 0
    strength += 40 if any(k in str(ident.get("inst_type") or "") for k in ("银行", "证券", "基金", "保险")) else 0
    strength = max(30, strength)  # 数据缺失时给底线分，避免 0 分误导

    # 财务稳健
    debt = _to_float(fin.get("debt_ratio"))
    if debt is None:
        fin_score = 60
    elif debt <= 0.6:
        fin_score = 90
    elif debt <= 0.8:
        fin_score = 70
    else:
        fin_score = 50

    # 信用资质
    r = str(rating.get("issuer_rating") or "").upper()
    credit = 55
    for key, val in _RATING_SCORE.items():
        if r.startswith(key):
            credit = val
            break

    # 交易活跃
    trade = 90 if len(bonds) >= 3 else (75 if bonds else 60)

    # 合规风险（事件越多分越低）
    n = len(risks)
    compliance = 90 if n == 0 else (75 if n <= 2 else (60 if n <= 4 else 40))

    return {
        "主体实力": float(strength),
        "财务稳健": float(fin_score),
        "信用资质": float(credit),
        "交易活跃": float(trade),
        "合规风险": float(compliance),
    }


def _to_float(v: Any) -> float | None:
    """把 "58.3%" / "0.583" / 58.3 统一成 0-1 的小数；无法解析返回 None。"""
    if v is None or v == "":
        return None
    s = str(v).strip().replace(",", "")
    try:
        if s.endswith("%"):
            return float(s[:-1]) / 100
        f = float(re.findall(r"-?\d+(?:\.\d+)?", s)[0])
        return f / 100 if f > 1.5 else f
    except (IndexError, ValueError):
        return None


def _risk_level(data: dict[str, Any]) -> str:
    n = len(data.get("risk_events") or [])
    rating = str((data.get("rating") or {}).get("issuer_rating") or "")
    if n >= 5 or rating.startswith(("BB", "B", "CCC")):
        return "高"
    if n >= 2:
        return "中"
    return "低"


# ==================== Agent3：生成报告 ====================
def agent3_generate(state: ReportState) -> ReportState:
    data = state.get("external") or {}
    cache_data = state.get("cache_data") or {}
    scores = compute_scores(data)
    rules = state.get("rules") or _retrieve_rules(state["counterparty"])
    fix = state.get("fix_instructions") or ""

    payload = {
        "counterparty": state["counterparty"],
        "identity": data.get("identity") or {},
        "rating": data.get("rating") or {},
        "finance": data.get("finance") or [],
        "bonds": data.get("bonds") or [],
        "risk_events": data.get("risk_events") or [],
        "news": data.get("news") or [],
        "scores": scores,
        "cache": {
            "parsed_docs": cache_data.get("parsed_docs"),
            "local_reference_index": (cache_data.get("local_reference_index") or "")[:1200],
            "conflicts": cache_data.get("conflicts"),
        },
        "rules": [{"title": r["metadata"].get("title"), "content": r["document"][:220]}
                  for r in rules[:5]],
    }
    user_prompt = (
        f"【Skill 规范】\n{SKILL_TEXT[:3000]}\n\n"
        f"【输入数据（JSON）】\n{json.dumps(payload, ensure_ascii=False, default=str)[:9000]}\n\n"
        + (f"【上一轮校验意见，必须逐条修正】\n{fix}\n" if fix else "")
        + "\n请输出报告 Markdown。"
    )

    template_mode = False
    try:
        if fix:
            report_md = llm.chat(REPORT_FIX_SYSTEM, user_prompt, temperature=0.2, max_tokens=2400)
        else:
            report_md = llm.chat(REPORT_GEN_SYSTEM, user_prompt, temperature=0.2, max_tokens=2400)
    except LLMError as exc:
        log.warning("报告生成降级为模板：%s", exc)
        template_mode = True
        report_md = _template_report(payload, scores)

    summary = _extract_summary(report_md, scores, data)
    return {
        **state, "report_md": report_md, "summary": summary, "scores": scores,
        "rules": rules, "status": "needs_review" if template_mode else "generated",
        "trace": _trace(state, "agent3_generate",
                        retry=state.get("retries", 0),
                        template_mode=template_mode,
                        chars=len(report_md)),
    }


def _retrieve_rules(counterparty: str) -> list[dict[str, Any]]:
    """检索与对手方尽调/清算/信用风险相关的知识库条款，作为报告规则依据与校验依据。"""
    q = f"{counterparty} 交易对手方 尽职调查 清算交收 中央对手方 信用风险 违约处置"
    res = retrieve(q, top_k=6, rewritten=q)
    return res.get("chunks") or []


def _extract_summary(report_md: str, scores: dict[str, float], data: dict[str, Any]) -> str:
    """从报告 Markdown 的「结论摘要」段抽取，失败则按评分规则生成。"""
    m = re.search(r"结论摘要(.*?)(?=\n#|\n二、|\n2\.)", report_md, re.S)
    if m:
        lines = [ln.strip(" -*") for ln in m.group(1).splitlines() if ln.strip()]
        if lines:
            return "\n".join(lines[:5])

    avg = sum(scores.values()) / len(scores)
    verdict = "可准入" if avg >= 80 else ("有条件准入" if avg >= 60 else "不建议准入，需人工尽调")
    return (
        f"- 合作建议：{verdict}（五维均值 {avg:.0f}）\n"
        f"- 信用资质：{(data.get('rating') or {}).get('issuer_rating') or '未获取'}\n"
        f"- 风险提示：共 {len(data.get('risk_events') or [])} 条风险事件记录\n"
        f"- 数据来源：{'示例数据（待接入真实 MCP）' if data.get('data_source') == 'mock' else 'MCP 实时取数'}"
    )


def _template_report(payload: dict[str, Any], scores: dict[str, float]) -> str:
    """LLM 不可用时的模板报告（结构完整、明确标注模板模式）。"""
    ident = payload.get("identity") or {}
    rating = payload.get("rating") or {}
    fin = (payload.get("finance") or [{}])[0]
    risks = payload.get("risk_events") or []
    avg = sum(scores.values()) / len(scores)
    verdict = "可准入" if avg >= 80 else ("有条件准入" if avg >= 60 else "不建议准入，需人工尽调")

    lines = [
        "⚠ 模板模式（大模型不可用，以下为规则生成）",
        "",
        "## 一、结论摘要",
        f"- 合作建议：{verdict}（五维均值 {avg:.0f}）",
        f"- 主体：{ident.get('full_name') or payload['counterparty']}（{ident.get('inst_type') or '类型未获取'}）",
        f"- 评级：{rating.get('issuer_rating') or '未获取'}（{rating.get('agency') or '机构未获取'}）",
        f"- 风险事件：{len(risks)} 条",
        "",
        "## 二、主体信息",
        f"- 统一社会信用代码：{ident.get('credit_code') or '未获取'}",
        f"- 注册资本：{ident.get('reg_capital') or '未获取'}",
        f"- 成立日期：{ident.get('found_date') or '未获取'}",
        f"- 法定代表人：{ident.get('legal_person') or '未获取'}",
        "",
        "## 三、评级情况",
        f"- 主体评级：{rating.get('issuer_rating') or '未获取'}；展望：{rating.get('issuer_outlook') or '未获取'}",
        f"- 债项评级：{rating.get('bond_rating') or '未获取'}",
        "",
        "## 四、财务概况",
        f"- 报告期：{fin.get('period') or '未获取'}；资产负债率：{fin.get('debt_ratio') or '未获取'}；ROE：{fin.get('roe') or '未获取'}",
        "",
        "## 五、交易与清算要点",
        "- 交易应采用 DVP（货银对付）方式，优先通过中央对手方（CCP）清算以降低双边敞口 [规则: 证券登记结算管理办法]",
        "- 对手方违约时按「担保物现金 → 互保金 → 风险基金」顺序处置 [规则: 结算风险基金管理办法]",
        "",
        "## 六、风险与合规事件",
    ]
    if risks:
        for r in risks[:8]:
            lines.append(f"- {r.get('event_type')}｜{r.get('event_date') or '日期未获取'}｜{r.get('event_desc') or ''}")
    else:
        lines.append("- 未获取到风险事件记录")
    lines.append("")
    lines.append("## 七、尽调与后续动作建议")
    lines.append("- 补充受益所有人、实际控制人与关联方材料")
    lines.append("- 按客户风险等级确定复核频率（最高风险客户每年至少审核 1 次）")
    return "\n".join(lines)


# ==================== Agent4：校验 ====================
def agent4_verify(state: ReportState) -> ReportState:
    report_md = state.get("report_md") or ""
    data = state.get("external") or {}
    rules = state.get("rules") or []

    # ---- 规则检查（确定性，优先）----
    issues: list[dict[str, Any]] = []
    name = state["counterparty"]
    if name not in report_md:
        issues.append({"level": "high", "field": "主体",
                       "problem": "报告未出现请求的对手方名称",
                       "fix": f"统一主体为「{name}」，删除其他主体内容"})

    required_sections = ["结论摘要", "主体信息", "评级情况", "财务概况", "风险与合规事件"]
    for sec in required_sections:
        if sec not in report_md:
            issues.append({"level": "medium", "field": sec,
                           "problem": f"缺少章节「{sec}」", "fix": f"补齐「{sec}」章节"})

    if (data.get("rating") or {}).get("issuer_rating") and "评级" not in report_md:
        issues.append({"level": "medium", "field": "评级",
                       "problem": "有评级数据但报告未体现", "fix": "补全评级章节与评级机构"})

    if (data.get("finance") or [{}])[0].get("debt_ratio") and "资产负债率" not in report_md:
        issues.append({"level": "medium", "field": "财务",
                       "problem": "有资产负债率数据但报告未体现", "fix": "补全财务概况"})

    if data.get("data_source") == "mock" and "示例数据" not in report_md:
        issues.append({"level": "medium", "field": "数据来源",
                       "problem": "使用示例数据但未标注", "fix": "首行标注「示例数据，待接入真实数据源」"})

    if "交易与清算要点" not in report_md:
        issues.append({"level": "low", "field": "清算",
                       "problem": "未说明清算交收安排", "fix": "补充 DVP/CCP 相关条款"})

    # 数字编造粗检：报告中出现的金额若在原始数据里找不到，提示人工核对
    for num in list(set(re.findall(r"\d+\.?\d*\s*(?:万亿|亿元|万元|%)", report_md)))[:5]:
        if num.replace(" ", "") not in json.dumps(data, ensure_ascii=False):
            issues.append({"level": "low", "field": "数字",
                           "problem": f"报告中的「{num}」无法在原始数据中核对",
                           "fix": "删除或改为「未获取」"})

    # ---- LLM 语义校验（补充）----
    try:
        data_json = json.dumps(
            {"identity": data.get("identity"), "rating": data.get("rating"),
             "finance": data.get("finance"), "risks": data.get("risk_events")},
            ensure_ascii=False, default=str,
        )[:4000]
        rules_text = "\n".join(f"- {r['metadata'].get('title')}: {r['document'][:160]}" for r in rules[:5])
        verdict = llm.chat_json(
            VERIFY_SYSTEM,
            f"【报告】\n{report_md[:7000]}\n\n【原始数据】\n{data_json}\n\n【知识库条款】\n{rules_text}",
            temperature=0.1, max_tokens=900,
        )
        for it in (verdict.get("issues") or [])[:6]:
            if isinstance(it, dict) and it.get("problem"):
                issues.append({
                    "level": str(it.get("level") or "medium"),
                    "field": str(it.get("field") or "综合"),
                    "problem": str(it.get("problem"))[:200],
                    "fix": str(it.get("fix") or "按校验意见修正")[:200],
                })
    except Exception as exc:  # noqa: BLE001 - LLM 校验失败不阻断
        log.warning("LLM 校验跳过：%s", exc)

    # 去重
    seen, uniq = set(), []
    for it in issues:
        key = (it["field"], it["problem"][:40])
        if key not in seen:
            seen.add(key)
            uniq.append(it)

    passed = not any(i["level"] == "high" for i in uniq) and len(uniq) <= 2
    return {**state, "issues": uniq,
            "trace": _trace(state, "agent4_verify", issues=len(uniq),
                            high=sum(1 for i in uniq if i["level"] == "high"),
                            passed=passed)}


def route_after_verify(state: ReportState) -> str:
    """条件边：有问题且未超重试上限 → 回炉重生；否则渲染输出。"""
    issues = state.get("issues") or []
    retries = state.get("retries", 0)
    max_retry = state.get("max_retry", settings.max_verify_retry)
    has_blocking = any(i["level"] == "high" for i in issues)

    if issues and (has_blocking or len(issues) > 2) and retries < max_retry:
        return "retry"
    return "render"


# ==================== render：出 PDF + 落库 ====================
def render_node(state: ReportState) -> ReportState:
    cp_id = state.get("cp_id") or "cp_unknown"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = str(Path(settings.reports_dir) / f"{cp_id}_{ts}.pdf")
    data = state.get("external") or {}

    payload = {
        "counterparty": state["counterparty"],
        "generated_at": now_str(),
        "data_source": "示例数据（待接入真实 MCP）" if state.get("data_source") == "mock" else "MCP 实时取数",
        "retries": state.get("retries", 0),
        "scores": state.get("scores") or {},
        "summary": state.get("summary") or "",
        "identity": data.get("identity") or {},
        "rating": data.get("rating") or {},
        "finance": data.get("finance") or [],
        "risks": data.get("risk_events") or [],
        "citations": [
            {"title": r["metadata"].get("title", ""),
             "source_name": r["metadata"].get("source_name", ""),
             "effective_date": r["metadata"].get("effective_date", "")}
            for r in (state.get("rules") or [])[:6]
        ],
        "issues": state.get("issues") or [],
        # 传整份 Markdown：PDF 渲染器据此补齐「交易与清算要点」「尽调建议」等叙述性章节
        "markdown": state.get("report_md") or "",
    }
    try:
        render_report(pdf_path, payload)
    except Exception as exc:  # noqa: BLE001 - PDF 失败不影响文本结果
        log.error("PDF 渲染失败：%s", exc)
        pdf_path = ""

    status = "needs_review" if (state.get("issues") and route_after_verify(state) == "render") else "completed"
    rec = db.save_report(
        cp_id, state["counterparty"], status, pdf_path,
        state.get("report_md") or "",
        json.dumps(state.get("issues") or [], ensure_ascii=False),
        json.dumps(state.get("scores") or {}, ensure_ascii=False),
    )
    bus.publish("report.generated", {"report_id": rec["report_id"],
                                     "counterparty": state["counterparty"],
                                     "status": status})
    return {**state, "pdf_path": pdf_path, "status": status,
            "trace": _trace(state, "render", pdf=bool(pdf_path), status=status)}


def _increment_retry(state: ReportState) -> ReportState:
    """回炉前把校验意见转成自然语言的修正指令。"""
    issues = state.get("issues") or []
    instructions = "\n".join(
        f"{i + 1}. [{it['level']}] {it['field']}：{it['problem']} → {it['fix']}"
        for i, it in enumerate(issues)
    )
    return {**state, "retries": state.get("retries", 0) + 1,
            "fix_instructions": instructions,
            "trace": _trace(state, "retry", retry=state.get("retries", 0) + 1,
                            issues=len(issues))}


# ==================== 图构建 ====================
def _build_graph():
    graph = StateGraph(ReportState)
    graph.add_node("agent1_fetch", agent1_fetch)
    graph.add_node("agent2_cache", agent2_cache)
    graph.add_node("agent3_generate", agent3_generate)
    graph.add_node("agent4_verify", agent4_verify)
    graph.add_node("retry", _increment_retry)
    graph.add_node("render", render_node)

    graph.add_edge(START, "agent1_fetch")
    graph.add_edge("agent1_fetch", "agent2_cache")
    graph.add_edge("agent2_cache", "agent3_generate")
    graph.add_edge("agent3_generate", "agent4_verify")
    graph.add_conditional_edges("agent4_verify", route_after_verify,
                                {"retry": "retry", "render": "render"})
    graph.add_edge("retry", "agent3_generate")
    graph.add_edge("render", END)
    return graph.compile()


report_graph = _build_graph()


def generate_report(counterparty: str, session_id: str = "",
                    max_retry: int | None = None) -> dict[str, Any]:
    """对外入口：跑一遍四 Agent 图，返回报告与轨迹。"""
    t0 = time.time()
    initial: ReportState = {
        "counterparty": counterparty, "session_id": session_id,
        "max_retry": max_retry if max_retry is not None else settings.max_verify_retry,
        "retries": 0, "issues": [], "trace": [],
    }
    final = report_graph.invoke(initial)

    report_id = ""
    if final.get("pdf_path"):
        report_id = Path(final["pdf_path"]).stem

    return {
        "report_id": report_id,
        "cp_id": final.get("cp_id"),
        "status": final.get("status", "completed"),
        "counterparty": counterparty,
        "markdown": final.get("report_md", ""),
        "summary": final.get("summary", ""),
        "scores": final.get("scores", {}),
        "issues": final.get("issues", []),
        "retries": final.get("retries", 0),
        "pdf_path": final.get("pdf_path", ""),
        "pdf_url": f"/api/reports/{report_id}/pdf" if report_id else "",
        "data_source": final.get("data_source", ""),
        "trace": final.get("trace", []),
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
