"""文档解析：PDF（文本/表格/扫描页 OCR）+ 图片（视觉模型 OCR），结果写入缓存。

解析策略：
    1. PDF 每页先取文本层；
    2. 有文本 → 直接使用，并尝试 find_tables() 抽表格转 Markdown；
    3. 无文本（扫描件/图片页）→ 渲染 150dpi JPEG → 视觉模型 OCR；
    4. 纯图片文件 → 视觉模型 OCR。
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.cache import cache
from app.services.llm import LLMError, llm

log = get_logger(__name__)

OCR_PROMPT = (
    "这是一页金融/财报类文档的图片。请：\n"
    "1. 完整提取其中所有文字（含页眉页脚、表名、单位、脚注）；\n"
    "2. 表格必须还原为 Markdown 表格，保留行列对齐与百分比、金额、日期；\n"
    "3. 图表请描述图表类型、横纵轴含义与关键数据点；\n"
    "4. 不要臆造任何数字，看不清的用「(模糊)」标注。"
)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _img_to_b64(data: bytes, max_edge: int = 1600) -> tuple[str, str]:
    """图片压缩为长边 ≤ max_edge 的 JPEG，返回 (base64, mime)。"""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


def _tables_to_markdown(tables: list[list[list[Any]]]) -> str:
    """把 find_tables() 的二维数组转 Markdown。"""
    out: list[str] = []
    for rows in tables:
        cleaned = [[("" if c is None else str(c).replace("\n", " ").strip()) for c in row] for row in rows]
        if not cleaned:
            continue
        header = cleaned[0]
        out.append("| " + " | ".join(header) + " |")
        out.append("| " + " | ".join(["---"] * len(header)) + " |")
        for row in cleaned[1:]:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            out.append("| " + " | ".join(row[:len(header)]) + " |")
        out.append("")
    return "\n".join(out)


def _parse_pdf(path: str) -> dict[str, Any]:
    """逐页解析 PDF，返回 pages/tables/text/markdown。"""
    import pymupdf  # PyMuPDF 2.x 推荐导入名

    pages: list[dict[str, Any]] = []
    tables_md: list[str] = []
    texts: list[str] = []
    parser_flags: set[str] = set()

    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = (page.get_text("text") or "").strip()

            # ---- 表格抽取（矢量表格才有坐标，图片表格交给 OCR）----
            page_tables: list[list[list[Any]]] = []
            try:
                found = page.find_tables()
                for tb in found.tables:
                    data = tb.extract()
                    if data:
                        page_tables.append(data)
            except Exception as exc:  # noqa: BLE001 - 某些页面无表格结构
                log.debug("第 %d 页表格抽取失败：%s", i, exc)

            page_md = _tables_to_markdown(page_tables) if page_tables else ""
            if page_md:
                tables_md.append(f"### 第 {i} 页表格\n{page_md}")
                parser_flags.add("table")

            # ---- 无文本层：渲染后走视觉 OCR ----
            if len(text) < 30:
                try:
                    pix = page.get_pixmap(dpi=150)
                    raw = pix.tobytes("jpg")
                    b64, mime = _img_to_b64(raw)
                    ocr_text = llm.vision(OCR_PROMPT, b64, mime)
                    text = ocr_text
                    parser_flags.add("vision")
                except LLMError as exc:
                    log.warning("第 %d 页 OCR 失败（LLM 不可用）：%s", i, exc)
                    text = text or f"(第 {i} 页为图片，OCR 不可用)"
                    parser_flags.add("ocr_failed")
                except Exception as exc:  # noqa: BLE001
                    log.warning("第 %d 页渲染失败：%s", i, exc)

            pages.append({
                "page": i,
                "chars": len(text),
                "tables": len(page_tables),
                "has_text": len(text) > 30,
                "text": text,
            })
            if text:
                texts.append(f"\n\n--- 第 {i} 页 ---\n{text}")

        page_count = doc.page_count

    parser = "mixed" if len(parser_flags) > 1 else (parser_flags.pop() if parser_flags else "pymupdf")

    return {
        "pages": page_count,
        "page_details": pages,
        "tables": sum(p["tables"] for p in pages),
        "text": "\n".join(texts),
        "markdown": ("\n".join(tables_md) + "\n" + "\n".join(texts)).strip(),
        "parser": parser,
    }


def _parse_image(path: str) -> dict[str, Any]:
    """图片走视觉模型 OCR。"""
    with open(path, "rb") as f:
        data = f.read()
    b64, mime = _img_to_b64(data)
    try:
        text = llm.vision(OCR_PROMPT, b64, mime)
        parser = "vision"
    except LLMError as exc:
        log.warning("图片 OCR 失败：%s", exc)
        text = "(图片 OCR 不可用，请配置 LLM 视觉模型)"
        parser = "ocr_failed"
    return {
        "pages": 1, "page_details": [{"page": 1, "chars": len(text), "has_text": True,
                                     "tables": 0, "text": text}],
        "tables": 0, "text": text, "markdown": text, "parser": parser,
    }


def parse_file(path: str, original_name: str | None = None) -> dict[str, Any]:
    """解析入口：按扩展名分派，结果按文件指纹缓存。"""
    filename = original_name or os.path.basename(path)
    ext = Path(filename).suffix.lower()
    doc_id = _sha256(path)[:24]
    cache_key = f"doc:{doc_id}"

    cached = cache.get_json(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    if ext == ".pdf":
        result = _parse_pdf(path)
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        result = _parse_image(path)
    else:
        raise ValueError(f"不支持的文件类型：{ext}（仅支持 PDF 与常见图片）")

    result.update({
        "doc_id": doc_id,
        "filename": filename,
        "text_chars": len(result.get("text", "")),
        "cached": False,
    })
    cache.set_json(cache_key, result, ttl=settings.doc_cache_ttl)
    log.info("解析完成 %s：pages=%s tables=%s parser=%s",
             filename, result["pages"], result["tables"], result["parser"])
    return result


def get_doc(doc_id: str) -> dict[str, Any] | None:
    data = cache.get_json(f"doc:{doc_id}")
    if data:
        data["cached"] = True
    return data


def summarize(doc: dict[str, Any], focus: str = "综合") -> dict[str, Any]:
    """对已解析文档做要点摘要（LLM 不可用时退化为抽取式）。"""
    text = (doc.get("markdown") or doc.get("text") or "")[:12000]
    try:
        data = llm.chat_json(
            "你是金融文档分析助手。从给定文档中提取与指定关注点相关的关键事实。",
            f"关注点：{focus}\n\n文档内容：\n{text}\n\n"
            '输出 JSON：{"summary": "150字以内摘要", "key_facts": ["事实1","事实2","事实3"]}',
        )
        if isinstance(data, dict):
            return {"summary": str(data.get("summary", ""))[:400],
                    "key_facts": [str(x)[:200] for x in (data.get("key_facts") or [])][:8]}
    except Exception as exc:  # noqa: BLE001
        log.warning("文档摘要失败，使用抽取式：%s", exc)

    sentences = [s.strip() for s in text.replace("\n", "。").split("。") if len(s.strip()) > 12]
    return {"summary": "；".join(sentences[:3])[:300], "key_facts": sentences[:5]}
