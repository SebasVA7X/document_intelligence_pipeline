"""core/utils.py — Shared utilities used across all pipeline stages."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import fitz
from PIL import Image


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_for_match(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[:\-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def guess_normalized_section(title: str) -> str:
    """Return a clean, lowercased key for a section title.

    Semantic grouping (e.g. "cleaning" → "maintenance") is handled by the
    LLM in the normalization stage, not here.
    """
    return normalize_for_match(title)


def safe_json_dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def image_from_pixmap(pix: fitz.Pixmap) -> Image.Image:
    if pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def bbox_union(lines) -> list[float]:
    return [
        min(l.x0 for l in lines),
        min(l.y0 for l in lines),
        max(l.x1 for l in lines),
        max(l.y1 for l in lines),
    ]


def is_probably_banner(x0: float, y0: float, x1: float, y1: float) -> bool:
    width = x1 - x0
    height = y1 - y0
    aspect_ratio = width / max(height, 1)
    return y0 < 80 and aspect_ratio > 6


def section_to_text_plain(section: dict) -> str:
    """Serialize a section's text_items into a single readable string.

    - subheader / section_header  → own line
    - text items                  → one line each; blank line on column/page
                                    change or vertical gap > 1.5x line height
    - table_text                  → pipe-separated grid with header separator
    - image_ocr                   → indented [OCR] block
    """
    parts: list[str] = []
    prev_column_id: int | None = None
    prev_bbox: list[float] | None = None
    prev_page: int | None = None

    for item in section.get("text_items", []):
        itype = item.get("type", "")

        if itype == "subheader":
            parts.append(f"\n{item.get('text', '').strip()}")
            prev_column_id = item.get("column_id")
            prev_bbox = item.get("bbox")
            prev_page = item.get("page")

        elif itype == "text":
            col  = item.get("column_id", 0)
            text = item.get("text", "").strip()
            bbox = item.get("bbox")
            page = item.get("page")

            paragraph_break = False
            if prev_column_id is not None and col != prev_column_id:
                paragraph_break = True
            elif page is not None and prev_page is not None and page != prev_page:
                paragraph_break = True
            elif bbox is not None and prev_bbox is not None and page == prev_page:
                line_height = bbox[3] - bbox[1]
                gap = bbox[1] - prev_bbox[3]
                if line_height > 0 and gap > line_height * 1.5:
                    paragraph_break = True

            if paragraph_break:
                parts.append("")
            parts.append(text)
            prev_column_id = col
            prev_bbox = bbox
            prev_page = page

        elif itype == "table_text":
            columns = item.get("columns") or []
            rows    = item.get("rows") or []
            if columns or rows:
                parts.append("")
                if columns:
                    parts.append(" | ".join(str(c) for c in columns))
                    parts.append("-" * max(20, sum(len(str(c)) + 3 for c in columns)))
                for row in rows:
                    parts.append(" | ".join(str(c) for c in row))
                parts.append("")

        elif itype == "image_ocr":
            text = item.get("text", "").strip()
            if text:
                parts.append(f"\n[OCR] {text}")

    return "\n".join(parts).strip()
