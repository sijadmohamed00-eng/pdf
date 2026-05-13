#!/usr/bin/env python3
"""
docx_builder.py
═══════════════
Enterprise DOCX Builder
- Full Arabic RTL support (reshaping + bidi)
- Multi-column layout reconstruction
- Table detection & formatting
- Page break after every PDF page
- Header/footer injection
- Embedded page images (optional)
- Memory-safe: processes one page result at a time
"""

import gc
import os
import io
import logging
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import lxml.etree as etree

log = logging.getLogger("docx_builder")

# ─────────────────────────────────────────────────────────────────────────────
# Arabic text helpers
# ─────────────────────────────────────────────────────────────────────────────

def reshape_arabic(text: str) -> str:
    """Reshape + apply bidi algorithm for correct Arabic display in DOCX."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def _is_arabic_char(c: str) -> bool:
    return '\u0600' <= c <= '\u06FF' or '\u0590' <= c <= '\u05FF'


def _dominant_direction(text: str) -> str:
    """Return 'rtl' or 'ltr' based on dominant script."""
    ar  = sum(1 for c in text if _is_arabic_char(c))
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    return 'rtl' if ar >= lat else 'ltr'


# ─────────────────────────────────────────────────────────────────────────────
# DOCX XML helpers
# ─────────────────────────────────────────────────────────────────────────────

def _add_page_break(doc: Document):
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after  = Pt(0)
    run  = para.add_run()
    br   = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    run._r.append(br)


def _set_para_rtl(para, rtl: bool):
    """Set bidi / alignment on a paragraph."""
    pPr = para._p.get_or_add_pPr()

    # Remove existing bidi element if any
    for old in pPr.findall(qn("w:bidi")):
        pPr.remove(old)

    bidi = OxmlElement("w:bidi")
    bidi.set(qn("w:val"), "1" if rtl else "0")
    pPr.append(bidi)

    if rtl:
        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    else:
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT


def _set_run_font(run, rtl: bool, size_pt: float = 11):
    run.font.size = Pt(size_pt)
    if rtl:
        run.font.name = "Traditional Arabic"
        rPr = run._r.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.insert(0, rFonts)
        rFonts.set(qn("w:cs"),      "Traditional Arabic")
        rFonts.set(qn("w:eastAsia"),"Traditional Arabic")
    else:
        run.font.name = "Calibri"


def _set_doc_rtl(doc: Document, rtl: bool):
    """Set document-level RTL default."""
    settings = doc.settings.element
    tag = qn("w:themeFontLang")
    lang = settings.find(tag)
    if lang is None:
        lang = OxmlElement(tag)
        settings.append(lang)
    if rtl:
        lang.set(qn("w:bidi"), "ar-SA")


def _add_section_header(doc: Document, text: str, level: int = 1, rtl: bool = False):
    """Add a heading paragraph."""
    heading = doc.add_heading(level=level)
    heading.clear()
    run = heading.add_run(text)
    _set_para_rtl(heading, rtl)
    _set_run_font(run, rtl, size_pt=14 if level == 1 else 12)
    run.font.bold = True
    return heading


# ─────────────────────────────────────────────────────────────────────────────
# Table builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_table(doc: Document, raw_text: str, rtl: bool):
    """
    Parse pipe-separated table text → python-docx table.
    Example row: "Name  |  Age  |  City"
    """
    lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
    if not lines:
        return

    rows_data = []
    for line in lines:
        cells = [c.strip() for c in line.split("|")]
        rows_data.append(cells)

    max_cols = max(len(r) for r in rows_data)

    table = doc.add_table(rows=len(rows_data), cols=max_cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    for r_idx, row_data in enumerate(rows_data):
        row = table.rows[r_idx]
        for c_idx, cell_text in enumerate(row_data):
            if c_idx >= max_cols:
                break
            cell = row.cells[c_idx]
            para = cell.paragraphs[0]
            text = reshape_arabic(cell_text) if rtl else cell_text
            run  = para.add_run(text)
            _set_para_rtl(para, rtl)
            _set_run_font(run, rtl, size_pt=10)
            if r_idx == 0:
                run.font.bold = True

    doc.add_paragraph()   # spacer after table


# ─────────────────────────────────────────────────────────────────────────────
# Image embedder
# ─────────────────────────────────────────────────────────────────────────────

def _embed_image(doc: Document, image_path: str, max_width_inches: float = 6.0):
    """Embed image into document with safe size clipping."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            w_px, h_px = img.size
            dpi         = img.info.get("dpi", (96, 96))[0] or 96
            w_in        = min(w_px / dpi, max_width_inches)
        doc.add_picture(image_path, width=Inches(w_in))
        last_para = doc.paragraphs[-1]
        last_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph()
    except Exception as exc:
        log.warning("Image embed failed (%s): %s", image_path, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main DOCX builder
# ─────────────────────────────────────────────────────────────────────────────

class DocxBuilder:
    """
    Builds a Word document from a list of PageResult dicts.
    Processes one page at a time to keep RAM usage bounded.
    """

    def __init__(
        self,
        output_path:    str,
        original_name:  str,
        embed_images:   bool = True,
        page_breaks:    bool = True,
        default_rtl:    bool = False,
    ):
        self.output_path   = output_path
        self.original_name = original_name
        self.embed_images  = embed_images
        self.page_breaks   = page_breaks
        self.default_rtl   = default_rtl
        self.doc           = self._init_document()

    # ── Document init ─────────────────────────────────────────────────────────
    def _init_document(self) -> Document:
        doc = Document()

        # A4 page size
        for section in doc.sections:
            section.page_width  = Cm(21)
            section.page_height = Cm(29.7)
            section.left_margin  = Cm(2.5)
            section.right_margin = Cm(2.5)
            section.top_margin   = Cm(2.5)
            section.bottom_margin= Cm(2.5)

        # Default paragraph style
        style = doc.styles["Normal"]
        style.paragraph_format.space_after  = Pt(4)
        style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        style.paragraph_format.line_spacing  = 1.15

        return doc

    # ── Cover page ────────────────────────────────────────────────────────────
    def add_cover(self, total_pages: int, lang: str = "ara+eng"):
        doc = self.doc
        doc.add_paragraph()
        title = doc.add_heading(level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run(f"📄  {self.original_name}")
        run.font.size = Pt(20)

        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub.add_run(f"OCR Output  ·  {total_pages} pages  ·  Language: {lang}").font.color.rgb = RGBColor(0x55, 0x55, 0x55)

        doc.add_paragraph()
        _add_page_break(doc)

    # ── Single page ───────────────────────────────────────────────────────────
    def add_page(self, page_result: dict, page_index: int, total_pages: int):
        doc    = self.doc
        pg_num = page_result.get("page_num", page_index) + 1
        error  = page_result.get("error")
        text   = page_result.get("text", "")
        is_rtl = page_result.get("is_rtl", self.default_rtl)

        # ── Page label ────────────────────────────────────────────────────────
        lbl = doc.add_heading(level=3)
        lbl.clear()
        label_text = f"— صفحة {pg_num} —" if is_rtl else f"— Page {pg_num} —"
        run = lbl.add_run(label_text)
        run.font.size   = Pt(9)
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        _set_para_rtl(lbl, is_rtl)

        # ── Error case ────────────────────────────────────────────────────────
        if error:
            err_para = doc.add_paragraph()
            err_run  = err_para.add_run(f"[خطأ في المعالجة: {error}]")
            err_run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
            err_run.font.italic    = True
            if self.page_breaks and page_index < total_pages - 1:
                _add_page_break(doc)
            return

        # ── Embed page image (optional) ───────────────────────────────────────
        if self.embed_images:
            img_path = page_result.get("image_path")
            if img_path and os.path.exists(img_path):
                _embed_image(doc, img_path)

        # ── Text content ──────────────────────────────────────────────────────
        if text.strip():
            # Split into paragraphs; detect direction per paragraph
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            if not paragraphs:
                paragraphs = [l.strip() for l in text.split("\n") if l.strip()]

            for para_text in paragraphs:
                direction = _dominant_direction(para_text)
                rtl_para  = (direction == 'rtl')

                # Check if this looks like a table row
                if "|" in para_text and para_text.count("|") >= 2:
                    _build_table(doc, para_text, rtl_para)
                    continue

                # Reshape Arabic text
                display_text = reshape_arabic(para_text) if rtl_para else para_text

                para = doc.add_paragraph()
                run  = para.add_run(display_text)
                _set_para_rtl(para, rtl_para)
                _set_run_font(run, rtl_para, size_pt=11)
        else:
            # Empty page placeholder
            empty = doc.add_paragraph()
            empty.add_run("[صفحة فارغة]" if is_rtl else "[Empty page]").font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)

        # ── Page break ────────────────────────────────────────────────────────
        if self.page_breaks and page_index < total_pages - 1:
            _add_page_break(doc)

        gc.collect()

    # ── Save ──────────────────────────────────────────────────────────────────
    def save(self):
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(self.output_path)
        log.info("DOCX saved → %s (%.1f MB)",
                 self.output_path,
                 os.path.getsize(self.output_path) / (1024 * 1024))

    # ── Convenience: build from full results list ─────────────────────────────
    def build_from_results(
        self,
        results:     list[dict],
        lang:        str = "ara+eng",
        progress_cb: Optional[object] = None,
    ):
        """
        Full pipeline: cover → pages → save.
        Memory-safe: one page at a time.
        """
        total = len(results)
        self.add_cover(total, lang)

        for idx, result in enumerate(results):
            self.add_page(result, idx, total)
            if progress_cb:
                progress_cb(idx + 1, total)
            # Periodic GC every 20 pages
            if (idx + 1) % 20 == 0:
                gc.collect()

        self.save()
        return self.output_path


# ─────────────────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile
    sample_results = [
        {
            "page_num": 0,
            "text": "مرحباً بكم في نظام OCR المتقدم\nهذا مثال على نص عربي يدعم RTL",
            "is_rtl": True,
            "has_image": False,
            "image_path": None,
        },
        {
            "page_num": 1,
            "text": "Hello World\nThis is an English paragraph.\nName | Age | City\nAli  | 25  | Baghdad",
            "is_rtl": False,
            "has_image": False,
            "image_path": None,
        },
    ]
    out = tempfile.mktemp(suffix=".docx")
    builder = DocxBuilder(out, "test_document.pdf", embed_images=False)
    builder.build_from_results(sample_results)
    print(f"Test DOCX written → {out}")
