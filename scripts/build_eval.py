"""生成知识问答评测集（eval/qa_eval_set.jsonl）。

策略：
- 从 100 条知识点中按主题分层抽样 27 条，自动生成「问题 + 期望命中条款 + 关键词」；
- 另手工补充 3 条**不可答用例**（知识库外话题），用于检验拒答能力；
- 关键词取该条 key_points 中最长的一个语义片段，用于校验答案是否真的覆盖要点。

用法：python scripts/build_eval.py
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402

EVAL_PATH = ROOT / "eval" / "qa_eval_set.jsonl"

# 每个主题的问题模板与用例类型
TEMPLATES = {
    "A": ("{title}的具体规则是什么？", "fact"),
    "B": ("{title}的核心要求有哪些？", "fact"),
    "C": ("{title}情形下，违约如何处置？", "summary"),
    "D": ("{title}是怎么规定的？", "fact"),
    "E": ("{title}对金融机构提出了什么要求？", "counterparty"),
    "F": ("{title}应当如何管理或计量？", "summary"),
}

REJECT_CASES = [
    {"question": "预测一下中国银行下周的股价涨跌。", "reason": "股价预测不在知识库范围"},
    {"question": "帮我推荐三只今年收益最高的公募基金。", "reason": "投资推荐不在知识库范围"},
    {"question": "茅台 2025 年的营业收入是多少？", "reason": "个股财务数据不在知识库范围"},
]


def pick_keyword(point: dict) -> str:
    """从正文中取一个「可验证的覆盖片段」作为关键词。

    取法：优先含数字/百分比/英文缩写的分句（如 DVP、10%、3 个工作日），
    否则取正文最长分句的中段 10 字。要求是正文原文，保证能用于校验召回覆盖。
    """
    content = str(point.get("content", ""))
    clauses = [s.strip() for s in re.split(r"[，。；、：]", content) if len(s.strip()) >= 8]

    for cl in clauses:
        if re.search(r"[A-Z]{2,6}|\d+(\.\d+)?%|\d+(万元|亿元|元|个工作日|个交易日|天|年)", cl):
            return cl[:20]

    if clauses:
        longest = max(clauses, key=len)
        start = min(len(longest) // 3, max(0, len(longest) - 10))
        return longest[start:start + 10]
    return str(point.get("title", ""))[:12]


def main() -> None:
    with open(settings.kb_jsonl, "r", encoding="utf-8") as f:
        points = [json.loads(line) for line in f if line.strip()]

    random.seed(42)
    by_cat: dict[str, list[dict]] = {}
    for p in points:
        by_cat.setdefault(str(p.get("id", "X"))[0], []).append(p)

    # 分层配额：A 8、B 6、C 4、D 4、E 4、F 1 = 27
    quota = {"A": 8, "B": 6, "C": 4, "D": 4, "E": 4, "F": 1}
    cases: list[dict] = []
    idx = 0
    for cat, n in quota.items():
        pool = by_cat.get(cat, [])
        for p in random.sample(pool, min(n, len(pool))):
            idx += 1
            tmpl, kind = TEMPLATES.get(cat, ("{title}是什么？", "fact"))
            cases.append({
                "id": f"Q{idx:02d}",
                "question": tmpl.format(title=p.get("title", "")),
                "type": kind,
                "expected_ids": [p.get("id")],
                "keywords": [pick_keyword(p)],
                "note": p.get("title", ""),
            })

    for i, rc in enumerate(REJECT_CASES, start=len(cases) + 1):
        cases.append({
            "id": f"Q{i:02d}",
            "question": rc["question"],
            "type": "reject",
            "expected_ids": [],
            "keywords": [],
            "note": rc["reason"],
        })

    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVAL_PATH, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"已生成 {len(cases)} 条评测用例 -> {EVAL_PATH}")


if __name__ == "__main__":
    main()
