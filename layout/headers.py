"""layout/headers.py — Header detection and TOC extraction."""
from __future__ import annotations

import re
from collections import Counter

from core.config import (
    HEADER_REGEXES, SECTION_HEADER_THRESHOLD, SUBHEADER_THRESHOLD,
)
from core.models import TextLine
from core.utils import normalize_for_match

# Detects dot-leader TOC lines: "Introduction ......... 55"
_INDEX_LINE_RE = re.compile(r"\.{3,}\s*\d{1,4}\s*$")
# Captures title and page number from a dot-leader TOC line
_DOT_TOC_ENTRY_RE = re.compile(r"^(.+?)\s*\.{2,}\s*(\d{1,4})\s*$")

_TOP_TOC_TITLES = {
    "contents",
    "table of contents",
    "index",
    "contenido",
    "contenidos",
    "indice",
    "índice",
    "tabla de contenido",
    "tabla de contenidos",
    "indice general",
    "índice general",
}

# Numbered header: "1. Introduction" / "3.2 Something"
_NUMBERED_HEADER_RE = re.compile(r"^\d+(\.\d+)*\s+\S")

# Words that, when heading a short line (≤10 words), always indicate a real
# section title — regardless of the accumulated score.
# Generic enough to apply across most document types and industries.
_UNIVERSAL_SECTION_STARTS: frozenset[str] = frozenset({
    # document structure
    "introduction", "overview", "summary", "abstract", "conclusion", "conclusions",
    "appendix", "glossary", "references", "bibliography", "index",
    # instructions / usage
    "instructions", "installation", "operation", "usage", "setup", "configuration",
    "getting started", "quick start",
    # safety / warnings
    "warning", "warnings", "caution", "cautions", "safety", "danger", "notice",
    "important", "note", "notes",
    # maintenance / support
    "maintenance", "cleaning", "troubleshooting", "support", "warranty",
    "disposal", "storage",
    # product info
    "specifications", "description", "features", "contents", "components",
    "parts", "accessories", "requirements", "compatibility",
    # legal / regulatory
    "compliance", "certifications", "regulatory", "legal", "disclaimer",
    "copyright", "license",
})


def is_index_line(text: str) -> bool:
    return bool(_INDEX_LINE_RE.search(text))


def _is_toc_title_line(line: TextLine) -> bool:
    norm = normalize_for_match(line.text)
    return norm in _TOP_TOC_TITLES and line.y0 <= 300


_NUMBER_ONLY_RE = re.compile(r"^\d{1,4}$")


def _looks_like_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}", text.strip()))


def _group_lines_by_y(lines: list[TextLine], tol: float = 3) -> list[list[TextLine]]:
    """Group TextLines sharing the same visual row.

    Uses a strict tolerance (3 pts) and anchors each group to its FIRST line
    to prevent chaining across interleaved columns.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda l: (round(l.y0, 1), round(l.x0, 1)))
    groups: list[list[TextLine]] = [[ordered[0]]]

    for line in ordered[1:]:
        group_y0 = groups[-1][0].y0
        if abs(line.y0 - group_y0) <= tol:
            groups[-1].append(line)
        else:
            groups.append([line])

    return groups


def _looks_like_toc_row(row: list[TextLine], page_width: float) -> bool:
    if len(row) < 2 or page_width <= 0:
        return False

    text_candidates = [
        l for l in row
        if not _looks_like_page_number(l.text)
        and len(l.text.strip()) >= 3
    ]
    number_candidates = [
        l for l in row
        if _looks_like_page_number(l.text)
    ]

    if not text_candidates or not number_candidates:
        return False

    left_text = min(text_candidates, key=lambda l: l.x0)
    right_number = max(number_candidates, key=lambda l: l.x0)

    gap = right_number.x0 - left_text.x1
    return gap > page_width * 0.10


# ---------------------------------------------------------------------------
# Spatial TOC extraction
# ---------------------------------------------------------------------------
# Handles the format where title and page-number are separate TextLine objects,
# e.g.:
#   SECTION TITLE          (x0=22, y0=55)   ← title
#                     2    (x0=175, y0=55)  ← number at same y
#   LONG SECTION TITLE...  (x0=22, y0=157)  ← wrapped title, no number
#   CONTINUED              (x0=22, y0=168)  ← title continuation
#                    11    (x0=172, y0=168) ← number

def _extract_spatial_toc_entries(
    lines: list[TextLine],
) -> list[dict[str, object]]:
    """Extract TOC entries by spatial pairing.

    Strategy: sort number-only lines by y0. For each number line N_i, collect
    all title lines whose y0 falls in the half-open interval
    (y0(N_{i-1}), y0(N_i) + tol]. This handles wrapped titles naturally.
    """
    if not lines:
        return []

    number_lines = [l for l in lines if _NUMBER_ONLY_RE.fullmatch(l.text.strip())]
    if len(number_lines) < 3:
        return []

    num_x0s = sorted(l.x0 for l in number_lines)
    median_num_x0 = num_x0s[len(num_x0s) // 2]
    number_lines = [l for l in number_lines if abs(l.x0 - median_num_x0) < 60]
    if len(number_lines) < 3:
        return []

    cand = [
        l for l in lines
        if not _NUMBER_ONLY_RE.fullmatch(l.text.strip())
        and len(l.text.strip()) >= 3
        and l.x0 < median_num_x0
        and not _is_toc_title_line(l)
    ]
    if not cand:
        return []

    title_x0s = sorted(l.x0 for l in cand)
    median_title_x0 = title_x0s[len(title_x0s) // 2]
    title_lines_all = [l for l in cand if abs(l.x0 - median_title_x0) < 30]
    if not title_lines_all:
        return []

    number_lines_sorted = sorted(number_lines, key=lambda l: l.y0)
    entries: list[dict[str, object]] = []

    for i, num_line in enumerate(number_lines_sorted):
        num_y0 = num_line.y0
        lower_bound = number_lines_sorted[i - 1].y0 if i > 0 else -1e9

        direct = [
            l for l in title_lines_all
            if abs(l.y0 - num_y0) <= 3
            and l.y0 > lower_bound + 1
            and l.x1 <= num_line.x0 + 5
        ]
        if not direct:
            continue

        direct_y0_min = min(l.y0 for l in direct)
        direct_x0    = min(l.x0 for l in direct)

        def _ends_with_number(line: TextLine) -> bool:
            words = line.text.strip().split()
            return bool(words and _NUMBER_ONLY_RE.fullmatch(words[-1]))

        def _has_own_number(line: TextLine) -> bool:
            return any(abs(n.y0 - line.y0) <= 3 for n in number_lines_sorted)

        continuation = [
            l for l in title_lines_all
            if l.y0 > lower_bound + 1
            and l.y0 < direct_y0_min - 1
            and abs(l.x0 - direct_x0) < 10
            and not _has_own_number(l)
            and not _ends_with_number(l)
        ]

        all_titles = sorted(continuation, key=lambda l: l.y0) + sorted(direct, key=lambda l: l.x0)
        title_text = " ".join(l.text.strip() for l in all_titles).strip()
        if not title_text:
            continue

        all_in_entry = all_titles + [num_line]
        entries.append({
            "type": "toc_entry",
            "page": all_titles[0].page + 1,
            "text": title_text,
            "target_page": int(num_line.text.strip()),
            "bbox": [
                min(l.x0 for l in all_in_entry),
                min(l.y0 for l in all_in_entry),
                max(l.x1 for l in all_in_entry),
                max(l.y1 for l in all_in_entry),
            ],
        })

    return entries


def _has_spatial_toc_pattern(lines: list[TextLine], min_entries: int = 3) -> bool:
    """Quick check: enough number-only lines at a consistent x0 to be a TOC."""
    number_lines = [l for l in lines if _NUMBER_ONLY_RE.fullmatch(l.text.strip())]
    if len(number_lines) < min_entries:
        return False
    num_x0s = sorted(l.x0 for l in number_lines)
    median = num_x0s[len(num_x0s) // 2]
    clustered = sum(1 for x0 in num_x0s if abs(x0 - median) < 60)
    return clustered >= min_entries


def extract_toc_entries(lines: list[TextLine]) -> list[dict[str, object]]:
    if not lines or not is_index_page(lines):
        return []

    page_width = max((line.x1 for line in lines), default=0)

    # Path 3 — spatial extraction: most general, handles wrapped titles.
    if _has_spatial_toc_pattern(lines):
        spatial = _extract_spatial_toc_entries(lines)
        if spatial:
            return spatial

    # Paths 1 & 2 — fallback for single-TextLine and same-Y multi-TextLine formats.
    entries: list[dict[str, object]] = []
    handled_ids: set[int] = set()

    # Path 1 — single-TextLine entries (dot-leader or space-gap format)
    for line in lines:
        m = _DOT_TOC_ENTRY_RE.match(line.text)
        if m:
            title = m.group(1).strip()
            if title:
                entries.append({
                    "type": "toc_entry",
                    "page": line.page + 1,
                    "text": title,
                    "target_page": int(m.group(2)),
                    "bbox": line.bbox,
                })
                handled_ids.add(id(line))
            continue

        if line.toc_page_number is not None:
            title = re.sub(r"\s+\d{1,4}\s*$", "", line.text).strip()
            if title:
                entries.append({
                    "type": "toc_entry",
                    "page": line.page + 1,
                    "text": title,
                    "target_page": line.toc_page_number,
                    "bbox": line.bbox,
                })
                handled_ids.add(id(line))

    # Path 2 — multi-TextLine entries at the same Y
    for row in _group_lines_by_y(lines):
        if any(id(l) in handled_ids for l in row):
            continue
        if not _looks_like_toc_row(row, page_width):
            continue

        text_candidates = [
            l for l in row
            if not _looks_like_page_number(l.text)
            and len(l.text.strip()) >= 3
        ]
        number_candidates = [
            l for l in row
            if _looks_like_page_number(l.text)
        ]
        if not text_candidates or not number_candidates:
            continue

        number_line = max(number_candidates, key=lambda l: l.x0)
        text_lines = sorted(
            [l for l in text_candidates if l.x1 <= number_line.x0 + 5],
            key=lambda l: l.x0,
        )
        if not text_lines:
            continue

        entry_lines = text_lines + [number_line]
        handled_ids.update(id(l) for l in entry_lines)
        entries.append({
            "type": "toc_entry",
            "page": text_lines[0].page + 1,
            "text": " ".join(l.text.strip() for l in text_lines).strip(),
            "target_page": int(number_line.text.strip()),
            "bbox": [
                min(l.x0 for l in entry_lines),
                min(l.y0 for l in entry_lines),
                max(l.x1 for l in entry_lines),
                max(l.y1 for l in entry_lines),
            ],
        })

    return entries


def is_index_page(lines: list[TextLine], threshold: float = 0.35) -> bool:
    if not lines:
        return False

    index_count = sum(
        1 for l in lines
        if is_index_line(l.text) or l.toc_page_number is not None
    )
    if (index_count / len(lines)) >= threshold:
        return True

    if not any(_is_toc_title_line(line) for line in lines):
        return False

    page_width = max((line.x1 for line in lines), default=0)
    toc_rows = sum(
        1 for row in _group_lines_by_y(lines)
        if _looks_like_toc_row(row, page_width)
    )
    if toc_rows >= 5:
        return True

    return _has_spatial_toc_pattern(lines, min_entries=3)


def compute_body_font_size(lines: list[TextLine]) -> float:
    sizes = [round(line.font_size_avg, 1) for line in lines if line.font_size_avg > 0]
    if not sizes:
        return 0
    return Counter(sizes).most_common(1)[0][0]


def compute_body_font_name(lines: list[TextLine]) -> str:
    names = [l.font_name for l in lines if l.font_name and not l.is_bold]
    if not names:
        names = [l.font_name for l in lines if l.font_name]
    if not names:
        return ""
    return Counter(names).most_common(1)[0][0]


def score_line(
    line: TextLine,
    body_font_size: float,
    body_font_name: str = "",
    high_table_density: bool = False,
) -> int:
    text = line.text.strip()
    word_count = len(text.split())

    if not text:
        return -999

    if is_index_line(text):
        return -999

    # --- Hard exclusions ---
    if len(text) <= 2 and not re.match(r"^\d+(\.\d+)*$", text):
        return -999
    if re.match(r"^\d+$", text):
        return -999
    if re.match(r"^[ivxlcdm]+$", text.lower()):
        return -999
    # 6+ uppercase/digit/dash sequences without spaces → likely a product code.
    # Exempt if the line has strong structural signals (bold or larger font).
    if re.match(r"^[A-Z0-9\-]{6,}$", text):
        if not (line.is_bold or (body_font_size > 0 and line.font_size_avg > body_font_size + 0.5)):
            return -999
    if re.match(r"^[A-Z]{1,4}\d{1,4}$", text):
        return -999
    if re.match(r"^\(?[A-Z]\)?$", text):
        return -999
    if text.startswith(("•", "-", "*", "▪")):
        return -999
    if re.match(r"^[a-zA-Z]\)\s", text) or re.match(r"^[a-zA-Z]\)$", text):
        return -999
    if re.search(r'\b(of the|of|and|or|the|a)\s*$', text.lower()):
        return -999

    # Universal section titles: short line whose first word(s) belong to the
    # canonical vocabulary → always a section_header regardless of format score.
    if word_count <= 10:
        _norm = normalize_for_match(text)
        _words = _norm.split()
        _first  = _words[0] if _words else ""
        _first2 = " ".join(_words[:2]) if len(_words) >= 2 else ""
        if _first in _UNIVERSAL_SECTION_STARTS or _first2 in _UNIVERSAL_SECTION_STARTS:
            return 10

    score = 0

    is_numbered_header = bool(_NUMBERED_HEADER_RE.match(text))

    # --- Penalties ---
    if line.is_table_like:
        if line.is_bold and line.is_upper:
            score -= 2
        else:
            score -= 6
    if high_table_density and not is_numbered_header:
        score -= 4
    if word_count > 18:
        score -= 3
    if re.match(r"^\d+\.\s", text) and not is_numbered_header:
        score -= 2
    if re.match(r"^[A-Z0-9\-\.\[\]/]+$", text) and word_count <= 3:
        score -= 3
    if word_count <= 3 and not line.is_upper and not text.endswith(":"):
        score -= 3
    if word_count <= 2 and not line.is_upper and not any(rx.match(text) for rx in HEADER_REGEXES):
        score -= 2
    if line.y0 > 400 and word_count <= 3 and not is_numbered_header:
        score -= 3

    # --- Positives ---
    if line.font_size_avg > body_font_size + 0.5:
        score += 3
    if line.is_bold:
        score += 2
    if body_font_name and line.font_name and line.font_name != body_font_name:
        score += 2
    if line.is_upper:
        score += 2
    if 1 <= word_count <= 10:
        score += 1
    if text.endswith(":"):
        score += 1
    if not text.endswith("."):
        score += 1
    if any(rx.match(text) for rx in HEADER_REGEXES):
        score += 2
    if is_numbered_header:
        score += 1

    return score


def _adaptive_thresholds(
    scores: list[int],
    base_section: int,
    base_subheader: int,
) -> tuple[int, int]:
    """Compute per-page header thresholds from the score distribution.

    Uses fixed config thresholds as a floor. Relaxes by exactly 1 point only
    when the best candidate scored base_section - 1 (narrowly missed threshold).
    """
    if not scores:
        return base_section, base_subheader

    positive = [s for s in scores if s > 0]
    if not positive:
        return base_section, base_subheader

    max_score = max(positive)

    if max_score >= base_section:
        return base_section, base_subheader

    if max_score == base_section - 1:
        return max_score, max(1, max_score - 1)

    return base_section, base_subheader


def assign_header_levels(lines: list[TextLine], table_count: int = 0) -> None:
    if is_index_page(lines):
        for line in lines:
            line.header_score = -999
            line.header_level = "non_header"
        return

    body_font      = compute_body_font_size(lines)
    body_font_name = compute_body_font_name(lines)
    high_table_density = table_count > 2

    for line in lines:
        line.header_score = score_line(line, body_font, body_font_name, high_table_density)

    all_scores = [l.header_score for l in lines]
    section_t, subheader_t = _adaptive_thresholds(
        all_scores, SECTION_HEADER_THRESHOLD, SUBHEADER_THRESHOLD
    )

    # Low-density pages (covers, separators): raise the bar to avoid promoting
    # layout labels. Genuine headers still score high enough to clear it.
    if len(lines) < 20:
        section_t   = max(section_t,   SECTION_HEADER_THRESHOLD + 3)
        subheader_t = max(subheader_t, SUBHEADER_THRESHOLD + 2)

    for line in lines:
        if line.header_score >= section_t:
            line.header_level = "section_header"
        elif line.header_score >= subheader_t:
            line.header_level = "subheader"
        else:
            line.header_level = "non_header"
