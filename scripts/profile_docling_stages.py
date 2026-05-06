"""Per-stage Docling profile: where does the parser actually spend time?

Sets ``settings.debug.profile_pipeline_timings = True`` on a clean
DocumentConverter and runs three representative PDFs back-to-back so
the formula-dense outlier is pulled apart from the median paper.
Stage breakdowns land in ``result.timings`` (one entry per stage:
LayoutAnalysis, TableStructure, FormulaEnrichment, ...).

Usage::

    uv run python scripts/profile_docling_stages.py \
        data/papers/ald_references/[1971\\ Chua]*.pdf \
        data/papers/ald_references/[2008\\ Strukov]*.pdf \
        data/papers/ald_references/[2010\\ Jo]*.pdf

Prints a per-doc table plus an aggregate. Failure to enable the
profiling flag (older docling versions) is reported but does not
abort the run -- you'll still get total wall-clock per doc.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _enable_profiling() -> bool:
    try:
        from docling.datamodel.settings import settings
        settings.debug.profile_pipeline_timings = True
        return True
    except Exception as exc:
        print(f"[profile] could not enable profile flag: {exc}",
              file=sys.stderr, flush=True)
        return False


def _build_converter():
    """Default Docling converter using our DoclingOptions defaults."""
    from wikify.ingest.parsers import docling as docling_mod

    docling_mod._patch_hf_symlinks()
    docling_mod._disable_torch_compile_on_windows()
    opts = docling_mod.DoclingOptions.from_env()
    docling_mod._CACHED_CONVERTER = None
    docling_mod._CACHED_OPTS_KEY = None
    return docling_mod._get_converter(opts)


def _stage_timings(result) -> dict[str, float]:
    """Best-effort extraction of stage->seconds from a Docling result.

    Different docling versions surface timings on different attributes;
    fall back to ``{}`` if none are present.
    """
    out: dict[str, float] = {}
    timings = getattr(result, "timings", None)
    if timings:
        try:
            # Pydantic-style: timings is a dict[str, ProfilingItem]
            for stage, item in timings.items():
                # Each item carries times[] and count
                times = getattr(item, "times", None)
                if times:
                    out[str(stage)] = float(sum(times))
                else:
                    elapsed = getattr(item, "elapsed", None)
                    if elapsed is not None:
                        out[str(stage)] = float(elapsed)
        except (AttributeError, TypeError):
            pass
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pdfs", nargs="+", type=Path)
    args = p.parse_args()

    enabled = _enable_profiling()
    print(f"profile flag enabled: {enabled}\n", file=sys.stderr, flush=True)

    converter = _build_converter()

    rows: list[dict] = []
    for pdf in args.pdfs:
        if not pdf.is_file():
            print(f"[skip] not a file: {pdf}", file=sys.stderr, flush=True)
            continue
        print(f"=== {pdf.name[:75]}", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        try:
            result = converter.convert(str(pdf.resolve()))
        except Exception as exc:
            print(f"  FAIL: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            continue
        wall_s = time.monotonic() - t0
        stages = _stage_timings(result)
        n_pages = len(getattr(result.document, "pages", {}) or {})
        print(f"  wall={wall_s:.1f}s  pages={n_pages}",
              file=sys.stderr, flush=True)
        if stages:
            for stage, secs in sorted(stages.items(), key=lambda kv: -kv[1]):
                pct = 100 * secs / wall_s if wall_s > 0 else 0
                print(f"    {stage:30s} {secs:>6.1f}s  ({pct:>4.1f}%)",
                      file=sys.stderr, flush=True)
        else:
            print("    (no per-stage timings on this docling version)",
                  file=sys.stderr, flush=True)
        rows.append({"pdf": pdf.name, "wall_s": wall_s,
                     "n_pages": n_pages, "stages": stages})

    # Aggregate
    if not rows:
        return 1
    print("\n## aggregate", file=sys.stderr, flush=True)
    print(f"{'pdf':50s}  {'wall_s':>8s}  {'pages':>6s}  per_page",
          file=sys.stderr, flush=True)
    for r in rows:
        per_page = r["wall_s"] / r["n_pages"] if r["n_pages"] else 0
        print(f"{r['pdf'][:48]:50s}  {r['wall_s']:>8.1f}  "
              f"{r['n_pages']:>6d}  {per_page:>5.2f}s",
              file=sys.stderr, flush=True)
    # Stage rollup across all docs
    all_stages: dict[str, float] = {}
    for r in rows:
        for s, t in r["stages"].items():
            all_stages[s] = all_stages.get(s, 0.0) + t
    if all_stages:
        total = sum(all_stages.values())
        print("\nstage rollup (sum across docs):", file=sys.stderr, flush=True)
        for s, t in sorted(all_stages.items(), key=lambda kv: -kv[1]):
            print(f"  {s:30s} {t:>7.1f}s  ({100*t/max(total,0.001):>4.1f}%)",
                  file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
