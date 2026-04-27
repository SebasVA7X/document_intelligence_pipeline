from __future__ import annotations

from core.models import TextLine

# Minimum gap between x0 clusters to count as a column separator,
# expressed as a fraction of page width.
_MIN_GAP_RATIO = 0.08

# Minimum fraction of lines that must fall in each detected column.
# Below this the column is considered a stray element, not a real column.
_MIN_COLUMN_RATIO = 0.10


def _find_split_points(x_positions: list[int], page_width: float) -> list[float]:
    """Return the midpoints of all significant gaps between x0 clusters."""
    threshold = page_width * _MIN_GAP_RATIO
    gaps = [
        (x_positions[i + 1] - x_positions[i], x_positions[i], x_positions[i + 1])
        for i in range(len(x_positions) - 1)
    ]
    significant = [(gap, l, r) for gap, l, r in gaps if gap >= threshold]
    # Sort by gap size descending; keep at most 2 splits (→ max 3 columns)
    significant.sort(key=lambda g: -g[0])
    splits = sorted((l + r) / 2 for _, l, r in significant[:2])
    return splits


def _assign_column_ids(lines: list[TextLine], splits: list[float]) -> None:
    """Assign column_id based on which split interval each line falls into."""
    for line in lines:
        col = 0
        for split in splits:
            if line.x0 >= split:
                col += 1
        line.column_id = col


def _column_balance_ok(lines: list[TextLine], n_cols: int) -> bool:
    """Return True if every column has at least _MIN_COLUMN_RATIO of lines."""
    total = len(lines)
    if total == 0:
        return False
    counts = [0] * n_cols
    for line in lines:
        counts[line.column_id] += 1
    return all(c / total >= _MIN_COLUMN_RATIO for c in counts)


def detect_columns(lines: list[TextLine], page_width: float) -> None:
    """Detect 1, 2, or 3 column layout and set line.column_id accordingly.

    Two-phase approach:
    1. Infer split points from non-table-like lines only, so internal table
       columns (e.g. Talla S / Talla M / Talla L) don't pollute the page-
       level gap analysis.
    2. Assign table-like lines to the nearest column by x0 proximity rather
       than by gap thresholds, which are unreliable inside dense table cells.
    """
    if not lines:
        return

    # Phase 1 — use prose lines to find the page column layout
    prose_lines = [l for l in lines if not l.is_table_like]
    # Fall back to all lines if there's not enough prose to work with
    anchor_lines = prose_lines if len(prose_lines) >= 6 else lines

    x_positions = sorted(set(round(l.x0) for l in anchor_lines))

    if len(x_positions) < 3:
        for line in lines:
            line.column_id = 0
        return

    splits = _find_split_points(x_positions, page_width)

    if not splits:
        for line in lines:
            line.column_id = 0
        return

    # Try detected splits; validate balance on prose lines only
    active_splits: list[float] = []
    for n_splits in range(len(splits), 0, -1):
        candidate = splits[:n_splits]
        _assign_column_ids(anchor_lines, candidate)
        if _column_balance_ok(anchor_lines, n_splits + 1):
            active_splits = candidate
            break

    if not active_splits:
        for line in lines:
            line.column_id = 0
        return

    # Apply confirmed splits to all prose lines
    _assign_column_ids(prose_lines, active_splits)

    # Phase 2 — snap table-like lines to nearest column centre by x0
    # Column centres are the midpoints of each interval between boundaries.
    boundaries = [0.0] + active_splits + [page_width]
    col_centres = [
        (boundaries[i] + boundaries[i + 1]) / 2
        for i in range(len(boundaries) - 1)
    ]
    for line in lines:
        if line.is_table_like:
            line.column_id = min(
                range(len(col_centres)),
                key=lambda i: abs(line.x0 - col_centres[i]),
            )