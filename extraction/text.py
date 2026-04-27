from __future__ import annotations

import fitz
import re

from collections import Counter
from core.models import TextLine
from core.utils import clean_text

# Detects a right-aligned page number: last span is a short number that sits
# significantly to the right of the preceding text — the "spaces" TOC format.
_SHORT_NUMBER_RE = re.compile(r"^\d{1,4}$")


def _detect_toc_page_number(spans: list[dict], line_x0: float, line_x1: float) -> int | None:
    """Return the page number if the last span looks like a right-aligned TOC number.

    Conditions:
    - At least 2 spans in the line.
    - Last span text is a short integer (1-4 digits).
    - Last span's x0 is at least 15 pts to the right of the preceding content's x1.
    - Last span starts in the right 50% of the line's total width.
    """
    if len(spans) < 2:
        return None
    last_span = spans[-1]
    last_text = last_span.get("text", "").strip()
    if not _SHORT_NUMBER_RE.fullmatch(last_text):
        return None
    prev_x1 = max(s["bbox"][2] for s in spans[:-1])
    last_x0 = last_span["bbox"][0]
    line_width = line_x1 - line_x0
    if line_width <= 0:
        return None
    gap = last_x0 - prev_x1
    relative_pos = (last_x0 - line_x0) / line_width
    if gap > 15 and relative_pos > 0.4:
        return int(last_text)
    return None


def extract_text_lines(page: fitz.Page, page_number: int) -> list[TextLine]:
    text_dict = page.get_text("dict")
    lines: list[TextLine] = []

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue

            line_text = clean_text(" ".join(span.get("text", "") for span in spans))
            if not line_text:
                continue

            x0 = min(span["bbox"][0] for span in spans)
            y0 = min(span["bbox"][1] for span in spans)
            x1 = max(span["bbox"][2] for span in spans)
            y1 = max(span["bbox"][3] for span in spans)

            sizes = [span.get("size", 0) for span in spans if span.get("size")]
            font_size_avg = sum(sizes) / len(sizes) if sizes else 0

            # Prefer the PDF bold flag (bit 4 of span["flags"]) over font-name
            # heuristics, which fail for custom or embedded fonts.
            # Fall back to name-based detection for PDFs that don't set flags
            # correctly (e.g. some scanned/re-exported files).
            bold_by_flag = any(span.get("flags", 0) & 16 for span in spans)
            font_names_str = " ".join(span.get("font", "") for span in spans).lower()
            bold_by_name = any(
                kw in font_names_str for kw in ("bold", "black", "heavy", "demi")
            )
            is_bold = bold_by_flag or bold_by_name

            # Dominant font name (most common span font, lowercased)
            font_names = [span.get("font", "") for span in spans if span.get("font")]
            font_name = Counter(font_names).most_common(1)[0][0].lower() if font_names else ""

            alpha_chars = [c for c in line_text if c.isalpha()]
            is_upper = (
                bool(alpha_chars)
                and sum(c.isupper() for c in alpha_chars) / len(alpha_chars) > 0.8
            )

            tl = TextLine(
                text=line_text,
                page=page_number,
                bbox=[x0, y0, x1, y1],
                x0=x0, y0=y0, x1=x1, y1=y1,
                font_size_avg=font_size_avg,
                is_bold=is_bold,
                is_upper=is_upper,
                font_name=font_name,
            )

            # Detect "spaces" TOC format: text .............. 55 (no dots, just gap)
            tl.toc_page_number = _detect_toc_page_number(spans, x0, x1)

            lines.append(tl)

    return lines
