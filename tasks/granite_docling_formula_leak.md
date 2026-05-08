# Granite-Docling VLM formula leak + autoregressive repetition

## Problem statement

The Docling default parser produces markdown where some equations
contain raw Granite-Docling VLM output structure that should have
been consumed by the upstream parser. Two distinct symptoms appear,
sometimes together, in ingested corpora:

1. **Wrapper leak.** Open and close `<formula>...</formula>` tags
   leak into the rendered markdown along with `<loc_NNN>` bbox tokens
   from Granite-Docling's vocabulary. Concrete sample from
   `build/ald_docling_2026_05_06/markdown/` (2026-05-07 ingest):

   ```
   <formula><loc_247><loc_0><loc_500><loc_499>( 3 ) \quad \text{for} ...
   ```

   ```
   $$\text{curl} \, H_{1} = J_{1} + \frac{\partial D_{0}}{\partial \tau}.
   \quad (2[6]) \quad \text{nam}</formula>$$
   ```

2. **Autoregressive repetition loops.** Inside leaked formula blocks,
   the LaTeX content is sometimes a degenerate repetition like
   `\text{not} \, s \, \text{not} \, s \, \text{not} \, s ...`
   continuing for hundreds of tokens. This is the VLM failing to
   predict EOS and looping on a sub-sequence until it hits its
   max-token budget.

The symptoms are systematic across the 207-paper corpus, not a single
bad PDF. They appear in roughly 5-15% of equation blocks based on
manual sampling.

## Why this matters

* **Equation index quality.** The corpus's `equations` table feeds
  semantic search and the writing pipeline's grounded-citation logic.
  Repetitive runaway equations contaminate retrieval — a query for
  "Maxwell equation" can match a 500-token loop of `\text{not} \, s`
  and outrank a clean equation.
* **Chunk pollution.** Chunks containing leaked formulas carry both
  garbage tokens (lower vector quality) and unbalanced HTML
  (`</formula>` without an opener). The unbalanced HTML can confuse
  any downstream renderer that treats markdown as the source of
  truth.
* **Wall-clock cost.** Repetition loops are the dominant cost on
  formula-heavy review papers: the heartbeat showed 720 s (Song) and
  1380 s (Zhu) on single papers in the 2026-05-07 run. Granite-Docling
  is autoregressive, so every extra repeated token is an extra forward
  pass through a 258 M-parameter VLM. A single paper with one runaway
  formula can dominate total ingest wall-clock for a 200-paper run.

## What I have NOT done

I am intentionally NOT stripping the leaked tags in `_light_clean`.
A regex strip would silently hide the symptom in the rendered output
while leaving:

* the broken LaTeX still in chunks and embeddings,
* the wall-clock cost still paid during the autoregressive loop,
* future regressions invisible because the sentinel tags are gone.

The visible HTML in the markdown is the ONLY surface signal that
something is wrong with this paper's formula extraction. Removing it
trades one bug for two.

## Investigation plan

In rough order of cost:

### 1. Confirm the leak path in Docling's source

Trace the data flow from Granite-Docling's raw decode output to the
markdown export:

* `granite_docling` formula model emits sequences shaped like
  `<formula><loc_a><loc_b><loc_c><loc_d>LATEX</formula>`. Find the
  Docling component that consumes those tags and extracts LATEX into
  the `FormulaItem.text` field.
* Find `DoclingDocument.export_to_markdown()`'s formula path. Does it
  read `FormulaItem.text` (clean) or some raw token stream (dirty)?
* Identify the failure mode: is it (a) the consumer mis-parsing the
  raw stream when LaTeX contains repetition, or (b) the markdown
  exporter falling back to raw tokens when `FormulaItem.text` is
  empty?

Concrete starting points: `docling/datamodel/document.py` for
`FormulaItem`, `docling_core/types/doc/document.py` for the markdown
exporter. Repo: github.com/DS4SD/docling.

Output of step 1: a one-paragraph note in this file naming the exact
function that emits the unparsed wrapper.

### 2. Quantify the repetition rate

Build a small audit script (call it `scripts/audit_formula_leak.py`)
that walks a corpus's markdown sidecars and counts:

* number of files with at least one leaked `<formula>` or `<loc_`
  token,
* total leaked-tag count,
* longest repetition run inside a leaked block (use a sliding-window
  shingle-repetition detector — e.g., split on whitespace, find the
  largest k such that the same k-gram repeats > 10 times).

Run against `build/ald_docling_2026_05_06/`. Output: counts per paper,
top 10 worst offenders. This tells us whether the problem affects 5%
or 50% of papers and how much wall-clock cost is going into runaway
decodes.

### 3. Reproduce on a minimal sample

Pick the worst offender from step 2. Re-parse it standalone with
verbose logging on Granite-Docling's decode. Capture:

* the raw token stream the VLM emits,
* the `FormulaItem.text` values produced,
* the exported markdown.

Determines whether the bug is in the VLM (degenerate decode) or
the parser (clean decode but bad post-processing).

## Structural resolution plan

Pick whichever applies after step 3 confirms the failure mode.

### Option A — VLM decode hardening (most likely needed)

If repetition is in the raw VLM output, fix at the inference layer.
Granite-Docling exposes `max_new_tokens` and `repetition_penalty`
through `CodeFormulaVlmOptions` / its underlying
`HuggingFaceTransformerOptions`. Concrete config to try:

```python
CodeFormulaVlmOptions.from_preset("granite_docling")
  .with_overrides(
      max_new_tokens=256,        # current default is 1024+
      repetition_penalty=1.15,   # discourage k-gram loops
      no_repeat_ngram_size=8,    # hard ban 8-gram repeats
      do_sample=False,           # keep greedy/deterministic
  )
```

Wire through `DOCLING_FORMULA_*` env vars so we can iterate without
code changes. Add a probe script that emits per-formula token counts
+ wall-clock so we can see the cap working.

Risk: if the formula genuinely needs > 256 tokens of LaTeX, the cap
truncates real content. Mitigation: log every formula whose decode
hits the cap; spot-check those for false positives.

### Option B — Parser-side recovery

If the VLM output is clean but the parser leaks tags, fix Docling
upstream (or vendor a small post-processing step in our parser
module). This would live next to `_doc_walk` in
`src/wikify/ingest/parsers/docling.py`:

1. After `doc.export_to_markdown()`, walk every `FormulaItem` again.
2. For each, search the markdown for the matching `<formula>...
   </formula>` block.
3. Replace with the clean LaTeX from `FormulaItem.text`, wrapped in
   `$$...$$`.

The block-replacement approach is preferable to a regex strip
because:

* It uses the structural (clean) LaTeX, not the leaked one.
* It surfaces a clear assertion failure if `FormulaItem.text` is
  also corrupted (so we don't quietly degrade).
* It's idempotent — running on already-clean markdown is a no-op.

### Option C — Detection + invalidation

If neither the VLM nor the parser can be made reliable enough,
detect contaminated formula blocks at ingest time and invalidate
them. Concretely:

1. Heuristic: a formula block whose LaTeX contains a 3-gram repeated
   > 10 times is degenerate.
2. Replace contaminated blocks with `$$\text{[formula extraction
   failed]}$$` AND raise a warning to `failed_files.log` (per-doc,
   per-formula count).
3. Keep the structural `_docling_formulas` records ONLY for
   non-contaminated formulas; chunks and embeddings use the clean
   subset.

This is the "gracefully fail" path. Better than silent strip but
worse than fixing the root cause.

### Recommendation

Do step 1 + 2 first to scope the problem. If the worst offenders
have repetition rate > 30%, jump to Option A. If repetition is rare
(< 5%) but parser tag leaks are common, do Option B. Option C is the
fallback if A and B both fail.

## Owner / status

* Owner: unassigned.
* Status: investigation pending. Step 1 should take ~2 hours of
  reading Docling source. Step 2 is a one-evening script.
* Blocking: equation-index quality is degraded for the 2026-05-07
  ingest. The current corpus is usable for prose retrieval but
  equations should be treated as untrusted until this is resolved.

## Related

* `tasks/parser_probe.md` — Stage B parser-comparison work that
  selected Granite-Docling.
* `src/wikify/ingest/parsers/docling.py::_make_standard_options` —
  where `code_formula_options` is configured.
* `src/wikify/ingest/equations.py` — downstream consumer of
  `_docling_formulas` records.
