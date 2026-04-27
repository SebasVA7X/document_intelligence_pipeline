"""normalize.py — Stage 2 entry point: normalize extracted JSONs to Excel.

Usage:
    python normalize.py
    python normalize.py --backend claude
    python normalize.py --backend ollama --model mistral
    python normalize.py --force
    python normalize.py --export-prompt
    python normalize.py --control triage_control.json --output catalog.xlsx

Files generated alongside triage_control.json:
    section_columns_map.json  — canonical columns + title→column map (LLM, one call)
    output_normalizado/       — per-document intermediate JSON cache
    analyzed_pdfs.xlsx        — final matrix (sheets: Short / Medium / Long / Skipped)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from normalizer import run_normalizer
from normalizer.router import export_column_map_prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize extractor JSONs to an Excel matrix.")
    parser.add_argument("--control",       default="triage_control.json",  help="Triage control JSON path")
    parser.add_argument("--out-dir",       default="output_normalizado",    help="Folder for intermediate JSON cache")
    parser.add_argument("--output",        default="analyzed_pdfs.xlsx",    help="Output Excel path")
    parser.add_argument("--backend",       default="none",                  choices=["claude", "ollama", "none"],
                        help="LLM backend (default: none)")
    parser.add_argument("--model",         default=None,                    help="Override LLM model name")
    parser.add_argument("--force",         action="store_true",             help="Regenerate column map and all cached JSONs")
    parser.add_argument("--min-col-ratio", default=0.15, type=float,        help="Min document fraction for a column (no-LLM fallback, default: 0.15)")
    parser.add_argument("--export-prompt", action="store_true",             help="Export corpus-level prompt to column_map_prompt.txt for manual LLM use")
    args = parser.parse_args()

    if args.export_prompt:
        export_column_map_prompt(control_path=Path(args.control))
        return

    run_normalizer(
        control_path=Path(args.control),
        output_dir=Path(args.out_dir),
        excel_path=Path(args.output),
        backend=args.backend,
        model=args.model,
        force=args.force,
        min_col_ratio=args.min_col_ratio,
    )


if __name__ == "__main__":
    main()
