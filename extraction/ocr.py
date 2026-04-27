"""extraction/ocr.py — EasyOCR integration for scanned and vector-art PDFs."""
from __future__ import annotations

import tempfile
from pathlib import Path as _Path

from core.config import OCR_ENABLED, OCR_LANGS, OCR_PAGE_DPI
from core.utils import clean_text
import torch

try:
    import easyocr
except ImportError:
    easyocr = None


def get_ocr_reader():
    if not OCR_ENABLED:
        return None
    if easyocr is None:
        print("EasyOCR is not installed. OCR will be skipped.")
        return None
    return easyocr.Reader(OCR_LANGS, gpu=torch.cuda.is_available())


def run_ocr_on_image(reader, image_path: _Path) -> str:
    if reader is None:
        return ""
    result = reader.readtext(str(image_path), detail=0, paragraph=True)
    return clean_text(" ".join(result).strip())


def _page_pixmap_to_tmp(page, dpi: int) -> _Path:
    """Render a page to a temporary PNG and return its path."""
    import fitz
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = _Path(tmp.name)
    pix.save(str(tmp_path))
    return tmp_path


def ocr_page_as_image(reader, page) -> str:
    """Render the full page as an image and run OCR on it (single text blob).

    Used as a last-resort fallback when ocr_page_as_lines returns no results.
    The output is a single unstructured text block with no section information.
    """
    if reader is None:
        return ""
    tmp_path = _page_pixmap_to_tmp(page, dpi=OCR_PAGE_DPI)
    try:
        return run_ocr_on_image(reader, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def ocr_page_as_lines(reader, page, dpi: int = OCR_PAGE_DPI) -> list[tuple]:
    """Render the page and run OCR in detail mode, returning individual lines.

    Unlike ocr_page_as_image, returns each text line with its bounding box in
    PDF coordinates, so header detection and segmentation work normally on
    OCR'd text.

    Returns:
        List of (text, x0, y0, x1, y1, font_size_est) tuples in PDF points.
        font_size_est is estimated as 75% of the bbox height.
    """
    if reader is None:
        return []

    scale = dpi / 72.0
    tmp_path = _page_pixmap_to_tmp(page, dpi=dpi)
    try:
        # detail=1: returns [(bbox_corners, text, confidence), ...]
        # paragraph=False: individual lines, not grouped blocks
        raw = reader.readtext(str(tmp_path), detail=1, paragraph=False)
    finally:
        tmp_path.unlink(missing_ok=True)

    items: list[tuple] = []
    for (bbox_corners, text, conf) in raw:
        text = clean_text(text.strip())
        if not text or conf < 0.3:
            continue
        # bbox_corners: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] in pixels
        xs = [pt[0] for pt in bbox_corners]
        ys = [pt[1] for pt in bbox_corners]
        x0 = min(xs) / scale
        y0 = min(ys) / scale
        x1 = max(xs) / scale
        y1 = max(ys) / scale
        font_size_est = (y1 - y0) * 0.75
        items.append((text, x0, y0, x1, y1, font_size_est))

    return items
