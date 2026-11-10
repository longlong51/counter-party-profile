"""报告接口：四 Agent 生成、列表、PDF 下载。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.agents.counterparty_graph import generate_report
from app.core.config import settings
from app.models.schemas import ReportGenerateIn
from app.services.db import db

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/generate", summary="生成对手方画像报告（4 Agent 协作 + 校验回环）")
def generate(body: ReportGenerateIn) -> dict[str, Any]:
    if body.session_id and not db.get_session(body.session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return generate_report(body.counterparty, session_id=body.session_id,
                           max_retry=body.max_retry)


@router.get("", summary="报告列表")
def list_reports(limit: int = 20) -> dict[str, Any]:
    items = []
    for r in db.list_reports(limit=limit):
        items.append({
            "report_id": r.get("report_id"),
            "counterparty": r.get("counterparty"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "pdf_url": f"/api/reports/{r.get('report_id')}/pdf",
            "scores": json.loads(r.get("scores") or "{}") if isinstance(r.get("scores"), str) else r.get("scores"),
        })
    return {"items": items}


@router.get("/{report_id}/pdf", summary="下载报告 PDF")
def download_pdf(report_id: str) -> FileResponse:
    rec = db.get_report(report_id)
    if not rec:
        raise HTTPException(status_code=404, detail="报告不存在")
    path = rec.get("pdf_path") or ""
    if not path or not Path(path).exists():
        # 兜底：按 report_id 在报告目录里找同名文件
        candidates = list(Path(settings.reports_dir).glob(f"*{report_id}*.pdf"))
        if not candidates:
            raise HTTPException(status_code=404, detail="PDF 文件不存在")
        path = str(candidates[0])
    return FileResponse(path, media_type="application/pdf",
                        filename=f"counterparty_report_{report_id}.pdf")
