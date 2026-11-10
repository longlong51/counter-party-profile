"""PDF 报告渲染：ReportLab（中文字体 + 表格 + 柱状图）。

字体策略：优先 Windows 系统字体（微软雅黑/宋体），失败则用 ReportLab 内置 CID 字体，
保证在没有 TTF 的机器上也能输出中文。

章节编号由渲染器动态生成，避免因「校验意见」等可选章节缺失导致编号跳号；
Markdown 中结构化渲染未覆盖的叙述性章节（交易与清算要点、尽调建议等）会追加在对应位置。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from app.core.logging import get_logger

log = get_logger(__name__)

_FONT_NAME = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

_CN_NUM = "零一二三四五六七八九十"


def _register_cjk_font() -> tuple[str, str]:
    """注册中文字体，返回 (常规, 粗体) 字体名。"""
    candidates = [
        (r"C:\Windows\Fonts\msyh.ttc", 0, "MSYaHei"),
        (r"C:\Windows\Fonts\simsun.ttc", 0, "SimSun"),
        (r"C:\Windows\Fonts\simhei.ttf", None, "SimHei"),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0, "WQYZenHei"),
    ]
    for path, idx, name in candidates:
        if not os.path.exists(path):
            continue
        try:
            if idx is None:
                pdfmetrics.registerFont(TTFont(name, path))
            else:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
            log.info("PDF 中文字体：%s", path)
            return name, name
        except Exception as exc:  # noqa: BLE001
            log.debug("字体注册失败 %s：%s", path, exc)

    # 兜底：CID 字体（无需字体文件）
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light", "STSong-Light"
    except Exception:  # noqa: BLE001
        return "Helvetica", "Helvetica-Bold"


_FONT_NAME, _FONT_BOLD = _register_cjk_font()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=_FONT_BOLD,
                                fontSize=20, leading=26, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=_FONT_BOLD,
                             fontSize=13, leading=18, spaceBefore=10, spaceAfter=6,
                             textColor=colors.HexColor("#1a3d6d")),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontName=_FONT_NAME,
                               fontSize=9.5, leading=15, firstLineIndent=0),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontName=_FONT_NAME,
                                fontSize=8, leading=12, textColor=colors.HexColor("#666666")),
    }


def _table(data: list[list[str]], col_widths: list[float] | None = None,
           header_bg: str = "#1a3d6d") -> Table:
    """统一风格表格。"""
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d3e0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _score_chart(scores: dict[str, float], width: float = 460) -> Drawing:
    """五维评分柱状图。"""
    drawing = Drawing(width, 150)
    chart = VerticalBarChart()
    chart.x, chart.y = 40, 34
    chart.height, chart.width = 95, width - 70
    # 单序列 5 个柱子（不能写成 5 个单值序列，否则每组各用一种默认配色）
    chart.data = [list(scores.values())]
    chart.categoryAxis.categoryNames = list(scores.keys())
    chart.categoryAxis.labels.fontName = _FONT_NAME
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.boxAnchor = "n"
    chart.categoryAxis.labels.dy = -6
    chart.valueAxis.labels.fontName = _FONT_NAME
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.valueAxis.gridStrokeColor = colors.HexColor("#e2e8f0")
    chart.valueAxis.visibleGrid = 1
    chart.bars[0].fillColor = colors.HexColor("#2f6db3")
    chart.bars[0].strokeColor = colors.HexColor("#1f4e79")
    chart.barWidth = 14
    chart.barSpacing = 6
    drawing.add(chart)
    return drawing


def _md_sections(markdown: str) -> dict[str, str]:
    """把 Markdown 的 `## 一、标题` 拆成 {标题: 正文}，用于补齐叙述性章节。"""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        m = re.match(r"^##\s+(.+)$", line.strip())
        if m:
            title = re.sub(r"^[一二三四五六七八九十]+、", "", m.group(1)).strip()
            current = title
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    """转义后渲染段落，保留换行。"""
    safe = escape(str(text)).replace("\n", "<br/>")
    return Paragraph(safe, style)


def render_report(path: str, payload: dict[str, Any]) -> str:
    """渲染对手方画像 PDF。

    payload 约定：
        counterparty, generated_at, data_source, scores(dict), summary,
        identity(dict), rating(dict), finance(list), risks(list), citations(list),
        issues(list), retries(int), markdown(str)
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    st = _styles()
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"对手方画像报告 - {payload.get('counterparty', '')}",
    )

    story: list[Any] = []
    cp = payload.get("counterparty", "未知对手方")
    story.append(_para("交易对手方画像报告", st["title"]))
    story.append(_para(
        f"对手方：{cp} ｜ 生成时间：{payload.get('generated_at', '')} ｜ "
        f"数据来源：{payload.get('data_source', '')} ｜ 校验轮次：{payload.get('retries', 0)}",
        st["small"]))
    story.append(Spacer(1, 6))

    md = _md_sections(str(payload.get("markdown") or ""))
    counter = {"n": 0}

    def heading(title: str) -> None:
        counter["n"] += 1
        story.append(_para(f"{_CN_NUM[counter['n']]}、{title}", st["h2"]))

    # ---- 一、结论摘要 ----
    heading("结论摘要")
    summary = str(payload.get("summary") or md.get("结论摘要") or "")
    for line in summary.split("\n"):
        if line.strip():
            story.append(_para(line.strip().lstrip("-• "), st["body"]))
    story.append(Spacer(1, 4))

    # ---- 二、五维评分 ----
    scores = payload.get("scores") or {}
    if scores:
        heading("五维评分")
        notes = {
            "主体实力": "注册资本、成立年限、是否上市等主体特征",
            "财务稳健": "资产负债率、盈利与规模等财务指标",
            "信用资质": "主体/债项评级与评级展望",
            "交易活跃": "存续债券、成交与发行活跃度",
            "合规风险": "司法与经营风险事件数量（得分越高风险越低）",
        }
        rows = [["维度", "得分（0-100）", "说明"]]
        for k, v in scores.items():
            rows.append([k, f"{v:.0f}", notes.get(k, "")])
        story.append(_table(rows, col_widths=[70, 70, 340]))
        story.append(Spacer(1, 6))
        story.append(KeepTogether(_score_chart(scores)))
        story.append(Spacer(1, 8))

    # ---- 三、主体信息 ----
    ident = payload.get("identity") or {}
    if ident:
        heading("主体信息")
        rows = [["字段", "内容"]]
        for k, v in ident.items():
            rows.append([str(k), str(v) or "—"])
        story.append(_table(rows, col_widths=[110, 380]))

    # ---- 四、评级情况 ----
    rating = payload.get("rating") or {}
    if rating:
        heading("评级情况")
        rows = [["评级日期", "评级机构", "主体评级", "展望", "债项评级"]]
        rows.append([str(rating.get("rating_date") or "—"), str(rating.get("agency") or "—"),
                     str(rating.get("issuer_rating") or "—"),
                     str(rating.get("issuer_outlook") or "—"),
                     str(rating.get("bond_rating") or "—")])
        story.append(_table(rows, col_widths=[80, 120, 80, 70, 70]))

    # ---- 五、财务概况 ----
    finance = payload.get("finance") or []
    if finance:
        heading("财务概况")
        rows = [["报告期", "总资产", "总负债", "营业收入", "净利润", "资产负债率", "ROE"]]
        for f in finance[:5]:
            rows.append([str(f.get("period") or "—"), str(f.get("total_asset") or "—"),
                         str(f.get("total_liab") or "—"), str(f.get("revenue") or "—"),
                         str(f.get("net_profit") or "—"), str(f.get("debt_ratio") or "—"),
                         str(f.get("roe") or "—")])
        story.append(_table(rows, col_widths=[70, 75, 75, 70, 65, 60, 50]))

    # ---- 六、交易与清算要点（来自 Markdown 叙述，结构表未覆盖）----
    trading = md.get("交易与清算要点") or md.get("交易与清算安排")
    if trading:
        heading("交易与清算要点")
        story.append(_para(trading, st["body"]))

    # ---- 七、风险与合规事件 ----
    heading("风险与合规事件")
    risks = payload.get("risks") or []
    if risks:
        rows = [["事件类型", "发生日期", "金额/标的", "描述", "来源"]]
        for r in risks[:10]:
            rows.append([str(r.get("event_type") or "—")[:14],
                         str(r.get("event_date") or "—"),
                         str(r.get("event_amount") or "—")[:12],
                         str(r.get("event_desc") or "—")[:40],
                         str(r.get("source") or "—")])
        story.append(_table(rows, col_widths=[75, 65, 60, 230, 55]))
    else:
        story.append(_para("未获取到风险事件记录（或数据源未覆盖）。", st["body"]))

    # ---- 八、尽调与后续动作建议 ----
    advice = md.get("尽调与后续动作建议") or md.get("后续动作建议")
    if advice:
        heading("尽调与后续动作建议")
        story.append(_para(advice, st["body"]))

    # ---- 校验意见（可选）----
    issues = payload.get("issues") or []
    if issues:
        heading("自动校验意见")
        rows = [["级别", "字段", "问题", "修正建议"]]
        for it in issues[:8]:
            rows.append([str(it.get("level") or ""), str(it.get("field") or ""),
                         str(it.get("problem") or "")[:40], str(it.get("fix") or "")[:40]])
        story.append(_table(rows, col_widths=[40, 70, 190, 190], header_bg="#a33"))

    # ---- 知识库引用（按标题+来源去重）----
    cites = payload.get("citations") or []
    if cites:
        heading("知识库引用")
        seen: set[tuple[str, str]] = set()
        seq = 0
        for c in cites:
            key = (str(c.get("title", "")), str(c.get("source_name", "")))
            if key in seen:
                continue
            seen.add(key)
            seq += 1
            if seq > 8:
                break
            story.append(_para(
                f"{seq}. {c.get('title', '')}（{c.get('source_name', '')}，"
                f"生效 {c.get('effective_date', '—')}）", st["small"]))

    story.append(Spacer(1, 10))
    story.append(_para(
        "免责声明：本报告由 AI 依据公开资料与知识库规则自动生成，仅供内部参考，"
        "不构成投资建议；用于授信或交易决策前须经人工复核。", st["small"]))

    doc.build(story)
    log.info("PDF 已生成：%s", path)
    return path
