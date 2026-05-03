"""Before/after chunk comparison between two corpora.

Loads both SQLite stores plus the equations index from disk, prints a
table of the metrics from `tasks/chunk_hygiene_plan.md`'s baseline:
chunk size distribution, nano-chunk fraction, section_path noise,
single-`["body"]` docs, equations index size, boilerplate flag count.

Usage::

    uv run python scripts/compare_chunks.py \
        data/corpora/ald_all_marker \
        data/corpora/ald_marker_rechunked
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path


def stats_for(corpus_root: Path) -> dict:
    db = corpus_root / "wikify.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out: dict = {"root": str(corpus_root)}

    sizes = [r[0] for r in con.execute(
        "SELECT LENGTH(text) FROM chunks WHERE text IS NOT NULL"
    )]
    out["chunks"] = {
        "n": len(sizes),
        "median": int(statistics.median(sizes)) if sizes else 0,
        "mean": int(statistics.mean(sizes)) if sizes else 0,
        "p10": sorted(sizes)[len(sizes) // 10] if sizes else 0,
        "p90": sorted(sizes)[len(sizes) * 9 // 10] if sizes else 0,
        "max": max(sizes) if sizes else 0,
    }

    buckets = [100, 500, 1000, 2000, 4000, 8000, 16000]
    hist = {f"<{b}": 0 for b in buckets}
    hist[">=16000"] = 0
    for s in sizes:
        for b in buckets:
            if s < b:
                hist[f"<{b}"] += 1
                break
        else:
            hist[">=16000"] += 1
    out["size_hist"] = hist

    out["chunks_flagged_boilerplate"] = con.execute(
        "SELECT COUNT(*) FROM chunks WHERE is_boilerplate=1"
    ).fetchone()[0]

    section_type = list(con.execute(
        "SELECT section_type, COUNT(*) FROM chunks GROUP BY section_type"
    ))
    out["section_type"] = {r[0] or "(null)": r[1] for r in section_type}

    paths = list(con.execute("SELECT section_path_json FROM chunks"))

    def m(pred):
        return sum(1 for r in paths if pred(r[0] or ""))

    out["section_path_artifacts"] = {
        "html_sup_or_sub": m(
            lambda s: "<sup>" in s.lower() or "<sub>" in s.lower()
        ),
        "html_span_anchor": m(lambda s: "<span" in s.lower()),
        "page_number_header": m(
            lambda s: bool(re.search(r'"-\s*\*\*\d+\*\*"', s))
        ),
        "single_body_only": m(lambda s: s == '["body"]'),
        "articles_sidebar": m(
            lambda s: "articles you may" in s.lower()
        ),
        "concat_3plus": m(lambda s: (s.count(",") >= 2)),
    }

    one_section_docs = con.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT doc_id, COUNT(DISTINCT section_path_json) AS n "
        "  FROM chunks GROUP BY doc_id HAVING n=1"
        ")"
    ).fetchone()[0]
    out["docs_with_one_section_path"] = one_section_docs
    out["n_docs"] = con.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM chunks"
    ).fetchone()[0]
    out["docs_with_abstract_chunk"] = con.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM chunks WHERE section_type='abstract'"
    ).fetchone()[0]

    eq_path = corpus_root / "equations.json"
    total_eq = 0
    if eq_path.exists():
        try:
            data = json.loads(eq_path.read_text(encoding="utf-8"))
            recs = data.get("equations", []) if isinstance(data, dict) else data
            total_eq = len(recs)
        except json.JSONDecodeError:
            total_eq = -1

    n_eq_chunks = con.execute(
        "SELECT COUNT(*) FROM chunks "
        "WHERE equation_ids_json NOT IN ('', '[]') "
        "AND equation_ids_json IS NOT NULL"
    ).fetchone()[0]
    out["equations"] = {
        "json_records": total_eq,
        "chunks_with_equation_ids": n_eq_chunks,
    }

    con.close()
    return out


def render_diff(before: dict, after: dict) -> None:
    def fmt(b, a, fmt_str="{:>7}"):
        delta = a - b
        sign = "+" if delta > 0 else ""
        return (
            f"{fmt_str.format(b)} -> {fmt_str.format(a)}  "
            f"({sign}{fmt_str.format(delta).strip()})"
        )

    print(f"baseline: {before['root']}")
    print(f"after   : {after['root']}\n")

    print("=== chunks ===")
    for k in ("n", "median", "mean", "p10", "p90", "max"):
        print(f"  {k:8s}  {fmt(before['chunks'][k], after['chunks'][k])}")

    print("\n=== chunk size histogram ===")
    for k in before["size_hist"]:
        print(f"  {k:>10s}  {fmt(before['size_hist'][k], after['size_hist'][k])}")

    print("\n=== section_path artifacts ===")
    sp_b = before["section_path_artifacts"]
    sp_a = after["section_path_artifacts"]
    for k in sp_b:
        print(f"  {k:25s}  {fmt(sp_b[k], sp_a[k])}")

    print("\n=== docs ===")
    print(f"  n_docs                     {fmt(before['n_docs'], after['n_docs'])}")
    abs_b = before["docs_with_abstract_chunk"]
    abs_a = after["docs_with_abstract_chunk"]
    one_b = before["docs_with_one_section_path"]
    one_a = after["docs_with_one_section_path"]
    print(f"  with abstract chunk        {fmt(abs_b, abs_a)}")
    print(f"  with single ['body'] path  {fmt(one_b, one_a)}")

    print("\n=== boilerplate / equations ===")
    bp_b = before["chunks_flagged_boilerplate"]
    bp_a = after["chunks_flagged_boilerplate"]
    eq_b = before["equations"]
    eq_a = after["equations"]
    print(f"  is_boilerplate flagged     {fmt(bp_b, bp_a)}")
    print(f"  equations.json records     "
          f"{fmt(eq_b['json_records'], eq_a['json_records'])}")
    print(f"  chunks w/ equation_ids     "
          f"{fmt(eq_b['chunks_with_equation_ids'], eq_a['chunks_with_equation_ids'])}")

    print("\n=== section_type breakdown ===")
    types = sorted(set(before["section_type"]) | set(after["section_type"]))
    for t in types:
        b = before["section_type"].get(t, 0)
        a = after["section_type"].get(t, 0)
        print(f"  {t:18s}  {fmt(b, a)}")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    before = stats_for(Path(sys.argv[1]))
    after = stats_for(Path(sys.argv[2]))
    render_diff(before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
