# PDF Pipeline

A modular pipeline for extracting, analyzing, and normalizing content from PDF document corpora into structured Excel matrices. Designed for processing collections of technical documents (manuals, datasheets, instructions) across any industry or domain. 

**Best suited for product manuals, datasheets and technical instruction guides.**

## Overview

The pipeline runs in two independent stages:

```
pdfs/                         # Input: your PDF files
  │
  ▼  python main.py
  │
output_json/                  # One JSON per PDF (structured content)
triage_report.xlsx            # Quality report for human review
triage_control.json           # Routing decisions for the normalizer
section_freq.json             # Section frequency analysis across corpus
  │
  ▼  python normalize.py [--backend claude|ollama|none]
  │
analyzed_pdfs.xlsx            # Final normalized matrix (one row per document)
output_normalizado/           # Intermediate per-document JSON cache
section_columns_map.json      # LLM-generated canonical column map (reused on reruns)
```

**Stage 1** (`main.py`) extracts content from every PDF and immediately runs triage. No configuration needed beyond dropping files in `pdfs/`.

**Stage 2** (`normalize.py`) reads the triage output and collapses each document's sections into a tabular row. Can run with or without an LLM backend.

---

## Project Structure

```
pdf-pipeline/
│
├── config.py              # Global settings: paths, OCR flags, thresholds
├── main.py                # Stage 1 entry point: extract + triage
├── normalize.py           # Stage 2 entry point: normalize to Excel
├── requirements.txt
│
├── core/
│   ├── models.py          # Shared dataclasses: TextLine, ImageBlock, TableBlock, Section
│   └── utils.py           # Shared helpers: clean_text, bbox_union, safe_json_dump, etc.
│
├── extraction/
│   ├── text.py            # Text line extraction with font/bold/uppercase metadata
│   ├── images.py          # Image block detection and optional OCR cropping
│   ├── tables.py          # Table detection: pdfplumber primary + heuristic fallback
│   └── ocr.py             # EasyOCR integration for scanned and vector-art PDFs
│
├── layout/
│   ├── columns.py         # 1/2/3-column layout detection per page
│   └── headers.py         # Header scoring, TOC detection, section level assignment
│
├── pipeline/
│   ├── segmentation.py    # Section segmentation from classified lines
│   └── enrichment.py      # Field hint extraction (configurable target fields)
│
├── triage/
│   └── triage.py          # Quality scoring, document classification, routing
│
└── normalizer/
    ├── assembler.py        # LLM prompt construction (corpus-level + per-document)
    ├── client.py           # LLM adapter: Claude API | Ollama | none
    ├── config.py           # Normalizer-specific settings and product code patterns
    ├── exporter.py         # Excel workbook generation with typed sheets
    ├── extractor.py        # Product code and name extraction from filenames
    ├── mapper.py           # Section → column assignment with 4-level resolution
    └── router.py           # Full normalization orchestrator
```

---

## Quickstart

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **PyTorch / CUDA note:** `requirements.txt` pins CPU torch by default. For GPU support, install torch separately before the rest:
> ```bash
> # CUDA 12.x
> pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
> pip install -r requirements.txt
> ```

### 2. Add your PDFs

```bash
mkdir pdfs
cp /your/documents/*.pdf pdfs/ (Copy your pdfs to the directory)
```

### 3. Run Stage 1: Extract + Triage

```bash
python main.py
```

This processes every PDF in `pdfs/` and produces:

| Output | Description |
|---|---|
| `output_json/<name>.json` | Structured content per document |
| `triage_report.xlsx` | Quality report with color-coded scores |
| `triage_control.json` | Routing map consumed by Stage 2 |
| `section_freq.json` | Section title frequency across the corpus |

Review `triage_report.xlsx` before proceeding. Documents with quality score below 50 are automatically skipped in Stage 2. You can adjust this threshold — see [Triage](#triage).

### 4. Run Stage 2: Normalize

```bash
# No LLM — columns determined by frequency
python normalize.py

# With Claude API (recommended for best column grouping)
export ANTHROPIC_API_KEY=sk-ant-...
python normalize.py --backend claude

# With local Ollama
python normalize.py --backend ollama --model mistral

# Without API access — export prompt for manual use
python normalize.py --export-prompt
# Then paste column_map_prompt.txt into any LLM chat, save the JSON response
# as section_columns_map.json, and rerun without --export-prompt
```

Output: `analyzed_pdfs.xlsx` with one row per document, one column per detected section category, split across sheets by document type (Short / Medium / Long / Skipped).

**Note:** This pipeline is optimized for document corpora. Results improve significantly when processing multiple similar documents together, as the column map is built from cross-document section frequency analysis.

---


## Stage 1 Detail: Extraction Pipeline

Each PDF goes through the following steps per page:

```
PDF page
  │
  ├─ 1. Extract       text lines (font size, bold, uppercase, bbox)
  │                   image blocks (size filtering, optional OCR)
  │
  ├─ 2. Layout        mark_table_like_lines()  ← first pass (page-wide)
  │                   detect_columns()          ← 1/2/3-column layout
  │                   mark_table_like_lines()  ← second pass (column-aware)
  │
  ├─ 3. Header score  assign_header_levels()   ← section_header / subheader / non_header
  │
  ├─ 4. Tables        build_table_blocks()     ← pdfplumber first, heuristic fallback
  │
  └─ 5. Re-score      assign_header_levels()   ← corrects for high-table-density pages
       TOC detect     extract_toc_entries()
```

After all pages: `segment_sections()` assembles lines into named sections, then `build_enrichment()` extracts field hints.

The JSON written to `output_json/` has this top-level structure:

```json
{
  "document":   { "file_name", "page_count", "title_metadata", ... },
  "pages":      [ { "page", "header_candidates", "is_toc_page", ... } ],
  "tables":     [ { "table_id", "columns", "rows", "bbox", ... } ],
  "images":     [ { "image_id", "ocr_text", ... } ],
  "sections":   [ { "section_id", "raw_title", "normalized_title", "text_plain", ... } ],
  "enrichment": { "field_hints": [ { "target_field", "confidence", "source_text", ... } ] }
}
```

### OCR

OCR (EasyOCR) activates automatically under two conditions:

- **Image-only PDF:** total extractable characters across the document < `OCR_AUTO_THRESHOLD` (default: 100). Full-page OCR runs on every page.
- **Sparse page:** characters per pt² < `SPARSE_PAGE_DENSITY` (default: 0.002). Catches vector-art / design-tool exports (CorelDRAW, Illustrator) where text is rendered as bezier paths. Per-page OCR runs, returning individual lines with bounding boxes so header detection and segmentation work normally.

Set `OCR_ENABLED = False` in `config.py` to disable OCR entirely.

---

## Stage 2 Detail: Normalization Pipeline

```
triage_control.json + section_freq.json
  │
  ├─ Determine canonical columns
  │     LLM:       one corpus-level call → section_columns_map.json (cached)
  │     No LLM:    top sections by frequency above min_col_ratio threshold
  │
  ├─ For each document:
  │     Resolve section → column (4-level priority):
  │       1. Corpus map (section_columns_map.json)
  │       2. Direct name match
  │       3. First-word match (avoids redundant LLM calls for variants)
  │       4. Per-document LLM call (only for uncovered titles)
  │     Concatenate section text per column → row
  │     Cache to output_normalizado/<name>.json
  │
  └─ Export analyzed_pdfs.xlsx
       Sheets: Short | Medium | Long | Skipped
```

### Column map caching

`section_columns_map.json` is generated once and reused on every subsequent run. To regenerate it (e.g. after adding new documents):

```bash
python normalize.py --backend claude --force
```

---

## Triage

`triage_report.xlsx` has two sheets:

- **Triage** — one row per document with quality score, detected problems, and top sections.
- **Section Frequency** — how often each normalized section title appears across the corpus, color-coded by prevalence.

Quality score starts at 100 and applies penalties for detected issues:

| Problem | Penalty | Meaning |
|---|---|---|
| `sin_texto` | −50 | No extractable text (image-only, OCR failed) |
| `mayormente_imagenes` | −30 | Mostly images, very few text lines |
| `demasiadas_secciones` | −12 | Too many headers for page count |
| `normalizacion_pobre` | −12 | >60% of sections mapped to fallback column |
| `muchas_sin_titulo` | −10 | >30% of sections have no detected title |
| `tablas_sospechosas` | −10 | Tables with >6 columns or >120-char cells |
| `tablas_con_footer` | −10 | Table cells look like page numbers / codes |
| `headers_lista` | −8 | Section headers look like list items |
| `headers_modelo` | −8 | Section headers look like product codes |
| `headers_largos` | −6 | Multiple headers with >12 words |
| `secciones_demasiado_cortas` | −6 | Average section < 150 chars |
| `secciones_demasiado_largas` | −6 | Few sections, very large average size |
| `muchos_subheaders` | −5 | Subheader count disproportionate to sections |
| `multilingue` | 0 | Multi-language document (no penalty, special routing) |

Documents below the quality threshold are routed to `accion: omitir` in `triage_control.json`. Multi-language documents are routed to `accion: procesar_español` (extract Spanish sections only). You can edit the control JSON manually before running Stage 2 to override any routing decision.

Change the quality threshold:

```bash
# From main.py (called automatically after extraction):
# edit run_triage(min_score=50) in main.py

# From triage directly:
python -m triage.triage --min-score 70
```

---

## Configuration

All global settings are in `config.py`:

```python
# Directories
INPUT_DIR  = Path("pdfs")
OUTPUT_DIR = Path("output_json")

# OCR
OCR_ENABLED          = True     # set False to skip OCR entirely
OCR_AUTO_THRESHOLD   = 100      # chars below which a PDF is treated as image-only
SPARSE_PAGE_DENSITY  = 0.002    # chars/pt² below which a page gets per-page OCR
OCR_LANGS            = ["es", "en"]  # EasyOCR language list

# Table detection
TABLE_MIN_LINES      = 4        # minimum rows to constitute a table
TABLE_Y_GROUP_TOL    = 10       # Y-tolerance for row clustering (pts)

# Header scoring
SECTION_HEADER_THRESHOLD = 6
SUBHEADER_THRESHOLD      = 5
```

For normalizer-specific settings (LLM backend, product code patterns), see `normalizer/config.py`.

### Adapting enrichment to your domain

`pipeline/enrichment.py` extracts structured field hints from document content. The target fields and their lookup terms are defined near the top of the file and should be adapted to your use case:

```python
TARGET_FIELDS = [
    "product_name",
    "product_code",
    "description",
    # ... add your fields
]

FIELD_ALIASES = {
    "product_name": ["product name", "commercial name", "item", ...],
    # ... add lookup terms per field
}
```

---

## Requirements

- Python 3.10+
- PyMuPDF, pdfplumber, EasyOCR, openpyxl, Pillow, torch
- Optional: `anthropic` (Claude API backend) or a running Ollama instance

See `requirements.txt` for pinned versions.

---

## License

MIT
