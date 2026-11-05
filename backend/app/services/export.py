"""Resume / cover-letter export to .docx (python-docx) and .pdf (ReportLab).

Both renderers consume the same StructuredResume so the two formats stay in sync.
If `data/resume_template.docx` exists its styles (fonts, margins, heading styles) are used
as the base template for .docx output.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from ..config import get_settings
from ..llm.schemas import StructuredResume

ACCENT = RGBColor(0x1F, 0x3A, 0x5F)


def _template_path() -> Path:
    return get_settings().data_dir / "resume_template.docx"


def _new_document() -> Document:
    tpl = _template_path()
    if tpl.exists():
        doc = Document(str(tpl))
        # clear template body content but keep styles/sections
        body = doc.element.body
        for child in list(body):
            if child.tag.endswith("sectPr"):
                continue
            body.remove(child)
        return doc
    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(0.6)
        section.left_margin = section.right_margin = Inches(0.7)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)
    return doc


def _heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = ACCENT


def resume_docx(r: StructuredResume) -> bytes:
    doc = _new_document()
    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    nr = name.add_run(r.contact.name or "Your Name")
    nr.bold = True
    nr.font.size = Pt(18)
    nr.font.color.rgb = ACCENT
    contact_bits = [x for x in [r.contact.location, r.contact.email, r.contact.phone, *r.contact.links] if x]
    if contact_bits:
        cp = doc.add_paragraph(" · ".join(contact_bits))
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if r.summary:
        _heading(doc, "Summary")
        doc.add_paragraph(r.summary)
    if r.experience:
        _heading(doc, "Experience")
        for e in r.experience:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            t = p.add_run(e.title)
            t.bold = True
            if e.company:
                p.add_run(f" — {e.company}")
            dates = " – ".join(x for x in [e.start, e.end] if x)
            meta = " | ".join(x for x in [e.location, dates] if x)
            if meta:
                m = p.add_run(f"   {meta}")
                m.italic = True
                m.font.size = Pt(9.5)
            for b in e.bullets:
                bp = doc.add_paragraph(b.text, style="List Bullet")
                bp.paragraph_format.space_after = Pt(1)
    if r.skills:
        _heading(doc, "Skills")
        for g in r.skills:
            p = doc.add_paragraph()
            cr = p.add_run(f"{g.category}: ")
            cr.bold = True
            p.add_run(", ".join(g.items))
    if r.education:
        _heading(doc, "Education")
        for ed in r.education:
            line = ", ".join(x for x in [" ".join(y for y in [ed.degree, ed.field] if y), ed.school, ed.year] if x)
            doc.add_paragraph(line)
    if r.certifications:
        _heading(doc, "Certifications")
        for c in r.certifications:
            doc.add_paragraph(c, style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def resume_pdf(r: StructuredResume) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"{r.contact.name or 'Resume'}",
        author=r.contact.name or "",
    )
    ss = getSampleStyleSheet()
    accent = colors.HexColor("#1F3A5F")
    name_style = ParagraphStyle("name", parent=ss["Title"], fontSize=18, textColor=accent, alignment=TA_CENTER, spaceAfter=2)
    contact_style = ParagraphStyle("contact", parent=ss["Normal"], fontSize=9.5, alignment=TA_CENTER, spaceAfter=6)
    h_style = ParagraphStyle("h", parent=ss["Heading4"], fontSize=11, textColor=accent, spaceBefore=8, spaceAfter=2)
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=10, leading=13)
    role = ParagraphStyle("role", parent=body, spaceBefore=4)
    flow: list = [Paragraph(_esc(r.contact.name or "Your Name"), name_style)]
    bits = [x for x in [r.contact.location, r.contact.email, r.contact.phone, *r.contact.links] if x]
    if bits:
        flow.append(Paragraph(_esc(" · ".join(bits)), contact_style))

    def section(title: str) -> None:
        flow.append(Paragraph(title.upper(), h_style))
        flow.append(HRFlowable(width="100%", thickness=0.6, color=accent, spaceAfter=3))

    if r.summary:
        section("Summary")
        flow.append(Paragraph(_esc(r.summary), body))
    if r.experience:
        section("Experience")
        for e in r.experience:
            dates = " – ".join(x for x in [e.start, e.end] if x)
            meta = " | ".join(x for x in [e.location, dates] if x)
            head = f"<b>{_esc(e.title)}</b>" + (f" — {_esc(e.company)}" if e.company else "")
            if meta:
                head += f"&nbsp;&nbsp;<i><font size=9>{_esc(meta)}</font></i>"
            flow.append(Paragraph(head, role))
            if e.bullets:
                flow.append(
                    ListFlowable(
                        [ListItem(Paragraph(_esc(b.text), body), leftIndent=10) for b in e.bullets],
                        bulletType="bullet",
                        start="•",
                        leftIndent=12,
                        bulletFontSize=8,
                    )
                )
    if r.skills:
        section("Skills")
        for g in r.skills:
            flow.append(Paragraph(f"<b>{_esc(g.category)}:</b> {_esc(', '.join(g.items))}", body))
    if r.education:
        section("Education")
        for ed in r.education:
            line = ", ".join(x for x in [" ".join(y for y in [ed.degree, ed.field] if y), ed.school, ed.year] if x)
            flow.append(Paragraph(_esc(line), body))
    if r.certifications:
        section("Certifications")
        for c in r.certifications:
            flow.append(Paragraph(_esc(c), body))
    flow.append(Spacer(1, 4))
    doc.build(flow)
    return buf.getvalue()


def cover_letter_docx(name: str, company: str, role: str, body: str) -> bytes:
    doc = _new_document()
    p = doc.add_paragraph()
    r = p.add_run(name or "")
    r.bold = True
    r.font.size = Pt(14)
    doc.add_paragraph(f"Re: {role} — {company}")
    for para in [x.strip() for x in re.split(r"\n\s*\n", body) if x.strip()]:
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def safe_filename(*parts: str) -> str:
    s = "_".join(p for p in parts if p)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return s[:120] or "document"
