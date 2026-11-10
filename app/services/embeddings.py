"""确定性本地 embedding：字符 n-gram + 词粒度哈希，无需下载任何模型。

为什么不用 sentence-transformers？
    本地/离线环境下载 onnx 模型常常失败，且首次加载慢。这里用「哈希技巧」
    （hashing trick）把 n-gram 映射到固定维度，特点是：
      - 完全离线、零依赖、毫秒级；
      - 相同文本恒等向量（跨进程/跨天稳定，便于持久化到 Chroma）；
      - 对中文术语与数字代码（如 "DVP"、"2026-07-06"）有较好区分度。
    在 100 条知识点的规模下，检索质量足够；若换成真实业务库，可无缝替换
    本模块的实现（接口只有 embed_text / embed_batch 两个函数）。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

from app.core.config import settings

_CN_RE = re.compile(r"[\u4e00-\u9fff]+")
_EN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]*")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _tokens(text: str) -> list[str]:
    """把文本切成 token：中文 1~3 gram + 英文词 + 数字。"""
    text = text.lower()
    tokens: list[str] = []

    for seg in _CN_RE.findall(text):
        for i in range(len(seg)):
            tokens.append(seg[i])
            if i + 1 < len(seg):
                tokens.append(seg[i:i + 2])
            if i + 2 < len(seg):
                tokens.append(seg[i:i + 3])

    tokens.extend(_EN_RE.findall(text))
    tokens.extend(_NUM_RE.findall(text))
    return tokens


def _hash_index(token: str, dim: int) -> tuple[int, float]:
    """token -> (维度下标, 符号)，用 blake2b 保证跨进程稳定（不能依赖内置 hash，它带随机盐）。"""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    idx = int.from_bytes(digest[:4], "big") % dim
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return idx, sign


def embed_text(text: str, dim: int | None = None) -> list[float]:
    """单条文本向量化（L2 归一，便于用余弦相似度）。"""
    dim = dim or settings.embed_dim
    vec = [0.0] * dim
    if not text:
        return vec

    tokens = _tokens(text)
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1

    for token, cnt in counts.items():
        idx, sign = _hash_index(token, dim)
        # 次线性词频抑制，避免长文本中高频字主导方向
        vec[idx] += sign * (1.0 + math.log(cnt))

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed_batch(texts: Iterable[str], dim: int | None = None) -> list[list[float]]:
    return [embed_text(t, dim) for t in texts]
