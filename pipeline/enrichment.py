"""pipeline/enrichment.py — Field hint extraction from document content.

TARGET_FIELDS and FIELD_ALIASES define what structured information to look for.
Adapt these to your domain before running the pipeline.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


# ─── Domain configuration — adapt to your use case ───────────────────────────

TARGET_FIELDS = [
    "product_name",
    "code",
    "origin",
    "description",
    "maintenance",
    "validity",
    "warranty",
    "certifications",
]

FIELD_ALIASES: dict[str, list[str]] = {
    "product_name": [
        "product name",
        "commercial name",
        "denomination",
        "product",
        "item name",
        "nombre del producto",
        "nombre comercial",
        "denominacion",
    ],
    "code": [
        "code",
        "product code",
        "reference",
        "ref",
        "sku",
        "model",
        "item",
        "cod",
        "codigo",
        "referencia",
        "modelo",
    ],
    "origin": [
        "country of origin",
        "origin",
        "made in",
        "manufactured in",
        "origen",
        "pais de origen",
        "fabricado en",
    ],
    "description": [
        "description",
        "product description",
        "general description",
        "overview",
        "introduction",
        "descripcion",
        "descripcion del producto",
        "introduccion",
    ],
    "maintenance": [
        "maintenance",
        "care",
        "cleaning",
        "storage",
        "conservation",
        "mantenimiento",
        "limpieza",
        "almacenamiento",
        "cuidado",
    ],
    "validity": [
        "validity",
        "shelf life",
        "expiry",
        "expiration",
        "vigencia",
        "vida util",
        "caducidad",
    ],
    "warranty": [
        "warranty",
        "guarantee",
        "garantia",
        "garantia del fabricante",
    ],
    "certifications": [
        "certification",
        "certifications",
        "registration",
        "regulatory",
        "compliance",
        "certificate",
        "certificacion",
        "registro sanitario",
        "normativa",
        "iso",
        "ce",
        "fda",
    ],
}

# Section titles that are structural and do not represent a product name
GENERIC_SECTION_TITLES = {
    "introduction",
    "contents",
    "index",
    "instructions",
    "maintenance",
    "warranty",
    "certification",
    "warnings",
    "description",
    "specifications",
    "opening",
    "closing",
    # Spanish equivalents (common in multilingual corpora)
    "introduccion",
    "contenido",
    "indice",
    "indicaciones",
    "instrucciones",
    "garantia",
    "precauciones",
    "advertencias",
    "opening",
    "closing",
}


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[:\-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _snippet(text: str, limit: int = 320) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


def _field_terms(field_name: str) -> list[str]:
    return [_norm(term) for term in FIELD_ALIASES.get(field_name, [])]


def _matches_alias(text: str, field_name: str) -> bool:
    norm = _norm(text)
    return any(term in norm for term in _field_terms(field_name))


def _match_count(text: str, field_name: str) -> int:
    norm = _norm(text)
    return sum(1 for term in _field_terms(field_name) if term in norm)


def _find_label_value(text: str, labels: list[str]) -> tuple[str, str] | None:
    flat = re.sub(r"\s+", " ", text or "").strip()
    for label in labels:
        pattern = re.compile(
            rf"(?:^|[\n.;])\s*{re.escape(label)}\s*[:\-]\s*(.+?)(?=$|[\n.;])",
            re.IGNORECASE,
        )
        match = pattern.search(flat)
        if match:
            value = match.group(1).strip(" :-")
            if value:
                return label, value
    return None


def _find_origin_value(text: str) -> tuple[str, str] | None:
    flat = re.sub(r"\s+", " ", text or "").strip()
    patterns = [
        re.compile(r"(country of origin|origin)\s*[:\-]\s*([^.;,\n]+)", re.IGNORECASE),
        re.compile(r"(made in|manufactured in)\s+([^.;,\n]+)", re.IGNORECASE),
        re.compile(r"(pais de origen|origen)\s*[:\-]\s*([^.;,\n]+)", re.IGNORECASE),
        re.compile(r"(fabricado en|hecho en)\s+([^.;,\n]+)", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(flat)
        if match:
            return match.group(1), match.group(2).strip()
    return None


def _bbox_from_section(section: dict[str, Any]) -> list[float] | None:
    boxes = [item.get("bbox") for item in section.get("text_items", []) if item.get("bbox")]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    ]


def _top_non_toc_pages(pages: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    return [page for page in pages if not page.get("is_toc_page")][:limit]


def _guess_product_name(document: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []

    title_metadata = (document.get("title_metadata") or "").strip()
    if title_metadata and _norm(title_metadata) not in GENERIC_SECTION_TITLES:
        hints.append({
            "target_field": "product_name",
            "confidence": 0.93,
            "hint_type": "document_metadata",
            "page": 1,
            "section_id": None,
            "section_title": None,
            "source_text": title_metadata,
            "supporting_text": None,
            "bbox": None,
            "evidence_scope": "document",
            "matched_terms": ["title_metadata"],
        })

    if hints:
        return hints

    for page in _top_non_toc_pages(pages):
        headers = sorted(
            page.get("header_candidates", []),
            key=lambda item: item.get("score", 0),
            reverse=True,
        )
        for header in headers:
            text = (header.get("text") or "").strip()
            norm = _norm(text)
            word_count = len(text.split())
            if not text or norm in GENERIC_SECTION_TITLES:
                continue
            if any(_matches_alias(text, f) for f in TARGET_FIELDS if f != "product_name"):
                continue
            if 2 <= word_count <= 12 and header.get("score", 0) >= 7:
                hints.append({
                    "target_field": "product_name",
                    "confidence": 0.58,
                    "hint_type": "page_header_candidate",
                    "page": page.get("page"),
                    "section_id": None,
                    "section_title": None,
                    "source_text": text,
                    "supporting_text": None,
                    "bbox": header.get("bbox"),
                    "evidence_scope": "line",
                    "matched_terms": ["header_candidate"],
                })
                return hints

    return hints


def _build_section_hints(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []

    label_value_labels = {
        "product_name":   ["product name", "commercial name", "denomination", "product",
                           "nombre del producto", "nombre comercial", "denominacion"],
        "code":           ["code", "product code", "reference", "ref", "sku", "model",
                           "codigo", "referencia", "modelo"],
        "maintenance":    ["maintenance", "cleaning", "storage", "conservation", "care",
                           "mantenimiento", "limpieza", "almacenamiento", "cuidado"],
        "validity":       ["validity", "shelf life", "expiry",
                           "vigencia", "vida util", "caducidad"],
        "warranty":       ["warranty", "guarantee", "garantia"],
        "certifications": ["registration", "certification", "certificate",
                           "registro sanitario", "certificacion"],
        "description":    ["description", "product description",
                           "descripcion", "descripcion del producto"],
    }

    for section in sections:
        title           = section.get("raw_title", "")
        normalized_title = section.get("normalized_title", "")
        text_plain      = section.get("text_plain", "")
        page            = section.get("page_start")
        bbox            = _bbox_from_section(section)

        for field_name in TARGET_FIELDS:
            title_match_count = max(
                _match_count(title, field_name),
                _match_count(normalized_title, field_name),
            )
            if title_match_count:
                hints.append({
                    "target_field": field_name,
                    "confidence": min(0.78 + 0.08 * title_match_count, 0.95),
                    "hint_type": "section_title_similarity",
                    "page": page,
                    "section_id": section.get("section_id"),
                    "section_title": title,
                    "source_text": title,
                    "supporting_text": _snippet(text_plain),
                    "bbox": bbox,
                    "evidence_scope": "section",
                    "matched_terms": [t for t in _field_terms(field_name) if t in _norm(title)],
                })

        for field_name, labels in label_value_labels.items():
            found = _find_label_value(text_plain, labels)
            if found:
                label, value = found
                hints.append({
                    "target_field": field_name,
                    "confidence": 0.89,
                    "hint_type": "pattern_match",
                    "page": page,
                    "section_id": section.get("section_id"),
                    "section_title": title,
                    "source_text": f"{label}: {value}",
                    "supporting_text": _snippet(value),
                    "bbox": bbox,
                    "evidence_scope": "section",
                    "matched_terms": [_norm(label)],
                })

        origin = _find_origin_value(text_plain)
        if origin:
            label, value = origin
            hints.append({
                "target_field": "origin",
                "confidence": 0.9,
                "hint_type": "pattern_match",
                "page": page,
                "section_id": section.get("section_id"),
                "section_title": title,
                "source_text": f"{label} {value}",
                "supporting_text": _snippet(value),
                "bbox": bbox,
                "evidence_scope": "section",
                "matched_terms": [_norm(label)],
            })

        if not any(h["target_field"] == "description" and h["section_id"] == section.get("section_id") for h in hints):
            norm_title = _norm(title)
            if norm_title in {"introduction", "description", "overview", "introduccion", "descripcion"} and text_plain:
                hints.append({
                    "target_field": "description",
                    "confidence": 0.67,
                    "hint_type": "section_body_similarity",
                    "page": page,
                    "section_id": section.get("section_id"),
                    "section_title": title,
                    "source_text": title,
                    "supporting_text": _snippet(text_plain),
                    "bbox": bbox,
                    "evidence_scope": "section",
                    "matched_terms": [norm_title],
                })

    return hints


def _build_table_hints(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for table in tables:
        columns = table.get("columns") or []
        rows    = table.get("rows") or []
        row_preview = " | ".join(" ".join(str(c) for c in row) for row in rows[:2])
        for column in columns:
            for field_name in TARGET_FIELDS:
                if _matches_alias(str(column), field_name):
                    hints.append({
                        "target_field": field_name,
                        "confidence": 0.84,
                        "hint_type": "table_header_similarity",
                        "page": table.get("page", 0) + 1,
                        "section_id": None,
                        "section_title": None,
                        "source_text": str(column),
                        "supporting_text": _snippet(row_preview),
                        "bbox": table.get("bbox"),
                        "evidence_scope": "table",
                        "matched_terms": [t for t in _field_terms(field_name) if t in _norm(str(column))],
                    })
    return hints


def _dedupe_and_rank(hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple, dict[str, Any]] = {}
    for hint in hints:
        key = (
            hint.get("target_field"),
            hint.get("page"),
            hint.get("section_id"),
            _norm(hint.get("source_text") or ""),
            hint.get("hint_type"),
        )
        existing = deduped.get(key)
        if existing is None or hint.get("confidence", 0) > existing.get("confidence", 0):
            deduped[key] = hint

    ranked: list[dict[str, Any]] = []
    counter = 0
    for field_name in TARGET_FIELDS:
        field_hints = [h for h in deduped.values() if h.get("target_field") == field_name]
        field_hints.sort(key=lambda h: (-h.get("confidence", 0), h.get("page") or 10**9, len(h.get("source_text") or "")))
        for rank, hint in enumerate(field_hints, start=1):
            counter += 1
            enriched = dict(hint)
            enriched["hint_id"] = f"hint_{counter:03d}"
            enriched["rank"] = rank
            ranked.append(enriched)
    return ranked


def build_enrichment(result: dict[str, Any]) -> dict[str, Any]:
    document = result.get("document", {})
    pages    = result.get("pages", [])
    sections = result.get("sections", [])
    tables   = result.get("tables", [])

    hints: list[dict[str, Any]] = []
    hints.extend(_guess_product_name(document, pages))
    hints.extend(_build_section_hints(sections))
    hints.extend(_build_table_hints(tables))

    return {
        "version": "1.0",
        "target_fields": TARGET_FIELDS,
        "field_hints": _dedupe_and_rank(hints),
        "unmapped_hints": [],
    }
