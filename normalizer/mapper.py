"""
normalizer/mapper.py — Builds the normalized row for a document.

Flow:
  1. For each section, if its normalized_title matches one of the dynamic
     columns, assign it directly (no LLM call).
  2. Sections whose normalized_title is not in the columns are accumulated
     and sent to the LLM (raw_titles only).
  3. The LLM returns a JSON {raw_title: column}; it is parsed and validated.
  4. With the complete mapping, build the row: for each column, concatenate
     the text of all sections that map to it, preceded by the raw_title in
     brackets.
  5. Anything that did not fit any column goes to the last column (fallback).
"""
from __future__ import annotations

from typing import Any

from normalizer.assembler import build_prompt, parse_llm_json
from normalizer.config import SKIP_TITLES
from normalizer.extractor import classify_doc_type, extract_code, extract_product_name

# Separator between sections concatenated within the same column
_SEP = "\n\n"


# ─── helpers ─────────────────────────────────────────────────────────────────

def _section_text(sec: dict) -> str:
    """Return the text block of a section with its title as a header."""
    raw_title = (sec.get("raw_title") or "").strip()
    text = (sec.get("text_plain") or "").strip()
    if raw_title and text:
        return f"[{raw_title}]\n{text}"
    return text or f"[{raw_title}]"


def _validate_column(col: str, columns: list[str]) -> str:
    """Ensure the column returned by the LLM is valid."""
    col = col.strip().lower()
    return col if col in columns else columns[-1]


# ─── section classification ───────────────────────────────────────────────────

def classify_sections(
    sections: list[dict],
    llm_client,
    filename: str,
    columns: list[str],
    title_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return {raw_title: standard_column} for all sections.

    Resolution order:
      1. Corpus map (title_map): normalized_title → canonical column.
         Generated once by the LLM from section_freq.json.
      2. Direct match: normalized_title is itself a column name.
      3. First-word match: avoids LLM calls for known title variants.
      4. LLM per-document: for raw_titles not covered by the corpus map.
      5. Fallback (no LLM): last column (additional_content).

    Sections in SKIP_TITLES or with raw_title < 4 chars are ignored.
    """
    columns_set = set(columns)
    fallback    = columns[-1]
    _title_map  = title_map or {}

    resolved: dict[str, str] = {}
    needs_llm: list[str] = []

    for sec in sections:
        raw  = (sec.get("raw_title") or "").strip()
        norm = (sec.get("normalized_title") or "").strip()

        if not raw or raw in SKIP_TITLES or len(raw) < 4:
            continue

        # 1. Corpus map
        if norm in _title_map:
            col = _title_map[norm]
            if col in columns_set:
                resolved[raw] = col
                continue

        # 2. Direct column match
        if norm in columns_set:
            resolved[raw] = norm
            continue

        # 3. First-word match: "installation guide" → column "installation"
        _norm_first = norm.split()[0] if norm else ""
        if _norm_first:
            for col in columns[:-1]:
                if _norm_first == col or _norm_first == col.split()[0]:
                    resolved[raw] = col
                    break
            else:
                needs_llm.append(raw)
            continue

        needs_llm.append(raw)

    if needs_llm and llm_client is not None:
        system, user = build_prompt(needs_llm, filename, columns)
        raw_response = llm_client.complete(system, user)
        llm_map = parse_llm_json(raw_response)

        for raw_title in needs_llm:
            col_raw = llm_map.get(raw_title, fallback)
            resolved[raw_title] = _validate_column(col_raw, columns)
    else:
        for raw_title in needs_llm:
            resolved[raw_title] = fallback

    return resolved


# ─── row builder ─────────────────────────────────────────────────────────────

def build_row(
    json_data: dict[str, Any],
    llm_client,
    action: str,
    columns: list[str],
    title_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the normalized row for a document.

    Args:
        json_data:  Slim document JSON (extractor output).
        llm_client: LLMClient instance (or None for no-LLM mode).
        action:     "process" | "process_primary_language" (from triage_control).
        columns:    Dynamic column list determined by router.
        title_map:  Corpus-level mapping from router.

    Returns:
        Dict with identification fields + dynamic columns + metadata.
    """
    doc        = json_data.get("document", {})
    sections   = json_data.get("sections", [])
    enrichment = json_data.get("enrichment", {})
    filename   = doc.get("file_name", "")
    stem       = filename.rsplit(".", 1)[0] if "." in filename else filename

    code            = extract_code(stem)
    product_name    = extract_product_name(enrichment, stem, code=code)
    doc_type        = classify_doc_type(code)

    mapping = classify_sections(sections, llm_client, filename, columns, title_map)

    cols_data: dict[str, list[str]] = {col: [] for col in columns}

    sec_by_title: dict[str, dict] = {}
    for sec in sections:
        raw = (sec.get("raw_title") or "").strip()
        if raw and raw not in SKIP_TITLES:
            sec_by_title[raw] = sec

    for raw_title, col in mapping.items():
        sec = sec_by_title.get(raw_title)
        if sec is None:
            continue
        block = _section_text(sec)
        if block:
            cols_data[col].append(block)

    row: dict[str, Any] = {
        "filename":         filename,
        "code":              code,
        "product_name":     product_name,
        "doc_type":         doc_type,
        "doc_type":         json_data.get("_doc_type", ""),
        "quality_score":    json_data.get("_quality_score"),
        "pages":            doc.get("page_count"),
        "action":            action,
    }

    for col in columns:
        row[col] = _SEP.join(cols_data[col]) if cols_data[col] else ""

    return row