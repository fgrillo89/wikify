# Marker vs Docling Parser Probe (Stage B1)

Date: 2026-05-04. Hardware: RTX 3070 Laptop, 8 GiB VRAM. Sample: 3
PDFs from `data/papers/ald_references/` (Chua 1971 -- formula-dense,
Strukov 2008 -- typical review, Jo 2010 -- short technical paper).
Each parser ran in its own Python process so VRAM was never shared
between them.

## Results

| | Marker | Docling default | Docling tuned (`page_batch_size=64`) |
|---|---|---|---|
| Chua 1971 (cold/dense) | 56,765 ms | 317,765 ms | 316,937 ms |
| Strukov 2008 (warm) | 6,500 ms | 14,515 ms | 14,827 ms |
| Jo 2010 (warm) | 5,468 ms | 8,530 ms | 8,108 ms |
| **median warm** | **6.0 s** | **11.5 s** | **11.5 s** |
| md regex equations median | 8 | 8 | 8 |
| structural FormulaItems median | n/a | 8 | 8 |

## What we learned

1. **Docling is ~2x slower than Marker on the warm path** (~11.5 s vs
   ~6.0 s per typical paper). Cold-start is much worse for Docling
   because of the 258 MB Granite-Docling model download on first run.
2. **Batch-size tuning is a no-op for our worker model.** Setting
   `page_batch_size`, `layout_batch_size`, `ocr_batch_size` from 4 to
   64 gave no measurable speedup. Each ingest worker processes one
   short doc at a time, so there are never enough pages in flight to
   fill a batch larger than 4. The Docling tuning guide's reported
   "up to 6x" speedup is for the `convert_all([many_docs])` shape,
   not the per-file shape we use.
3. **Equation quality is strictly better with Docling.** The
   markdown-regex count is misleading: Marker's 136 equations on
   Chua are dominated by inline `$x$` matches that aren't real
   formulas. Docling's structural `FormulaItem` extraction surfaces
   45 actual LaTeX equations on the same paper. On the less
   formula-dense Strukov / Jo papers the counts agree (8 / 2).
4. **Granite-Docling first-run cost is real.** Add it to onboarding
   docs so users don't think the parser is hung.

## Implication for default-parser flip

Conditional yes:

- **Speed cost is tolerable** (~2x slower means a 200-doc corpus
  takes ~38 min vs ~20 min on this hardware -- both already
  multi-minute jobs).
- **Equation quality unblocks Stage 5** (`equations.json` index
  repair): Docling gives us decoded LaTeX direct from FormulaItem,
  not regex-over-markdown.
- Corpus-quality wins from Stage A (clean section_path, no nano-
  chunks) compound with Docling's structural extraction in ingest.

But the sample size (3 PDFs) is too small to lock in the 2x ratio.
Recommend a larger probe before flipping:

- **Stage B1.5**: 20-doc probe, Docling-default vs Marker only
  (tuned proven no-op). Measure median + tail latency on more
  formula-heavy papers.
- **Stage B2**: if B1.5 confirms the ratio, flip the default in
  `parsers/registry.py` and remove (or rename) the
  `DOCLING_*_BATCH_SIZE` knobs to avoid implying they help.

## Cleanup landed alongside the probe

- Fixed `FormulaItem` import path
  (`docling_core.types.doc.document`, was wrong).
- Added `extract_formulas(doc) -> list[dict]` helper to pull
  structural formulas from a parsed `DoclingDocument`.
- Cleaned up stale `_docling_chunks` references in module + parse
  docstring (unified-chunker landed in Stage A).
- Removed dead `_hybrid_chunk()` (no longer called).
- Wired `page_batch_size` / `layout_batch_size` / `ocr_batch_size` /
  `table_batch_size` to env vars even though they don't help us
  today, so a future bulk-convert path can use them.
- `_apply_global_perf_settings(opts)` sets
  `settings.perf.page_batch_size` unconditionally so probes can
  toggle the value across runs.

## Files

- `scripts/probe_marker_vs_docling.py` -- the probe harness, one
  parser mode per process.
- `tasks/probe_marker.json`, `tasks/probe_docling_default.json`,
  `tasks/probe_docling_tuned.json` -- per-doc raw measurements.
