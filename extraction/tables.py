from __future__ import annotations

from typing import Optional

from core.config import TABLE_MIN_LINES, TABLE_Y_GROUP_TOL, TABLE_X_MERGE_TOL
from core.models import TextLine, TableBlock
from core.utils import bbox_union

try:
    import pdfplumber
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    _PDFPLUMBER_AVAILABLE = False


def open_pdfplumber(pdf_path: str):
    """Open a pdfplumber PDF object for the caller to reuse across pages.

    Returns the open pdfplumber.PDF on success, or None if pdfplumber is not
    installed or the file cannot be opened.  The caller is responsible for
    closing the returned object (``pl_pdf.close()`` or ``with`` statement).
    """
    if not _PDFPLUMBER_AVAILABLE:
        return None
    try:
        return pdfplumber.open(pdf_path)
    except Exception:
        return None


# ── pdfplumber primary layer ──────────────────────────────────────────────────

def extract_tables_pdfplumber(
    pl_pdf,
    page_number: int,
    section_title_guess: Optional[str],
    page_lines: list[TextLine],
) -> list[TableBlock]:
    """Detect and extract tables from an already-open pdfplumber PDF.

    ``pl_pdf`` must be a pdfplumber.PDF object opened by the caller so that
    the file is not reopened for every page.

    pdfplumber uses the PDF's actual vector lines for lattice tables (borders)
    and whitespace analysis for stream tables (no borders).  This is far more
    reliable than Y-clustering heuristics for documents with drawn table borders.

    Returns a list of TableBlock.  Each cell's newlines are normalised to a
    single space so multi-line cells come through as one clean string.

    Falls back gracefully to an empty list if pl_pdf is None or the page
    raises an exception.
    """
    if pl_pdf is None:
        return []

    table_blocks: list[TableBlock] = []

    try:
        page = pl_pdf.pages[page_number]
        found = page.find_tables()

        page_area = page.width * page.height

        for t_idx, pt in enumerate(found):
            bbox = list(pt.bbox)          # (x0, y0, x1, y1) in PDF coords

            # Skip tables that span most of the page — these are almost always
            # page borders or two-column layout frames misidentified as tables
            # by pdfplumber's vector-line analysis.  A real data table rarely
            # covers more than 65 % of the page area.
            if page_area > 0:
                table_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                if table_area / page_area > 0.65:
                    continue

            raw  = pt.extract()           # list[list[str|None]]
            if not raw:
                continue

            # Normalise cells: None → "", newlines → space
            def _clean(cell: str | None) -> str:
                if cell is None:
                    return ""
                return " ".join(cell.split())

            cleaned = [[_clean(c) for c in row] for row in raw]

            # First row is the header
            columns   = cleaned[0] if cleaned else []
            data_rows = cleaned[1:] if len(cleaned) > 1 else []

            table_blocks.append(
                TableBlock(
                    table_id=f"table_p{page_number + 1}_pl{t_idx + 1}",
                    page=page_number,
                    bbox=bbox,
                    section_title_guess=section_title_guess,
                    columns=columns,
                    rows=data_rows,
                )
            )

            # Mark TextLines that fall inside this table bbox as table_like
            # so detect_columns and the heuristic fallback both ignore them.
            _mark_lines_in_bbox(page_lines, bbox)

    except Exception:
        return []

    return table_blocks


def _mark_lines_in_bbox(lines: list[TextLine], bbox: list[float]) -> None:
    """Mark all TextLines inside bbox as is_table_like = True."""
    x0, y0, x1, y1 = bbox
    for line in lines:
        if (line.x0 >= x0 - 5 and line.x1 <= x1 + 5
                and line.y0 >= y0 - 5 and line.y1 <= y1 + 5):
            line.is_table_like = True


# ── heuristic fallback layer ──────────────────────────────────────────────────

def cluster_y_lines(
    lines: list[TextLine], tol: float = TABLE_Y_GROUP_TOL
) -> list[list[TextLine]]:
    if not lines:
        return []

    ordered = sorted(lines, key=lambda l: (l.y0, l.x0))
    groups: list[list[TextLine]] = [[ordered[0]]]

    for line in ordered[1:]:
        if abs(line.y0 - groups[-1][-1].y0) <= tol:
            groups[-1].append(line)
        else:
            groups.append([line])

    return groups


def looks_like_table_row(row: list[TextLine]) -> bool:
    if len(row) < 2:
        return False
    shortish = sum(len(l.text.split()) <= 8 for l in row)
    return shortish >= 2


def _cluster_y_lines_for_column(
    lines: list[TextLine], tol: float = TABLE_Y_GROUP_TOL
) -> list[list[TextLine]]:
    """cluster_y_lines scoped to lines sharing the same column_id."""
    if not lines:
        return []

    col_ids = sorted(set(l.column_id for l in lines))
    all_groups: list[list[TextLine]] = []

    for col_id in col_ids:
        col_lines = [l for l in lines if l.column_id == col_id]
        all_groups.extend(cluster_y_lines(col_lines, tol))

    all_groups.sort(key=lambda g: (min(l.y0 for l in g), min(l.x0 for l in g)))
    return all_groups


def mark_table_like_lines(lines: list[TextLine]) -> None:
    """Heuristic pass: mark lines that look like table rows via Y-clustering.

    Resets all is_table_like flags to False before recomputing so that
    each call starts from a clean slate.  This prevents the first pass
    (which runs before detect_columns, with all column_id=0) from
    permanently tagging two-column prose lines as table-like when the
    second pass (column-aware) would correctly clear them.
    """
    for line in lines:
        line.is_table_like = False

    rows      = _cluster_y_lines_for_column(lines, tol=TABLE_Y_GROUP_TOL)
    row_flags = [looks_like_table_row(row) for row in rows]

    for flag, row in zip(row_flags, rows):
        if flag:
            for line in row:
                line.is_table_like = True

    streak_start = None
    for i, flag in enumerate(row_flags + [False]):
        if flag and streak_start is None:
            streak_start = i
        elif not flag and streak_start is not None:
            streak_len = i - streak_start
            for row in rows[streak_start:i]:
                for line in row:
                    line.is_table_like = streak_len >= TABLE_MIN_LINES
            streak_start = None


def _is_continuation_row(
    row: list[TextLine],
    prev_row: list[TextLine],
    x_tol: float = TABLE_X_MERGE_TOL,
) -> bool:
    if not prev_row or not row:
        return False
    if len(row) >= len(prev_row):
        return False
    prev_x0s = [l.x0 for l in prev_row]
    for cell in row:
        if not any(abs(cell.x0 - px) <= x_tol for px in prev_x0s):
            return False
    return True


def _merge_multiline_cells(
    raw_rows: list[list[TextLine]],
) -> list[list[TextLine]]:
    if not raw_rows:
        return []

    merged: list[list[TextLine]] = [raw_rows[0]]

    for row in raw_rows[1:]:
        if _is_continuation_row(row, merged[-1]):
            for cont_cell in row:
                best = min(merged[-1], key=lambda l: abs(l.x0 - cont_cell.x0))
                best.text = best.text.rstrip() + " " + cont_cell.text.strip()
        else:
            merged.append(row)

    return merged


def _flush_table(
    current_rows: list[list[TextLine]],
    page_number: int,
    table_idx: int,
    section_title_guess: Optional[str],
    table_blocks: list[TableBlock],
) -> int:
    if len(current_rows) < TABLE_MIN_LINES:
        return table_idx

    merged_rows = _merge_multiline_cells(current_rows)

    if len(merged_rows) < TABLE_MIN_LINES:
        return table_idx

    table_idx += 1
    parsed_rows = [
        [l.text for l in sorted(row, key=lambda l: l.x0)]
        for row in merged_rows
    ]
    columns   = parsed_rows[0] if parsed_rows else []
    data_rows = parsed_rows[1:] if len(parsed_rows) > 1 else []
    all_lines = [l for row in current_rows for l in row]

    table_blocks.append(
        TableBlock(
            table_id=f"table_p{page_number + 1}_{table_idx}",
            page=page_number,
            bbox=bbox_union(all_lines),
            section_title_guess=section_title_guess,
            columns=columns,
            rows=data_rows,
        )
    )
    return table_idx


def build_table_blocks_heuristic(
    page_lines: list[TextLine],
    page_number: int,
    section_title_guess: Optional[str],
) -> list[TableBlock]:
    """Heuristic fallback: detect tables via Y-clustering when pdfplumber
    found nothing on this page."""
    col_ids     = sorted(set(l.column_id for l in page_lines))
    table_blocks: list[TableBlock] = []
    table_idx   = 0

    for col_id in col_ids:
        col_lines    = [l for l in page_lines if l.column_id == col_id]
        rows         = cluster_y_lines(col_lines, tol=TABLE_Y_GROUP_TOL)
        current_rows: list[list[TextLine]] = []

        for row in rows:
            if all(l.is_table_like for l in row):
                current_rows.append(row)
            else:
                if current_rows:
                    table_idx = _flush_table(
                        current_rows, page_number, table_idx,
                        section_title_guess, table_blocks,
                    )
                    current_rows = []

        if current_rows:
            table_idx = _flush_table(
                current_rows, page_number, table_idx,
                section_title_guess, table_blocks,
            )

    return table_blocks


# ── public entry point ────────────────────────────────────────────────────────

def build_table_blocks(
    page_lines: list[TextLine],
    page_number: int,
    section_title_guess: Optional[str],
    pl_pdf=None,
) -> list[TableBlock]:
    """Detect tables on a page.

    ``pl_pdf`` is an already-open pdfplumber.PDF object shared across all pages
    of the document.  Pass None to skip pdfplumber and use heuristics only.

    Strategy:
    1. Try pdfplumber (primary) — reliable for tables with drawn borders,
       also handles borderless tables via whitespace analysis.
    2. Fall back to heuristic Y-clustering if pdfplumber found nothing or
       pl_pdf is None.

    pdfplumber also marks the TextLines inside detected table bboxes as
    is_table_like=True, so detect_columns and header scoring automatically
    ignore them on the next pass.
    """
    if pl_pdf is not None:
        pl_tables = extract_tables_pdfplumber(
            pl_pdf, page_number, section_title_guess, page_lines
        )
        if pl_tables:
            return pl_tables

    # pdfplumber found nothing or was not provided — fall back to heuristics
    return build_table_blocks_heuristic(
        page_lines, page_number, section_title_guess
    )