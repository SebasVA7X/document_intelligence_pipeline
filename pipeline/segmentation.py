"""pipeline/segmentation.py — Section segmentation from classified text lines."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from layout.headers import is_index_page
from core.models import TextLine, ImageBlock, TableBlock, Section
from core.utils import guess_normalized_section

# Structural section titles for pre-header and post-content blocks.
_TITLE_OPENING = "OPENING"
_TITLE_CLOSING = "CLOSING"


def sort_reading_order(lines: list[TextLine]) -> list[TextLine]:
    return sorted(lines, key=lambda l: (l.page, l.column_id, round(l.y0, 1), round(l.x0, 1)))


def _line_inside_table(line: TextLine, table_bboxes: list[tuple[int, list[float]]]) -> bool:
    for pg, bbox in table_bboxes:
        if pg != line.page:
            continue
        x0, y0, x1, y1 = bbox
        if line.x0 >= x0 and line.x1 <= x1 and line.y0 >= y0 and line.y1 <= y1:
            return True
    return False


def _enclosing_table_y0(
    line: TextLine,
    table_bboxes: list[tuple[int, list[float]]],
) -> float:
    """Return the y0 of the table bbox that contains this line.

    Used when a section_header sits inside a table (e.g. a title as the first
    row of a bordered table). Using the table's y0 instead of the line's y0
    as the pop threshold keeps the enclosing table pending for the new section.
    """
    for pg, bbox in table_bboxes:
        if pg != line.page:
            continue
        x0, y0, x1, y1 = bbox
        if line.x0 >= x0 and line.x1 <= x1 and line.y0 >= y0 and line.y1 <= y1:
            return y0
    return line.y0


def _table_item(tb: TableBlock) -> dict[str, Any]:
    return {
        "type": "table_text",
        "page": tb.page + 1,
        "bbox": tb.bbox,
        "table_id": tb.table_id,
        "section_title_guess": tb.section_title_guess,
        "columns": tb.columns,
        "rows": tb.rows,
    }


def _pop_tables_before_y(
    page: int,
    y_threshold: float,
    table_rows_lookup: dict[int, list[TableBlock]],
) -> list[TableBlock]:
    """Pop and return all pending tables on `page` whose top edge is above y_threshold."""
    pending   = table_rows_lookup.get(page, [])
    due       = [tb for tb in pending if tb.bbox[1] <= y_threshold]
    remaining = [tb for tb in pending if tb.bbox[1] >  y_threshold]
    if remaining:
        table_rows_lookup[page] = remaining
    else:
        table_rows_lookup.pop(page, None)
    return due


def segment_sections(
    lines: list[TextLine],
    image_blocks: list[ImageBlock],
    table_blocks: list[TableBlock],
) -> list[Section]:
    toc_pages = {}
    for page in sorted({line.page for line in lines}):
        page_lines = [line for line in lines if line.page == page]
        toc_pages[page] = is_index_page(page_lines)

    filtered_lines  = [line for line in lines       if not toc_pages.get(line.page, False)]
    filtered_images = [img  for img  in image_blocks if not toc_pages.get(img.page,  False)]
    filtered_tables = [tb   for tb   in table_blocks  if not toc_pages.get(tb.page,   False)]

    ordered = sort_reading_order(filtered_lines)

    sections: list[Section] = []
    current_title      = _TITLE_OPENING
    current_norm       = "opening"
    current_page_start = ordered[0].page if ordered else 0
    current_items: list[dict[str, Any]] = []
    section_counter    = 0
    first_real_header_seen = False

    table_rows_lookup: dict[int, list[TableBlock]] = defaultdict(list)
    for tb in sorted(filtered_tables, key=lambda t: t.bbox[1]):
        table_rows_lookup[tb.page].append(tb)

    table_bboxes = [(tb.page, tb.bbox) for tb in filtered_tables]

    def flush_section(page_end: int) -> None:
        nonlocal section_counter, current_items, current_title, current_norm, current_page_start
        if not current_items:
            return
        section_counter += 1
        sections.append(
            Section(
                section_id=f"sec_{section_counter}",
                page_start=current_page_start + 1,
                page_end=page_end + 1,
                raw_title=current_title,
                normalized_title=current_norm,
                text_items=current_items.copy(),
            )
        )
        current_items.clear()

    for line in ordered:
        inside_table = _line_inside_table(line, table_bboxes)

        if inside_table and line.header_level != "section_header":
            continue

        pop_y = (_enclosing_table_y0(line, table_bboxes) - 0.001) if inside_table else line.y0
        for tb in _pop_tables_before_y(line.page, pop_y, table_rows_lookup):
            current_items.append(_table_item(tb))

        if line.header_level == "section_header":
            flush_section(line.page)
            current_title      = line.text
            current_norm       = guess_normalized_section(line.text)
            current_page_start = line.page
            current_items      = []
            first_real_header_seen = True

        elif line.header_level == "subheader":
            current_items.append({
                "type": "subheader",
                "page": line.page + 1,
                "bbox": line.bbox,
                "text": line.text,
                "column_id": line.column_id,
            })
        else:
            current_items.append({
                "type": "text",
                "page": line.page + 1,
                "bbox": line.bbox,
                "text": line.text,
                "column_id": line.column_id,
            })

    if ordered:
        last_page = ordered[-1].page
        for page_tables in list(table_rows_lookup.values()):
            for tb in sorted(page_tables, key=lambda t: t.bbox[1]):
                current_items.append(_table_item(tb))
        table_rows_lookup.clear()

        if first_real_header_seen and current_title == _TITLE_OPENING:
            current_title = _TITLE_CLOSING
            current_norm  = "closing"

        flush_section(last_page)

    for img in filtered_images:
        if not img.ocr_text:
            continue
        matching = [s for s in sections if s.page_start <= (img.page + 1) <= s.page_end]
        if matching:
            matching[0].text_items.append({
                "type": "image_ocr",
                "page": img.page + 1,
                "bbox": img.bbox,
                "image_id": img.image_id,
                "text": img.ocr_text,
                "image_path": img.path,
            })

    return sections
