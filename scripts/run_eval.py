"""评测执行脚本：跑评测集并输出 eval/eval_report.md。

用法：python scripts/run_eval.py [--limit 30]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.logging import setup_logging  # noqa: E402
from app.services.evaluator import render_markdown, run_eval  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="知识问答评测")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    setup_logging()
    result = run_eval(limit=args.limit)

    report_path = ROOT / "eval" / "eval_report.md"
    report_path.write_text(render_markdown(result), encoding="utf-8")

    summary = {k: v for k, v in result.items() if k != "details"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for d in result["details"]:
        if not d["passed"]:
            print(f"[FAIL] {d['id']} {d['question']} -> {d['reason']}")
    print(f"报告已写入：{report_path}")


if __name__ == "__main__":
    main()
