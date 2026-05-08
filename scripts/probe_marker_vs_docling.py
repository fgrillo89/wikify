"""Speed + equation-quality probe: Marker vs Docling on a PDF sample.

Runs in *one* parser mode per invocation to keep VRAM clean -- loading
both Marker's surya models and Docling's Granite-Docling models in the
same Python process saturates an 8 GiB GPU and forces paging. Drive
each parser in a separate process and merge JSON outputs at the end.

Usage::

    # marker pass
    uv run python scripts/probe_marker_vs_docling.py \
        --source data/papers/ald_references --n 3 \
        --mode marker --out tasks/probe_marker.json

    # docling default pass (stage batch sizes=4)
    uv run python scripts/probe_marker_vs_docling.py \
        --source data/papers/ald_references --n 3 \
        --mode docling-default --out tasks/probe_docling_default.json

    # docling tuned pass (layout/OCR batch sizes=64)
    uv run python scripts/probe_marker_vs_docling.py \
        --source data/papers/ald_references --n 3 \
        --mode docling-tuned --out tasks/probe_docling_tuned.json

    # merge
    uv run python scripts/probe_marker_vs_docling.py --merge \
        tasks/probe_marker.json tasks/probe_docling_default.json \
        tasks/probe_docling_tuned.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _count_md_equations(md: str) -> int:
    """Count distinct LaTeX blocks present in markdown text."""
    display = re.findall(r"\$\$.+?\$\$", md, flags=re.DOTALL)
    inline = re.findall(r"(?<!\$)\$(?!\$)[^$\n]{2,}\$(?!\$)", md)
    return len(display) + len(inline)


def run_marker(pdf: Path) -> dict:
    """Marker parse. One subprocess load per probe invocation."""
    from wikify.ingest.parsers import marker_pdf

    t0 = time.monotonic()
    try:
        result = marker_pdf.parse(pdf, skip_metadata=True)
    except Exception as exc:
        return {"parser": "marker", "ok": False, "pdf": pdf.name,
                "error": f"{type(exc).__name__}: {exc}"}
    parse_ms = _ms(t0)
    md = result.markdown or ""
    return {
        "parser": "marker",
        "ok": True,
        "pdf": pdf.name,
        "parse_ms": parse_ms,
        "md_chars": len(md),
        "n_sections": len(result.sections),
        "n_images": len(result.raw_images),
        "n_md_equations": _count_md_equations(md),
        "first_md_equations": [
            m.group(0)[:120]
            for m in list(re.finditer(r"\$\$.+?\$\$", md, flags=re.DOTALL))[:3]
        ],
    }


def run_docling(pdf: Path, *, tuned: bool) -> dict:
    """Docling parse + structural formula extract in one pass."""
    if tuned:
        os.environ["DOCLING_LAYOUT_BATCH_SIZE"] = "64"
        os.environ["DOCLING_OCR_BATCH_SIZE"] = "64"
        os.environ["DOCLING_TABLE_BATCH_SIZE"] = "4"
    else:
        os.environ["DOCLING_LAYOUT_BATCH_SIZE"] = "4"
        os.environ["DOCLING_OCR_BATCH_SIZE"] = "4"
        os.environ["DOCLING_TABLE_BATCH_SIZE"] = "4"

    from wikify.ingest.parsers import docling as docling_mod

    docling_mod._patch_hf_symlinks()
    docling_mod._disable_torch_compile_on_windows()
    opts = docling_mod.DoclingOptions.from_env()
    converter = docling_mod._get_converter(opts)

    t0 = time.monotonic()
    try:
        conv_res = converter.convert(str(pdf.resolve()))
    except Exception as exc:
        return {"parser": f"docling_{'tuned' if tuned else 'default'}",
                "ok": False, "pdf": pdf.name,
                "error": f"{type(exc).__name__}: {exc}"}
    parse_ms = _ms(t0)
    doc = conv_res.document
    md = doc.export_to_markdown() or ""
    formulas = docling_mod.extract_formulas(doc)
    return {
        "parser": f"docling_{'tuned' if tuned else 'default'}",
        "ok": True,
        "pdf": pdf.name,
        "parse_ms": parse_ms,
        "md_chars": len(md),
        "n_md_equations": _count_md_equations(md),
        "n_structural_formulas": len(formulas),
        "first_structural_formulas": [
            f["latex"][:120] for f in formulas[:3]
        ],
    }


def run_pass(mode: str, pdfs: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for i, pdf in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf.name[:70]}", file=sys.stderr, flush=True)
        if mode == "marker":
            row = run_marker(pdf)
        elif mode == "docling-default":
            row = run_docling(pdf, tuned=False)
        elif mode == "docling-tuned":
            row = run_docling(pdf, tuned=True)
        else:
            print(f"unknown mode {mode!r}", file=sys.stderr)
            sys.exit(2)
        rows.append(row)
        if row.get("ok"):
            extras = []
            if "n_md_equations" in row:
                extras.append(f"md_eq={row['n_md_equations']}")
            if "n_structural_formulas" in row:
                extras.append(f"struct_eq={row['n_structural_formulas']}")
            print(
                f"  {row['parser']:18s} {row['parse_ms']:>6}ms "
                f"{' '.join(extras)}",
                file=sys.stderr, flush=True,
            )
        else:
            print(f"  ERROR: {row.get('error')}", file=sys.stderr, flush=True)
    return rows


def merge(paths: list[Path]) -> None:
    by_parser: dict[str, list[dict]] = {}
    for p in paths:
        rows = json.loads(p.read_text(encoding="utf-8"))
        for r in rows:
            if r.get("ok"):
                by_parser.setdefault(r["parser"], []).append(r)

    print(f"\n{'parser':22s}  {'n':>3s}  "
          f"{'med_ms':>8s}  {'mean_ms':>9s}  "
          f"{'med_md_eq':>10s}  {'med_struct':>11s}")
    for parser, rs in by_parser.items():
        med_ms = int(statistics.median(r["parse_ms"] for r in rs))
        mean_ms = int(statistics.mean(r["parse_ms"] for r in rs))
        med_md = int(statistics.median(r.get("n_md_equations", 0) for r in rs))
        struct_vals = [r["n_structural_formulas"]
                       for r in rs if "n_structural_formulas" in r]
        med_struct = int(statistics.median(struct_vals)) if struct_vals else "-"
        print(f"  {parser:20s} {len(rs):>3d}  "
              f"{med_ms:>8d}  {mean_ms:>9d}  "
              f"{med_md:>10d}  {med_struct!s:>11s}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--merge", nargs="+", type=Path,
                   help="JSON files from prior runs to summarise.")
    p.add_argument("--source", default="data/papers/ald_references")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--mode",
                   choices=["marker", "docling-default", "docling-tuned"])
    p.add_argument("--out")
    args = p.parse_args()

    if args.merge:
        merge(args.merge)
        return 0

    if not args.mode or not args.out:
        p.error("require --mode and --out (or pass --merge)")

    src = Path(args.source)
    pdfs = sorted(src.glob("*.pdf"))[: args.n]
    if not pdfs:
        print(f"no PDFs found under {src}", file=sys.stderr)
        return 2

    print(f"probe mode={args.mode}  n={len(pdfs)}", file=sys.stderr, flush=True)
    rows = run_pass(args.mode, pdfs)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
