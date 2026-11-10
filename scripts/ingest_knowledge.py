"""知识库入库脚本：100 条知识点 → 语义分块 → 向量化 → Chroma。

用法：python scripts/ingest_knowledge.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.logging import setup_logging  # noqa: E402
from app.services.rag import ingest, stats  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="构建交易知识库向量索引")
    parser.add_argument("--force", action="store_true", help="强制重建（先清理旧索引）")
    args = parser.parse_args()

    setup_logging()
    result = ingest(force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(stats(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
