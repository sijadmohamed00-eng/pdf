#!/usr/bin/env python3
"""
layout_analyzer.py
══════════════════
Advanced Layout Engine for Enterprise OCR System
- Object Detection (text / image / table regions)
- Column detection (multi-column books)
- Arabic/RTL paragraph reconstruction
- Memory-safe image processing pipeline
"""

import gc
import io
import logging
from dataclasses import dataclass, field
from typing import Optional

import fitz                   # PyMuPDF
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

log = logging.getLogger("layout_analyzer")


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BBox:
    x0: float; y0: float; x1: float; y1: float

    @property
    def width(self):  return self.x1 - self.x0
    @property
    def height(self): return self.y1 - self.y0
    @property
    def area(self):   return self.width * self.height

    def to_tuple(self): return (self.x0, self.y0, self.x1, self.y1)

    def overlaps(self, other: "BBox", threshold=0.2) -> bool:
        ix0 = max(self.x0, other.x0); iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1); iy1 = min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        inter = (ix1 - ix0) * (iy1 - iy0)
        return inter / min(self.area, other.area) > threshold


@dataclass
class Region:
    bbox:     BBox
    kind:     str          # "text" | "image" | "table" | "header" | "footer"
    text:     str  = ""
    is_rtl:   bool = False
    confidence: float = 1.0
    col_idx:  int  = 0     # column index (for multi-column layouts)
    order:    int  = 0     # reading order


@dataclass
class PageLayout:
    page_num:   int
    width:      float
    height:     float
    regions:    list[Region] = field(default_factory=list)
    num_cols:   int = 1
    is_rtl:     bool = False
    has_images: bool = False
    has_tables: bool = False
    raw_text:   str  = ""


# ─────────────────────────────────────────────────────────────────────────────
# Image enhancement
# ─────────────────────────────────────────────────────────────────────────────

def enhance_for_ocr(img: Image.Image) -> Image.Image:
    """
    Multi-step image enhancement pipeline optimised for OCR accuracy on
    Arabic/English printed text (books, scanned documents).
    """
    # 1. Convert to grayscale
    gray = img.convert("L")

    # 2. Sharpness boost
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)

    # 3. Contrast boost
    gray = ImageEnhance.Contrast(gray).enhance(1.8)

    # 4. Slight denoise via median filter
    gray = gray.filter(ImageFilter.MedianFilter(size=3))

    # 5. Adaptive binarization via numpy threshold
    arr  = np.array(gray, dtype=np.float32)
    # Local mean threshold (simple block approach)
    from scipy.ndimage import uniform_filter
    try:
        blurred   = uniform_filter(arr, size=51)
        binary    = (arr > blurred * 0.85).astype(np.uint8) * 255
        result    = Image.fromarray(binary)
    except ImportError:
        # scipy not available → simple global Otsu-like threshold
        threshold = arr.mean()
        binary    = (arr > threshold).astype(np.uint8) * 255
        result    = Image.fromarray(binary)

    return result


def pil_from_fitz_page(page: fitz.Page, dpi: int = 200) -> Image.Image:
    """Render a PyMuPDF page to a PIL Image without writing to disk."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    del pix
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Column detector
# ─────────────────────────────────────────────────────────────────────────────

def detect_columns(page: fitz.Page, min_gap_ratio: float = 0.04) -> int:
    """
    Detect number of text columns on a page using horizontal gap analysis.
    Returns 1, 2, or 3 (caps at 3 for books).
    """
    blocks = page.get_text("blocks")
    if not blocks:
        return 1

    pw = page.rect.width
    min_gap = pw * min_gap_ratio

    # Collect x-centres of all text blocks
    xs = [(b[0] + b[2]) / 2 for b in blocks if b[4].strip()]
    if len(xs) < 4:
        return 1

    xs.sort()
    arr = np.array(xs)

    # Simple gap detection: find large gaps between successive x values
    gaps = np.diff(arr)
    large_gaps = np.sum(gaps > min_gap * 4)

    if large_gaps >= 2:
        return 3
    elif large_gaps == 1:
        return 2
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Region classifier
# ─────────────────────────────────────────────────────────────────────────────

def _classify_block(block: dict, page_width: float, page_height: float) -> str:
    """
    Rule-based block classifier.
    block fields: (x0, y0, x1, y1, text, block_no, block_type)
    """
    btype = block[6] if len(block) > 6 else 0

    # PyMuPDF type 1 = image block
    if btype == 1:
        return "image"

    text  = str(block[4]) if len(block) > 4 else ""
    x0, y0, x1, y1 = block[:4]
    h = page_height

    # Header / footer heuristic (top/bottom 8% of page)
    if y1 < h * 0.08 or y0 > h * 0.92:
        if len(text.strip()) < 120:
            return "header" if y1 < h * 0.08 else "footer"

    # Table heuristic: many pipe chars or tab chars
    pipe_ratio = text.count("|") / max(len(text), 1)
    tab_ratio  = text.count("\t") / max(len(text), 1)
    if pipe_ratio > 0.05 or tab_ratio > 0.04 or text.count("|") > 4:
        return "table"

    return "text"


def _is_rtl_block(text: str) -> bool:
    arabic = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    alpha  = sum(1 for c in text if c.isalpha())
    return (arabic / alpha) > 0.30 if alpha > 0 else False


# ─────────────────────────────────────────────────────────────────────────────
# Reading-order sorter
# ─────────────────────────────────────────────────────────────────────────────

def sort_reading_order(regions: list[Region], num_cols: int, is_rtl: bool) -> list[Region]:
    """
    Sort regions into correct reading order:
    - For multi-column RTL: right column first, top-to-bottom
    - For multi-column LTR: left column first, top-to-bottom
    - Single column: top-to-bottom
    """
    if num_cols <= 1:
        return sorted(regions, key=lambda r: r.bbox.y0)

    # Assign column index
    all_x = [r.bbox.x0 for r in regions]
    if not all_x:
        return regions

    page_w    = max(r.bbox.x1 for r in regions)
    col_width = page_w / num_cols

    for r in regions:
        cx = (r.bbox.x0 + r.bbox.x1) / 2
        r.col_idx = min(int(cx / col_width), num_cols - 1)

    # RTL: read right (high col_idx) first
    if is_rtl:
        return sorted(regions, key=lambda r: (-r.col_idx, r.bbox.y0))
    else:
        return sorted(regions, key=lambda r: ( r.col_idx, r.bbox.y0))


# ─────────────────────────────────────────────────────────────────────────────
# Main analyzer
# ─────────────────────────────────────────────────────────────────────────────

class LayoutAnalyzer:
    """
    Analyses a single PDF page and returns a PageLayout object containing
    all detected regions in correct reading order.
    """

    def __init__(self, dpi: int = 200):
        self.dpi = dpi

    def analyze(self, pdf_path: str, page_num: int) -> PageLayout:
        doc = None
        try:
            doc  = fitz.open(pdf_path)
            page = doc[page_num]
            pw, ph = page.rect.width, page.rect.height

            layout = PageLayout(
                page_num = page_num,
                width    = pw,
                height   = ph,
            )

            # ── 1. Column detection ─────────────────────────────────────────
            layout.num_cols = detect_columns(page)

            # ── 2. Extract raw blocks ───────────────────────────────────────
            blocks = page.get_text("blocks")

            # ── 3. Check for native text (skip OCR render) ──────────────────
            native_text = page.get_text("text").strip()
            layout.raw_text = native_text
            layout.is_rtl   = _is_rtl_block(native_text)

            regions = []
            for i, blk in enumerate(blocks):
                kind  = _classify_block(blk, pw, ph)
                text  = str(blk[4]).strip() if len(blk) > 4 else ""
                bbox  = BBox(blk[0], blk[1], blk[2], blk[3])
                is_rtl= _is_rtl_block(text) if text else layout.is_rtl

                if kind == "image":
                    layout.has_images = True
                elif kind == "table":
                    layout.has_tables = True

                regions.append(Region(
                    bbox       = bbox,
                    kind       = kind,
                    text       = text,
                    is_rtl     = is_rtl,
                    order      = i,
                ))

            # ── 4. Sort into reading order ───────────────────────────────────
            layout.regions = sort_reading_order(regions, layout.num_cols, layout.is_rtl)

            return layout

        except Exception as exc:
            log.error("LayoutAnalyzer failed page %d: %s", page_num, exc)
            return PageLayout(page_num=page_num, width=595, height=842)

        finally:
            if doc:
                doc.close()
            gc.collect()


# ─────────────────────────────────────────────────────────────────────────────
# OCR overlay (used when native text extraction is insufficient)
# ─────────────────────────────────────────────────────────────────────────────

def ocr_region(
    full_page_img: Image.Image,
    bbox: BBox,
    lang: str = "ara+eng",
    psm_modes: list[int] = None,
) -> str:
    """
    Crop a region from the full page image and run Tesseract OCR on it.
    Tries multiple PSM modes and returns the best result.
    """
    import pytesseract

    if psm_modes is None:
        psm_modes = [1, 3]

    # Crop with small padding
    pad  = 4
    w, h = full_page_img.size
    crop = full_page_img.crop((
        max(0, bbox.x0 - pad), max(0, bbox.y0 - pad),
        min(w, bbox.x1 + pad), min(h, bbox.y1 + pad),
    ))

    # Enhance
    enhanced = enhance_for_ocr(crop)
    del crop; gc.collect()

    best = ""
    for psm in psm_modes:
        cfg = f"--oem 3 --psm {psm} -l {lang}"
        try:
            txt = pytesseract.image_to_string(enhanced, config=cfg)
            if len(txt.strip()) > len(best.strip()):
                best = txt
        except Exception as e:
            log.warning("Tesseract PSM %d error: %s", psm, e)

    del enhanced; gc.collect()
    return best.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Table extractor (simple heuristic via lines)
# ─────────────────────────────────────────────────────────────────────────────

def extract_table_as_text(page: fitz.Page, bbox: BBox) -> str:
    """
    Extract text from a table region using word-level positions to
    reconstruct rows/columns.
    """
    try:
        clip  = fitz.Rect(bbox.to_tuple())
        words = page.get_text("words", clip=clip)
        if not words:
            return ""

        # Group by approximate y-position (row)
        rows: dict[int, list] = {}
        for w in words:
            y_key = round(w[1] / 10) * 10   # bin to 10-pt rows
            rows.setdefault(y_key, []).append(w)

        lines = []
        for y_key in sorted(rows.keys()):
            row_words = sorted(rows[y_key], key=lambda w: w[0])
            lines.append("  |  ".join(rw[4] for rw in row_words))

        return "\n".join(lines)

    except Exception as exc:
        log.warning("Table extract error: %s", exc)
        return ""
