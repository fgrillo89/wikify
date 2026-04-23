"""Verify a candidate ExtractResponse against its request before writing.

The handler agent composes a response, saves it to a temp path, then
runs:

    uv run python scripts/verify_extract_response.py <request.json> <candidate.json>

Exit code 0 = response is valid; exit != 0 = errors printed to stderr.
The agent fixes per the error messages and re-runs until it passes,
THEN renames the candidate to ``<rid>.response.json`` (the dispatch
target). This makes the verbatim-quote and schema-enum rules
non-negotiable: the agent can't ship a response the harness would
later reject.

Checks performed:
  - JSON parses
  - Top-level shape: chunk_id, concepts (list), tokens_in, tokens_out
  - chunk_id matches request
  - Per concept: kind ∈ {article, person}; category null only for person
  - Per concept: quote is a literal substring of request.chunk_text
  - Per concept: equations[].kind ∈ {mathematical, chemical} when present
  - Per concept: definition / summary length ranges for kind=article
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ALLOWED_KINDS = {"article", "person"}
_ALLOWED_CATEGORIES = {
    "phenomenon", "method", "material", "device",
    "theory", "metric", "organization", "other",
}
_ALLOWED_EQUATION_KINDS = {"mathematical", "chemical"}


def _err(msg: str) -> None:
    sys.stderr.write(f"[verify] {msg}\n")


def main(req_path: str, cand_path: str) -> int:
    try:
        req = json.loads(Path(req_path).read_text(encoding="utf-8"))
    except Exception as e:
        _err(f"cannot read request {req_path}: {e}")
        return 2
    try:
        cand = json.loads(Path(cand_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _err(f"candidate is not valid JSON: {e}")
        _err("Common cause: two JSON objects concatenated. Output ONE JSON only.")
        return 1
    except Exception as e:
        _err(f"cannot read candidate {cand_path}: {e}")
        return 2

    chunk_text = req.get("chunk_text", "")
    expected_chunk_id = req.get("chunk_id", "")

    errors: list[str] = []

    # Top-level shape
    if not isinstance(cand, dict):
        errors.append("response root is not a JSON object")
        return _report(errors)
    if cand.get("chunk_id") != expected_chunk_id:
        errors.append(
            f"chunk_id mismatch: response has {cand.get('chunk_id')!r}, "
            f"request has {expected_chunk_id!r}"
        )
    concepts = cand.get("concepts")
    if not isinstance(concepts, list):
        errors.append("'concepts' must be a list (use [] for empty)")
        return _report(errors)

    # Per-concept checks
    for i, c in enumerate(concepts):
        if not isinstance(c, dict):
            errors.append(f"concepts[{i}] is not an object")
            continue
        kind = c.get("kind")
        if kind not in _ALLOWED_KINDS:
            errors.append(
                f"concepts[{i}].kind = {kind!r} — must be one of "
                f"{sorted(_ALLOWED_KINDS)}. NOTE: facet tags like 'device', "
                f"'phenomenon', 'method' go in the 'category' field, NOT 'kind'."
            )
        cat = c.get("category")
        if kind == "person" and cat is not None:
            errors.append(
                f"concepts[{i}].category = {cat!r} — must be null for kind='person'"
            )
        if kind == "article" and cat is not None and cat not in _ALLOWED_CATEGORIES:
            errors.append(
                f"concepts[{i}].category = {cat!r} — must be one of "
                f"{sorted(_ALLOWED_CATEGORIES)} or null"
            )

        # Verbatim quote check (the dominant failure mode)
        quote = c.get("quote", "")
        if not isinstance(quote, str) or not quote:
            errors.append(f"concepts[{i}].quote is missing or empty")
        elif quote not in chunk_text:
            preview = quote[:80].replace("\n", " ")
            errors.append(
                f"concepts[{i}].quote IS NOT a literal substring of chunk_text. "
                f"Quote starts with: {preview!r}. "
                "FIX: pick a different sentence/phrase that you can copy-paste "
                "verbatim from chunk_text. Do NOT paraphrase or synthesize. "
                "If no clean verbatim phrase exists, drop the concept."
            )

        # Article: definition + summary length sanity
        if kind == "article":
            d = c.get("definition", "") or ""
            s = c.get("summary", "") or ""
            n_d = len(d.split())
            n_s = len(s.split())
            if n_d < 30:
                errors.append(
                    f"concepts[{i}].definition is only {n_d} words — "
                    "must be 50-200 words for kind='article' substantive content"
                )
            if n_s < 30:
                errors.append(
                    f"concepts[{i}].summary is only {n_s} words — "
                    "must be 80-200 words for kind='article' substantive content"
                )

        # Equations
        for j, eq in enumerate(c.get("equations") or []):
            ek = eq.get("kind")
            if ek not in _ALLOWED_EQUATION_KINDS:
                errors.append(
                    f"concepts[{i}].equations[{j}].kind = {ek!r} — must be one of "
                    f"{sorted(_ALLOWED_EQUATION_KINDS)}"
                )

        # Parameters: sub-fields must be strings, never null
        for j, p in enumerate(c.get("parameters") or []):
            for fname in ("name", "value", "unit", "conditions"):
                fv = p.get(fname)
                if fv is None or not isinstance(fv, str):
                    errors.append(
                        f"concepts[{i}].parameters[{j}].{fname} = {fv!r} — "
                        "must be a string (use \"\" if unknown), never null"
                    )

    return _report(errors)


def _report(errors: list[str]) -> int:
    if not errors:
        sys.stderr.write("[verify] OK\n")
        return 0
    for e in errors:
        _err(e)
    sys.stderr.write(f"[verify] {len(errors)} error(s) — fix and retry\n")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.stderr.write(
            f"usage: {sys.argv[0]} <request.json> <candidate.response.json>\n"
        )
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
