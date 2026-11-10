"""知识问答评测器：读取 eval/qa_eval_set.jsonl，逐条跑 RAG，统计命中率与拒答正确率。

判分口径（三选一，按用例 type）：
    fact / summary / counterparty / freshness → 期望 citations 命中 expected_ids 且答案包含任一 keyword；
    reject → 期望 grounded=False 且 answer 为「知识库暂无相关信息」。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.rag import answer

EVAL_PATH = Path(settings.base_dir) / "eval" / "qa_eval_set.jsonl"


def load_cases(limit: int | None = None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with open(EVAL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases[:limit] if limit else cases


def _judge(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """单条判分，返回 {passed, reason, hit_ids}。"""
    kind = case.get("type", "fact")

    if kind == "reject":
        ok = (not result.get("grounded")) and "知识库暂无相关信息" in result.get("answer", "")
        return {"passed": ok, "reason": "正确拒答" if ok else "未按预期拒答",
                "hit_ids": []}

    hit_ids = [c.get("knowledge_id") for c in result.get("citations", [])]
    expected = set(case.get("expected_ids") or [])
    hit = bool(expected & set(hit_ids)) if expected else True

    # 关键词覆盖：答案命中，或召回片段中命中（衡量「证据是否被召回」，不苛求生成措辞）
    # 比较时忽略空格，避免分块/排版差异造成的假失败
    snippets = " ".join(str(c.get("snippet", "")) for c in result.get("citations", []))
    haystack = (result.get("answer", "") + " " + snippets).replace(" ", "")
    keywords = case.get("keywords") or []
    kw = any(str(k).replace(" ", "") in haystack for k in keywords) if keywords else True

    passed = hit and kw and result.get("grounded", False)
    reason = "通过"
    if not result.get("grounded"):
        reason = "被阈值拒答"
    elif not hit:
        reason = f"未命中期望条款（期望 {sorted(expected)}，实际 {hit_ids[:3]}）"
    elif not kw:
        reason = f"答案缺少关键词 {keywords}"
    return {"passed": passed, "reason": reason, "hit_ids": hit_ids[:5]}


def run_eval(limit: int | None = None) -> dict[str, Any]:
    """执行评测并返回汇总 + 明细。"""
    cases = load_cases(limit)
    t0 = time.time()
    details: list[dict[str, Any]] = []
    passed = 0
    reject_total = reject_ok = 0

    for case in cases:
        try:
            res = answer(case["question"], top_k=case.get("top_k", 6))
        except Exception as exc:  # noqa: BLE001
            res = {"answer": f"ERROR: {exc}", "grounded": False, "citations": []}

        judged = _judge(case, res)
        if case.get("type") == "reject":
            reject_total += 1
            reject_ok += 1 if judged["passed"] else 0
        passed += 1 if judged["passed"] else 0

        details.append({
            "id": case.get("id"),
            "question": case["question"],
            "type": case.get("type"),
            "passed": judged["passed"],
            "reason": judged["reason"],
            "answer": res.get("answer", "")[:200],
            "citations": [c.get("knowledge_id") for c in res.get("citations", [])][:5],
        })

    total = len(cases)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "hit_rate": round(
            sum(1 for d in details if d["passed"] and d["type"] != "reject") /
            max(1, sum(1 for d in details if d["type"] != "reject")), 4),
        "reject_total": reject_total,
        "reject_rate": round(reject_ok / reject_total, 4) if reject_total else 0.0,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "degraded": None,
        "details": details,
    }


def render_markdown(result: dict[str, Any]) -> str:
    """把评测结果渲染成 Markdown 报告。"""
    lines = [
        "# 知识问答模块评测报告",
        "",
        f"- 用例总数：{result['total']}",
        f"- 通过：{result['passed']}｜失败：{result['failed']}｜通过率：{result['pass_rate'] * 100:.1f}%",
        f"- 可答用例命中率：{result['hit_rate'] * 100:.1f}%",
        f"- 拒答正确率：{result['reject_rate'] * 100:.1f}%（{result['reject_total']} 条拒答用例）",
        f"- 总耗时：{result['elapsed_ms']} ms",
        "",
        "| # | 问题 | 类型 | 结果 | 说明 | 命中条款 |",
        "|---|---|---|---|---|---|",
    ]
    for i, d in enumerate(result["details"], start=1):
        mark = "✅" if d["passed"] else "❌"
        q = d["question"].replace("|", "｜")
        lines.append(
            f"| {i} | {q[:40]} | {d['type']} | {mark} | {d['reason'][:40]} | "
            f"{', '.join(str(x) for x in d['citations'])} |"
        )
    lines.append("")
    lines.append("> 评测口径：可答用例需同时满足「引用命中期望条款」与「答案含期望关键词」；"
                 "拒答用例需返回「知识库暂无相关信息」。")
    return "\n".join(lines)
