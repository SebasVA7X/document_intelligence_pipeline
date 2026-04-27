"""
normalizer/assembler.py — Builds LLM prompts for the normalizer.

Two prompt types:
  1. Corpus-level (build_column_map_prompt): called once per corpus. The LLM
     receives all section titles with their frequencies and proposes canonical
     columns + a full mapping {title → column}. The result is saved to
     section_columns_map.json and reused on subsequent runs.

  2. Per-document (build_prompt): for sections whose normalized_title is not
     covered by the corpus map. The LLM receives the pending raw_titles and
     assigns them to the already-defined columns.
"""
from __future__ import annotations

import json
import re


# ─── Shared JSON parser ───────────────────────────────────────────────────────

def parse_llm_json(raw: str) -> dict:
    """Extract and parse a JSON object from LLM response text.

    Handles markdown code fences (```json ... ```) and falls back to
    searching for the first {...} block if direct parsing fails.
    Returns an empty dict on any parse failure.
    """
    if not raw:
        return {}
    clean = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        result = json.loads(clean)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]+\}", clean)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return {}


# ─── Corpus-level prompt ──────────────────────────────────────────────────────

_COLUMN_MAP_SYSTEM = """\
You are an expert in document structure analysis.

Given a list of section titles found across a corpus of documents, your task is to:
1. Propose a set of canonical semantic columns that represent the content categories of the corpus.
2. Assign each section title to its corresponding canonical column.

Return ONLY a valid JSON object with this exact structure:
{
  "columns": ["column1", "column2", ...],
  "title_map": {
    "exact section title": "canonical_column",
    ...
  }
}

RULES FOR COLUMNS:
- Propose BETWEEN 5 AND 11 content columns, plus "additional_content" as the last one.
  Total: MAXIMUM 12 columns. NEVER generate more than 12. If the corpus has many
  variants, group them — do not create one column per variant.
- Short names (1-3 words), lowercase, no special characters.
- The last column must always be "additional_content" (catch-all for anything
  that does not fit another category).
- Use generic, reusable names (e.g. "instructions", not "installation instructions").
- If the corpus contains introductory sections ("introduction", "product description",
  "about", etc.), include them in a column named "introduction" or "description".
  Do not send them to "additional_content".

RULES FOR THE MAPPING:
- Assign EACH title in the list to exactly one column. Do not omit any.
- Group semantically: "installation instructions", "how to use", and "cleaning
  instructions" should all map to "instructions" or "maintenance" based on content.
- Do not modify the original titles used as JSON keys.
- Do not include explanations or any text outside the JSON.\
"""


def build_column_map_prompt(
    sections: list[dict],
    total_docs: int,
) -> tuple[str, str]:
    """Build the corpus-level prompt for determining canonical columns.

    Args:
        sections:   List of sections from section_freq.json (with normalized_title,
                    docs_con_seccion, avg_chars). Structural sections excluded.
        total_docs: Total number of documents in the corpus.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    lines = [f"Corpus: {total_docs} documents\n"]
    lines.append("Section titles found (title — frequency — avg chars):")
    for s in sections:
        title = s["normalized_title"].strip()
        freq  = s["docs_con_seccion"]
        chars = int(s.get("avg_chars", 0))
        lines.append(f'- "{title}" — {freq} docs — {chars} chars')

    user = "\n".join(lines)
    return _COLUMN_MAP_SYSTEM, user


# ─── Per-document prompt ──────────────────────────────────────────────────────

_SECTION_SYSTEM_TEMPLATE = """\
You are a document section classifier.

Given a list of section titles, assign each one to the most appropriate standard
column from the list below. Return ONLY a valid JSON object where keys are the
original titles (exact, unmodified) and values are the assigned column name.

AVAILABLE COLUMNS:
{columns_block}

RULES:
- Assign each title to exactly one column.
- If a title partially fits multiple columns, choose the most specific one.
- Use the last column only when no other column adequately describes the title.
- Do not modify the original titles.
- Do not include explanations or any text outside the JSON.\
"""


def build_prompt(
    titles: list[str],
    archivo: str,
    columns: list[str],
) -> tuple[str, str]:
    """Build the per-document prompt for titles not covered by the corpus map.

    Args:
        titles:  List of raw_titles pending classification.
        archivo: Filename (context for the LLM).
        columns: Canonical columns defined in the corpus map.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    columns_block = "\n".join(f"- {col}" for col in columns)
    system = _SECTION_SYSTEM_TEMPLATE.format(columns_block=columns_block)

    if not titles:
        return system, ""

    titles_block = "\n".join(f'- "{t}"' for t in titles)
    user = f"Document: {archivo}\n\nTitles to classify:\n{titles_block}"
    return system, user
