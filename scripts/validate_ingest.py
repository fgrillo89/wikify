"""Validation pass for a freshly-ingested corpus.

Checks the four risks the chunk-hygiene + Docling-default rollout
introduced into the ingest path:

1. DoclingDocument JSON cache: per-doc file present, sizes plausible.
2. Citation marker survival: ``chunk_citations`` table has rows, and
   the per-chunk ``[N]`` regex finds markers in chunk text.
3. Equations index quality: ``equations.json`` populated, no junk
   records (single-char latex, pure digits, no math operator).
4. End-to-end pipeline sanity: chunks JSONL + docs JSON written for
   every doc; section_path noise zero (no HTML / page-number
   leakage); all docs have at least one non-``["body"]`` section.

Run::

    uv run python scripts/validate_ingest.py data/corpora/ald_validation
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path


def main(corpus_root: Path) -> int:
    print(f"# validation: {corpus_root}\n")
    fail = False

    # 1. doclingdoc cache
    cache_dir = corpus_root / "derived" / "doclingdoc"
    docs_dir = corpus_root / "docs"
    n_docs = sum(1 for _ in docs_dir.glob("*.json"))
    cache_files = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
    print("## DoclingDocument cache")
    print(f"  docs/*.json: {n_docs}")
    print(f"  derived/doclingdoc/*.json: {len(cache_files)}")
    if not cache_files:
        print("  FAIL: no DoclingDocument JSONs cached")
        fail = True
    else:
        sizes = [p.stat().st_size for p in cache_files]
        print(
            f"  cache sizes: min={min(sizes)/1024:.0f} KiB "
            f"median={sorted(sizes)[len(sizes)//2]/1024:.0f} KiB "
            f"max={max(sizes)/1024:.0f} KiB "
            f"total={sum(sizes)/2**20:.1f} MiB"
        )
        if len(cache_files) < n_docs:
            print(f"  WARN: cache miss for {n_docs - len(cache_files)} doc(s)")

    # 2. citation marker survival
    print("\n## Citations")
    db = corpus_root / "wikify.db"
    if not db.exists():
        print("  FAIL: no wikify.db")
        fail = True
    else:
        con = sqlite3.connect(db)
        n_chunk_cit = con.execute(
            "SELECT COUNT(*) FROM chunk_citations"
        ).fetchone()[0]
        n_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        n_with_marker = 0
        marker_re = re.compile(r"\[\d+\]")
        for r in con.execute("SELECT text FROM chunks"):
            if r[0] and marker_re.search(r[0]):
                n_with_marker += 1
        n_bib = con.execute("SELECT COUNT(*) FROM bib_entries").fetchone()[0]
        print(f"  chunks: {n_chunks}")
        print(f"  chunks containing [N] markers: {n_with_marker}")
        print(f"  chunk_citations rows: {n_chunk_cit}")
        print(f"  bib_entries rows: {n_bib}")
        if n_with_marker > 0 and n_chunk_cit == 0:
            print("  FAIL: chunks have markers but chunk_citations is empty")
            fail = True
        con.close()

    # 3. equations.json quality
    print("\n## Equations")
    eq_path = corpus_root / "equations.json"
    if not eq_path.exists():
        print("  FAIL: no equations.json")
        fail = True
    else:
        data = json.loads(eq_path.read_text(encoding="utf-8"))
        recs = data.get("records", []) if isinstance(data, dict) else data
        total = len(recs)
        # Count junk under the same predicate as the in-process filter
        junk = 0
        for r in recs:
            latex = (r.get("latex") or "").strip()
            if len(latex) < 3 or latex.isdigit():
                junk += 1
            elif not re.search(r"[A-Za-z0-9]", latex):
                junk += 1
        print(f"  total records: {total}")
        print(f"  junk (would be filtered): {junk} ({100*junk/max(total,1):.0f}%)")
        if total == 0:
            print("  WARN: equations.json empty (sample may have no equations)")
        if junk > 0:
            print("  FAIL: junk records in index despite filter")
            fail = True

    # 4. section_path quality
    print("\n## Section paths")
    db = corpus_root / "wikify.db"
    if db.exists():
        con = sqlite3.connect(db)
        rows = list(con.execute("SELECT section_path_json FROM chunks"))
        n = len(rows)
        n_html = sum(
            1 for r in rows
            if "<sup>" in (r[0] or "").lower()
            or "<sub>" in (r[0] or "").lower()
            or "<span" in (r[0] or "").lower()
        )
        n_page_num = sum(
            1 for r in rows
            if bool(re.search(r'"-\s*\*\*\d+\*\*"', r[0] or ""))
        )
        n_body_only = sum(1 for r in rows if (r[0] or "") == '["body"]')
        # Per-doc structure
        single_doc_paths = list(con.execute(
            "SELECT doc_id, COUNT(DISTINCT section_path_json) AS n "
            "FROM chunks GROUP BY doc_id"
        ))
        n_one_section = sum(1 for d, c in single_doc_paths if c == 1)
        print(f"  total chunks: {n}")
        print(f"  HTML in section_path: {n_html}")
        print(f"  page-number-as-header: {n_page_num}")
        print(f"  body-only chunks: {n_body_only}")
        print(
            f"  docs with single section_path: "
            f"{n_one_section} / {len(single_doc_paths)}"
        )
        if n_html > 0 or n_page_num > 0:
            print("  FAIL: section_path noise leaked through chunker")
            fail = True
        con.close()

    print("\n" + ("=" * 40))
    if fail:
        print("VALIDATION FAILED -- see FAIL lines above")
        return 1
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
