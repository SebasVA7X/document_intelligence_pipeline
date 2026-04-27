from __future__ import annotations

import re
from pathlib import Path

# =========================
# PATHS
# =========================
INPUT_DIR = Path("pdfs")
OUTPUT_DIR = Path("output_json")
IMAGE_DIR = OUTPUT_DIR / "images"

# =========================
# OCR / IMAGE FLAGS
# =========================
# Set OCR_ENABLED=True to allow OCR to be used when the document needs it.
# OCR is only activated for PDFs that have fewer than OCR_AUTO_THRESHOLD
# extractable characters in total (i.e. image-only or scanned documents).
OCR_ENABLED = True
SAVE_IMAGE_CROPS = False
OCR_LANGS = ["es", "en"]
OCR_MIN_TEXT_LEN = 4
# Total extractable-character count below which a PDF is treated as image-only.
OCR_AUTO_THRESHOLD = 100
# chars-per-pt² below which a single page is treated as sparse and receives
# full-page OCR even when the document as a whole is not image-only.
# Catches vector-art / design-tool PDFs (CorelDRAW, Illustrator exports) where
# text is rendered as bezier paths and is largely invisible to PyMuPDF.
# Typical dense text page: ~0.004.  Sparse/vector page: < 0.001.
SPARSE_PAGE_DENSITY = 0.002
# DPI for rendering sparse pages before feeding them to EasyOCR.
OCR_PAGE_DPI = 150

MIN_IMAGE_WIDTH = 120
MIN_IMAGE_HEIGHT = 60
MIN_IMAGE_AREA = 50_000

# =========================
# TABLE DETECTION
# =========================
TABLE_MIN_LINES = 4
TABLE_Y_GROUP_TOL = 10
TABLE_X_MERGE_TOL = 18

# =========================
# HEADER SCORING THRESHOLDS
# =========================
SECTION_HEADER_THRESHOLD = 6
SUBHEADER_THRESHOLD = 5

HEADER_REGEXES = [
    re.compile(r"^\d+(\.\d+)*\s*[A-ZÁÉÍÓÚÑÜ].*"),
    re.compile(r"^[A-ZÁÉÍÓÚÑÜ0-9\s:/\-&®().,]+$"),
]