"""文档接口：PDF / 图片上传解析、结果查询、要点摘要。

解析结果会写进 Redis（键 doc:{sha256}，TTL 12h），并登记到会话文档列表
session:docs:{session_id}，供报告 Agent2 读取。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import SummarizeIn
from app.services.cache import cache
from app.services.document_parser import get_doc, parse_file, summarize
from app.services.events import bus

router = APIRouter(prefix="/api/documents", tags=["documents"])
log = get_logger(__name__)

_ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


@router.post("/parse", summary="上传并解析 PDF / 图片")
async def parse_document(file: UploadFile = File(...),
                        session_id: str = Form("")) -> dict[str, Any]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED:
        raise HTTPException(status_code=400,
                            detail=f"不支持的类型 {ext}，仅支持 PDF 与常见图片")

    dest = Path(settings.uploads_dir) / (file.filename or "upload.bin")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = parse_file(str(dest), original_name=file.filename)
    except Exception as exc:  # noqa: BLE001
        log.exception("解析失败")
        raise HTTPException(status_code=500, detail=f"解析失败：{exc}") from exc

    if session_id:
        docs = cache.get_json(f"session:docs:{session_id}") or []
        if result["doc_id"] not in [d.get("doc_id") for d in docs]:
            docs.append({"doc_id": result["doc_id"], "filename": result["filename"],
                         "parser": result["parser"], "text_chars": result["text_chars"]})
            cache.set_json(f"session:docs:{session_id}", docs, ttl=12 * 3600)

    bus.publish("document.parsed", {"doc_id": result["doc_id"], "filename": result["filename"]})
    # 大文本不回传给前端，只给摘要预览
    return {
        "doc_id": result["doc_id"],
        "filename": result["filename"],
        "pages": result["pages"],
        "tables": result["tables"],
        "text_chars": result["text_chars"],
        "parser": result["parser"],
        "cached": result.get("cached", False),
        "markdown": (result.get("markdown") or "")[:4000],
    }


@router.get("/{doc_id}", summary="查询已解析文档")
def read_document(doc_id: str) -> dict[str, Any]:
    data = get_doc(doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="文档不存在或缓存已过期")
    return data


@router.post("/{doc_id}/summarize", summary="文档要点摘要")
def summarize_document(doc_id: str, body: SummarizeIn) -> dict[str, Any]:
    data = get_doc(doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="文档不存在或缓存已过期")
    return summarize(data, focus=body.focus)
