"""
normalizer/extractor.py — Extracts document identification fields.

  - code:          Product codes parsed from the filename.
                   Returns None if more than MAX_PRODUCT_CODES are found
                   (indicates a multi-product document with ambiguous code).
  - product_name:  Highest-confidence enrichment hint, or a cleaned filename fallback.
"""
from __future__ import annotations

from normalizer.config import CODE_PATTERN, MAX_PRODUCT_CODES, _FILENAME_NOISE


# ─── Product code ─────────────────────────────────────────────────────────────

def extract_code(filename_stem: str) -> str | None:
    """Extract product code(s) from the filename stem.

    Returns codes joined by ' | ', or None if none are found or if there are
    more than MAX_PRODUCT_CODES (multi-product document).
    """
    clean = _FILENAME_NOISE.sub("", filename_stem).strip()
    raw_codes = CODE_PATTERN.findall(clean)

    seen: set[str] = set()
    unique: list[str] = []
    for c in raw_codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if not unique:
        return None
    if len(unique) > MAX_PRODUCT_CODES:
        return None

    return " | ".join(unique)


# ─── Document type ────────────────────────────────────────────────────────────

def classify_doc_type(codigo: str | None) -> str:
    """Classify the document as 'manual' or 'document'.

    Documents with a detected product code are treated as product manuals.
    """
    return "manual" if codigo is not None else "document"


# ─── Product name ─────────────────────────────────────────────────────────────

def extract_product_name(
    enrichment: dict,
    filename_stem: str,
    codigo: str | None = None,
) -> str | None:
    """Return the most reliable product name available.

    Priority:
      1. Highest-confidence 'product_name' hint from enrichment.field_hints.
      2. Cleaned filename fallback — only for product manuals (code detected).
         For generic documents the filename is not a product name.
    """
    hints = enrichment.get("field_hints", []) or []
    nombre_hints = [
        h for h in hints
        if h.get("target_field") == "nombre_del_producto"
        and h.get("source_text")
    ]

    if nombre_hints:
        best = max(nombre_hints, key=lambda h: h.get("confidence", 0))
        return best["source_text"].strip()

    if codigo is None:
        return None

    clean = _FILENAME_NOISE.sub("", filename_stem).strip()
    clean = CODE_PATTERN.sub("", clean).strip(" -_")
    return clean if clean else None
