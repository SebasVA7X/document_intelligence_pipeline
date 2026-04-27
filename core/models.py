"""core/models.py — Shared dataclasses used across all pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TextLine:
    text: str
    page: int
    bbox: list[float]
    x0: float
    y0: float
    x1: float
    y1: float
    font_size_avg: float
    is_bold: bool
    is_upper: bool
    font_name: str = ""
    column_id: int = 0
    header_score: int = 0
    header_level: str = "non_header"   # section_header | subheader | non_header
    is_table_like: bool = False
    toc_page_number: Optional[int] = None


@dataclass
class ImageBlock:
    image_id: str
    page: int
    bbox: list[float]
    width: int
    height: int
    path: Optional[str] = None
    ocr_text: Optional[str] = None
    relevant_for_ocr: bool = False


@dataclass
class TableBlock:
    table_id: str
    page: int
    bbox: list[float]
    section_title_guess: Optional[str]
    columns: list[str]
    rows: list[list[str]]


@dataclass
class Section:
    section_id: str
    page_start: int
    page_end: int
    raw_title: str
    normalized_title: str
    text_items: list[dict[str, Any]] = field(default_factory=list)
