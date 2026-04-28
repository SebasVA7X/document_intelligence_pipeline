"""main.py — Stage 1 entry point: extract all PDFs, then run triage.

Usage:
    python main.py
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import fitz

from core.config import INPUT_DIR, OUTPUT_DIR, IMAGE_DIR, OCR_AUTO_THRESHOLD, SPARSE_PAGE_DENSITY
from extraction import (
    extract_text_lines,
    process_images,
    mark_table_like_lines,
    build_table_blocks,
    open_pdfplumber,
)
from pipeline.enrichment import build_enrichment
from layout import detect_columns, assign_header_levels, extract_toc_entries
from core.models import TextLine, ImageBlock, TableBlock
from extraction.ocr import get_ocr_reader, ocr_page_as_image, ocr_page_as_lines
from pipeline.segmentation import segment_sections
from triage.triage import run_triage
from core.utils import safe_json_dump, section_to_text_plain
from tqdm import tqdm


def _is_image_only(doc: fitz.Document, threshold: int = OCR_AUTO_THRESHOLD) -> bool:
    """Return True when the document has fewer than `threshold` extractable characters."""
    total_chars = sum(len(doc[p].get_text()) for p in range(len(doc)))
    return total_chars < threshold


def _is_sparse_page(page: fitz.Page, threshold: float = SPARSE_PAGE_DENSITY) -> bool:
    """Return True when a page has very few extractable characters per pt².

    Catches vector-art / design-tool exported PDFs where text is rendered as
    bezier paths. PyMuPDF extracts almost nothing even though the page appears
    full of text.

    Typical dense text page : ~0.004 chars/pt²
    Sparse / vector-art page: < 0.001 chars/pt²
    Default threshold        :  0.002 chars/pt²
    """
    chars = len(page.get_text().strip())
    area  = page.rect.width * page.rect.height
    return area > 0 and (chars / area) < threshold


def process_pdf(pdf_path: Path, reader=None) -> dict[str, Any]:
    doc = fitz.open(pdf_path)
    effective_reader = reader if (reader is not None and _is_image_only(doc)) else None
    pdf_stem = pdf_path.stem

    all_lines: list[TextLine]  = []
    all_images: list[ImageBlock] = []
    all_tables: list[TableBlock] = []
    all_toc_entries: list[dict[str, Any]] = []
    pages_meta = []
    last_section_title_guess = None

    pl_pdf = open_pdfplumber(str(pdf_path))
    try:
        for page_number in tqdm(range(len(doc)), desc=pdf_path.name[:35], unit="pg", leave=False):
            page = doc[page_number]

            # 1. Extract
            lines  = extract_text_lines(page, page_number)
            images = process_images(page, page_number, pdf_stem, reader=effective_reader)

            # Per-page OCR for sparse pages (vector-art / design-tool PDFs)
            if reader is not None and _is_sparse_page(page):
                ocr_line_items = ocr_page_as_lines(reader, page)
                if ocr_line_items:
                    for (txt, x0, y0, x1, y1, fs) in ocr_line_items:
                        alpha = [c for c in txt if c.isalpha()]
                        is_upper = bool(alpha) and (
                            sum(c.isupper() for c in alpha) / len(alpha) > 0.8
                        )
                        lines.append(TextLine(
                            text=txt, page=page_number,
                            bbox=[x0, y0, x1, y1],
                            x0=x0, y0=y0, x1=x1, y1=y1,
                            font_size_avg=fs,
                            is_bold=False,
                            is_upper=is_upper,
                            font_name="",
                        ))
                else:
                    ocr_text = ocr_page_as_image(reader, page)
                    if ocr_text:
                        images.append(ImageBlock(
                            image_id=f"{pdf_stem}_p{page_number + 1}_page_ocr",
                            page=page_number,
                            bbox=[0.0, 0.0, page.rect.width, page.rect.height],
                            width=int(page.rect.width),
                            height=int(page.rect.height),
                            relevant_for_ocr=True,
                            ocr_text=ocr_text,
                        ))

            # 2. Layout analysis (order matters)
            #    First pass of mark_table_like_lines shields detect_columns from
            #    table cell positions polluting the gap analysis.
            mark_table_like_lines(lines)
            detect_columns(lines, page.rect.width)
            mark_table_like_lines(lines)   # second pass: column-aware

            # 3. Header scoring (before table detection so header_level is populated)
            assign_header_levels(lines, table_count=0)

            # 4. Table detection
            page_tables = build_table_blocks(lines, page_number, last_section_title_guess, pl_pdf=pl_pdf)

            # 5. Re-score headers with actual table count for this page
            assign_header_levels(lines, table_count=len(page_tables))
            page_toc_entries = extract_toc_entries(lines)
            is_toc_page = bool(page_toc_entries)

            # 6. Update section title context for the next page's table detection
            page_headers = [l for l in lines if l.header_level == "section_header"]
            if page_headers:
                last_section_title_guess = page_headers[0].text

            all_lines.extend(lines)
            all_images.extend(images)
            all_tables.extend(page_tables)
            all_toc_entries.extend(page_toc_entries)

            pages_meta.append({
                "page": page_number + 1,
                "width": page.rect.width,
                "height": page.rect.height,
                "text_line_count": len(lines),
                "image_count": len(images),
                "table_count": len(page_tables),
                "is_toc_page": is_toc_page,
                "toc_entry_count": len(page_toc_entries),
                "header_candidates": [
                    {
                        "text": l.text,
                        "score": l.header_score,
                        "header_level": l.header_level,
                        "is_table_like": l.is_table_like,
                        "bbox": l.bbox,
                        "column_id": l.column_id,
                    }
                    for l in lines if l.header_level != "non_header"
                ],
            })
    finally:
        if pl_pdf is not None:
            pl_pdf.close()

    sections = segment_sections(all_lines, all_images, all_tables)

    serialized_sections = []
    for sec in sections:
        sec_dict = asdict(sec)
        sec_dict["text_plain"] = section_to_text_plain(sec_dict)
        serialized_sections.append(sec_dict)

    result = {
        "document": {
            "file_name":       pdf_path.name,
            "file_path":       str(pdf_path),
            "page_count":      len(doc),
            "title_metadata":  doc.metadata.get("title"),
            "author_metadata": doc.metadata.get("author"),
        },
        "pages":    pages_meta,
        "toc":      all_toc_entries,
        "tables":   [asdict(tb)  for tb  in all_tables],
        "images":   [asdict(img) for img in all_images],
        "sections": serialized_sections,
    }
    result["enrichment"] = build_enrichment(result)

    # Slim the saved JSON — fields consumed in-memory above are removed
    result.pop("toc", None)
    for sec in result["sections"]:
        sec.pop("text_items", None)
    for hint in result["enrichment"].get("field_hints", []):
        for _key in ("bbox", "hint_id", "evidence_scope"):
            hint.pop(_key, None)

    doc.close()
    return result


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    reader    = get_ocr_reader()
    pdf_files = sorted(INPUT_DIR.glob("*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in {INPUT_DIR.resolve()}")
        return

    for pdf_path in tqdm(pdf_files, desc="PDFs", unit="file"):
        print(f"Processing: {pdf_path.name}")
        result   = process_pdf(pdf_path, reader=reader)
        out_file = OUTPUT_DIR / f"{pdf_path.stem}.json"
        safe_json_dump(out_file, result)
        print(f"  -> {out_file}")

    run_triage(input_dir=OUTPUT_DIR)
    print("Done.")


if __name__ == "__main__":
    main()