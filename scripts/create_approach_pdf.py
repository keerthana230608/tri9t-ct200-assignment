"""Build the upload-friendly PDF copy of APPROACH.md (requires reportlab)."""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "APPROACH.md"
OUTPUT = ROOT / "output" / "pdf" / "Assignment_Approach.pdf"


def clean_inline(text: str) -> str:
    text = text.replace("→", " to ").replace("–", "-").replace("—", "-")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def build() -> None:
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    bold_path = Path("C:/Windows/Fonts/arialbd.ttf")
    if font_path.exists():
        pdfmetrics.registerFont(TTFont("Arial", str(font_path)))
        if bold_path.exists():
            pdfmetrics.registerFont(TTFont("Arial-Bold", str(bold_path)))
        body_font, bold_font = "Arial", "Arial-Bold" if bold_path.exists() else "Arial"
    else:
        body_font, bold_font = "Helvetica", "Helvetica-Bold"

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DocTitle", parent=styles["Title"], fontName=bold_font, fontSize=21, leading=26,
                              textColor=colors.HexColor("#17324D"), spaceAfter=8 * mm, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], fontName=bold_font, fontSize=13, leading=17,
                              textColor=colors.HexColor("#175A87"), spaceBefore=5 * mm, spaceAfter=2 * mm, keepWithNext=True))
    styles.add(ParagraphStyle(name="Subsection", parent=styles["Heading3"], fontName=bold_font, fontSize=10.5, leading=14,
                              textColor=colors.HexColor("#263746"), spaceBefore=3 * mm, spaceAfter=1.5 * mm, keepWithNext=True))
    styles.add(ParagraphStyle(name="BodyCustom", parent=styles["BodyText"], fontName=body_font, fontSize=9.5, leading=14,
                              textColor=colors.HexColor("#202A33"), spaceAfter=2.5 * mm))
    styles.add(ParagraphStyle(name="BulletCustom", parent=styles["BodyText"], fontName=body_font, fontSize=9.2, leading=13.2,
                              textColor=colors.HexColor("#202A33"), spaceAfter=1.5 * mm))

    story = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        if bullets:
            items = [ListItem(Paragraph(clean_inline(item), styles["BulletCustom"]), leftIndent=3 * mm) for item in bullets]
            story.append(ListFlowable(items, bulletType="bullet", start="circle", leftIndent=6 * mm,
                                      bulletFontName=body_font, bulletFontSize=6, spaceAfter=2 * mm))
            bullets.clear()

    for raw in SOURCE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            flush_bullets()
            continue
        if line.startswith("- "):
            bullets.append(line[2:])
            continue
        flush_bullets()
        if line == "## Version matching and traceability":
            story.append(PageBreak())
        if line.startswith("# "):
            story.append(Paragraph(clean_inline(line[2:]), styles["DocTitle"]))
        elif line.startswith("## "):
            story.append(Paragraph(clean_inline(line[3:]), styles["Section"]))
        elif line.startswith("### "):
            story.append(Paragraph(clean_inline(line[4:]), styles["Subsection"]))
        else:
            story.append(Paragraph(clean_inline(line), styles["BodyCustom"]))
    flush_bullets()

    def footer(canvas, doc) -> None:
        canvas.saveState()
        width, _ = A4
        canvas.setStrokeColor(colors.HexColor("#D7E2EA"))
        canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
        canvas.setFont(body_font, 8)
        canvas.setFillColor(colors.HexColor("#526575"))
        canvas.drawString(18 * mm, 9 * mm, "Working prototype - human review required for safety-critical content")
        canvas.drawRightString(width - 18 * mm, 9 * mm, str(doc.page))
        canvas.restoreState()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                                 topMargin=22 * mm, bottomMargin=21 * mm,
                                 title="CT-200 Assignment Approach", author="Codex draft for Keerthana Janakiram")
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    print(OUTPUT)


if __name__ == "__main__":
    build()
