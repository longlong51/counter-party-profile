"""统一日志配置：控制台 + 文件双输出。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings

_LOG_DIR = Path(settings.data_dir) / "logs"


def setup_logging() -> None:
    """初始化根日志器（重复调用安全）。"""
    root = logging.getLogger()
    if getattr(root, "_cpa_configured", False):
        return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(
        _LOG_DIR / "app.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    # 降低三方库噪音
    for noisy in ("httpx", "chromadb", "urllib3", "openai", "jieba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._cpa_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
