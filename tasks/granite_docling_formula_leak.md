# Granite-Docling Formula Leak And Autoregressive Repetition

## Problem Statement

The Docling default parser produces markdown where some equations
contain raw Granite-Docling VLM output structure that should have been
consumed by the upstream parser. Two distinct symptoms appear,
sometimes together, in ingested corpora:

1. **Wrapper leak.** Open and close `<formula>...</formula>` tags leak
   into rendered markdown along with `<loc_NNN>` bbox tokens from
   Granite-Docling's vocabulary. Concrete sample from
   `build/ald_docling_2026_05_06/markdown/` (2026-05-07 ingest):

   ```text
   <formula><loc_247><loc_0><loc_500><loc_499>( 3 ) \quad \text{for} ...
   ```

   ```text
   $$\text{curl} \, H_{1} = J_{1} + \frac{\partial D_{0}}{\partial \tau}.
   \quad (2[6]) \quad \text{nam}</formula>$$
   ```

2. **Autoregressive repetition loops.** Inside leaked formula blocks,
   the LaTeX content is sometimes a degenerate repetition like
   `\text{not} \, s \, \text{not} \, s \, \text{not} \, s ...`
   continuing for hundreds of tokens. This is the VLM failing to
   predict EOS and looping on a sub-sequence until it hits its token
   budget.

The symptoms are systematic across the 207-paper corpus, not a single
bad PDF. They appear in roughly 5-15% of equation blocks based on
manual sampling.

## Why This Matters

* **Equation index quality.** The corpus's `equations` table feeds
  semantic search and the writing pipeline's grounded-citation logic.
  Repetitive runaway equations contaminate retrieval: a query for
  "Maxwell equation" can match a 500-token loop of `\text{not} \, s`
  and outrank a clean equation.
* **Chunk pollution.** Chunks containing leaked formulas carry garbage
  tokens, lower vector quality, and sometimes unbalanced HTML such as
  `</formula>` without an opener.
* **Wall-clock cost.** Repetition loops are a dominant cost on
  formula-heavy review papers: the heartbeat showed 720 s (Song) and
  1380 s (Zhu) on single papers in the 2026-05-07 run. Granite-Docling
  is autoregressive, so every repeated token is another model step.

## What We Must Not Do

Do not strip leaked tags in `_light_clean`.

A regex strip would hide the visible symptom while leaving:

* broken LaTeX in chunks and embeddings,
* the wall-clock cost of the runaway decode,
* future regressions invisible because the sentinel tags are gone.

The visible HTML in markdown is the only cheap surface signal that the
paper's formula extraction is wrong. Removing it trades one obvious bug
for several hidden ones.

Do not replace contaminated formulas with placeholders in normal
builds. Placeholders still pollute retrieval and make an incomplete
corpus look complete.

## Docling API Baseline

Current Docling documentation shows the standard formula-enrichment
path as:

```python
code_formula_options = CodeFormulaVlmOptions.from_preset("granite_docling")
pipeline_options = PdfPipelineOptions(
    do_ocr=False,  # only when the PDF has a reliable text layer
    do_formula_enrichment=True,
    code_formula_options=code_formula_options,
)
converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
    },
)
doc = converter.convert(path).document
formulas = [
    item for item, _ in doc.iterate_items()
    if isinstance(item, FormulaItem)
]
```

Observed API facts from local inspection:

* `CodeFormulaVlmOptions.from_preset("granite_docling")` is valid.
* `CodeFormulaVlmOptions` has no `.with_overrides()` method.
* Passing `max_new_tokens` directly to `from_preset()` raises because
  it is not a direct field on `CodeFormulaVlmOptions`.
* Granite's token budget is exposed at
  `code_formula_options.model_spec.max_new_tokens` (8192 in the current
  preset).
* Docling's markdown serializer normally serializes `FormulaItem.text`
  directly as `$...$` or `$$...$$`. If leaked tags appear in exported
  markdown, assume `FormulaItem.text` may already be dirty until proven
  otherwise.

## Pipeline Assessment

The current Wikify pipeline is directionally right:

* `src/wikify/ingest/parsers/docling.py` builds
  `PdfPipelineOptions` with `do_formula_enrichment=True` and
  `CodeFormulaVlmOptions.from_preset(opts.formula_model)`.
* `_doc_walk()` reads `FormulaItem.text` from the `DoclingDocument`,
  which is the correct primary equation source.
* Markdown regex extraction is already demoted to a supplement for
  chemical, unicode, named, and image equations when Docling structural
  formulas exist.
* OCR is conditional: enabled only when no text layer is detected,
  unless explicitly forced.
* Layout/OCR batch sizes are throughput and memory controls, not
  quality controls.

The unsound part is that `FormulaItem.text` and exported markdown are
trusted before quality validation. If Granite emits leaked tags or a
loop into `FormulaItem.text`, contamination can enter:

* persisted markdown,
* chunks,
* embeddings,
* `_docling_formulas`,
* equation assets,
* cached `DoclingDocument` JSON.

This needs a parser-boundary assertion before any artifact is
persisted.

## Refactor Plan: Align With Docling And Add Quality Gate

Goal: keep the official Docling construction pattern, make API use
explicit, and prevent contaminated equations from entering downstream
artifacts.

### 1. Split Docling Construction

File: `src/wikify/ingest/parsers/docling.py`

Split converter setup into focused helpers:

```python
def _make_code_formula_options(opts: DoclingOptions):
    ...

def _make_pdf_pipeline_options(accel, opts: DoclingOptions):
    ...

def _make_document_converter(opts: DoclingOptions):
    ...
```

Rules:

* `_make_code_formula_options()` uses
  `CodeFormulaVlmOptions.from_preset(opts.formula_model)`.
* Do not pass unknown direct fields to `from_preset()`.
* Do not invent `.with_overrides()`.
* Any future token cap must mutate a verified public nested field such
  as `options.model_spec.max_new_tokens`, and only after a probe proves
  the active Docling engine consumes it.
* Generation knobs such as `repetition_penalty`,
  `no_repeat_ngram_size`, and `do_sample` must not be wired until the
  active engine path is inspected and verified.

### 2. Make Structural Formula Extraction Explicit

Add:

```python
def _extract_docling_formulas(doc) -> list[dict]:
    ...
```

This is the only Docling structural formula source. `_doc_walk()` can
call it or be split so image/ref walking does not obscure formula
validation.

Extraction rules:

* Iterate `DoclingDocument.iterate_items()`.
* Select `docling_core.types.doc.document.FormulaItem`.
* Read `item.text`.
* Preserve page provenance when available.
* Do not parse structural formulas from exported markdown.

### 3. Add A Parser-Boundary Formula Quality Gate

Add:

```python
def _assert_formula_quality(doc, md_text: str, path: Path) -> None:
    ...
```

Call immediately after:

```python
md_text = doc.export_to_markdown()
```

and before:

* `_light_clean()`,
* `doc.save_as_json()`,
* markdown persistence,
* chunking,
* `_docling_formulas` metadata insertion.

Fail the document if any structural formula or exported markdown
contains:

* `<formula`,
* `</formula>`,
* `<loc_`,
* a 3-gram repeated more than 10 times,
* a formula token count above a diagnostic threshold unless the value
  has been reviewed.

This function is an assertion, not a cleanup pass. It should raise a
typed parse error with counts and examples.

### 4. Quarantine Contaminated Documents

When formula quality assertion fails:

* abort that document before chunks, embeddings, cache JSON, and
  equation rows are persisted,
* append a structured line to `failed_files.log`,
* make `wikify corpus build` exit non-zero by default if any document
  is quarantined.

No placeholder replacement in normal builds.

### 5. Treat Docling Partial Results As Failures

If `document_timeout` is added later, treat Docling timeout or
`PARTIAL_SUCCESS` as document failure. Partial conversion must not be
persisted into corpus state.

## Investigation Plan

### 1. Confirm The Leak Path In Docling Source

Trace the data flow from Granite-Docling's raw decode output to
markdown export:

* Confirm where raw model output shaped like
  `<formula><loc_a><loc_b><loc_c><loc_d>LATEX</formula>` is parsed.
* Confirm whether the dirty tags are already in `FormulaItem.text`.
* Confirm whether markdown export is just faithfully serializing dirty
  structural text.

Output: a one-paragraph note in this file naming the exact function
that emits or preserves the unparsed wrapper.

### 2. Quantify The Contamination Rate

Build `scripts/audit_formula_leak.py` to walk markdown sidecars and,
when available, cached `derived/doclingdoc/*.json` files. Count:

* files with leaked `<formula>` or `<loc_` tokens,
* total leaked-tag count,
* total formula blocks scanned,
* contaminated formula blocks,
* contaminated-block rate = contaminated formula blocks / total
  formula blocks,
* contaminated-token share = tokens inside contaminated blocks / all
  formula-block tokens,
* longest repeated n-gram run.

Run against `build/ald_docling_2026_05_06/`. Output counts per paper
and top 10 worst offenders.

### 3. Reproduce On A Minimal Sample

Pick the worst offender from the audit and re-parse it standalone.
Capture:

* raw VLM decode output if accessible,
* `FormulaItem.text` values,
* exported markdown,
* per-formula token counts,
* per-document wall-clock time.

This determines whether the bug is raw VLM degeneration, parser
post-processing, or markdown serialization.

## Structural Resolution Options

### Option A -- VLM Decode Hardening

Use only verified Docling API surfaces.

Start with a probe:

```python
options = CodeFormulaVlmOptions.from_preset("granite_docling")
options.model_spec.max_new_tokens = 2048
```

Before exposing an env var, prove the active engine consumes the
mutated value. Add metrics for formula token counts, cap-hit counts,
contaminated-block rate, and wall-clock time.

Risk: a real formula can require more tokens than the cap. Any cap hit
is a quality warning and needs spot-checking.

### Option B -- Parser-Side Recovery

Only take this path if the minimal reproduction proves
`FormulaItem.text` is clean while exported markdown is dirty.

If that happens:

1. Replace leaked markdown blocks with clean `FormulaItem.text`.
2. Assert the replacement removed all `<formula` / `<loc_` tokens.
3. Keep structural `_docling_formulas` records unchanged.

Do not do a blind regex strip. Replacement is allowed only when backed
by clean structural text.

### Option C -- Detection + Quarantine

If A and B do not produce verified-clean formulas, quarantine any
document with contaminated formulas:

1. A formula is contaminated if it contains `<formula`, `</formula>`,
   `<loc_`, or a 3-gram repeated more than 10 times.
2. Abort the document before any corpus artifact is persisted.
3. Append a structured `failed_files.log` entry with per-doc and
   per-formula contamination counts.
4. Fail the build non-zero by default.

This preserves quality by keeping known-bad equations out of chunks,
embeddings, and the equation index.

## Recommendation

Do the Docling API alignment refactor first. Then run the source trace
and audit.

Branching rule:

* If worst offenders have contaminated-block rate > 30%, prioritize
  Option A.
* If contaminated-block rate is rare (< 5%) and `FormulaItem.text` is
  clean while markdown is dirty, use Option B.
* Otherwise keep Option C as the safety net.

## Verification

* Unit-test `_assert_formula_quality()` on clean formulas, wrapper
  leaks, `<loc_` tokens, repeated n-grams, and long formulas.
* Unit-test `_make_code_formula_options()` to prove it uses
  `from_preset()` and does not pass invalid direct fields.
* Add an ingest test where a contaminated Docling parse raises before
  markdown/cache/chunks are persisted.
* Run `uv run ruff check src/wikify tests/wikify`.
* Run `uv run pytest tests/wikify/test_docling_options.py
  tests/wikify/test_pipeline_helpers.py tests/wikify/test_cli_corpus.py
  -q`.
* Run the audit script against `build/ald_docling_2026_05_06/` and
  attach the top-10 offender summary.

## Audit Result (2026-05-09)

`scripts/audit_formula_leak.py` against
`build/ald_docling_2026_05_06/` (207 papers, full corpus):

```
[markdown sidecars]
  files: 207  with_leaks: 23  blocks: 479  contaminated: 44
  block_rate: 9.19%
  tokens: 89449  contaminated_tokens: 63898  token_share: 71.44%
  leaked_tag_count: 737

[cached DoclingDocument JSON]
  files: 207  with_leaks: 23  blocks: 479  contaminated: 44
  block_rate: 9.19%
  tokens: 89449  contaminated_tokens: 63898  token_share: 71.44%
```

Two facts immediately useful for the structural-resolution branching:

1. **Markdown sidecar counts match cached doclingdoc JSON exactly.**
   The same 23 papers / 44 blocks / 737 leaked tags appear in both
   surfaces. This rules out a markdown-export bug — the leak is
   already in `FormulaItem.text` before serialisation. Option B
   (parser-side recovery from clean structural text into dirty
   markdown) does not apply.
2. **Token bloat dominates.** 9.19% of blocks but 71.44% of
   formula-block tokens are inside contaminated blocks; worst
   offenders show 3-grams repeated up to ~2000x in a single
   `FormulaItem`. This is the autoregressive-degeneration symptom
   driving the wall-clock cost on formula-heavy review papers.

Top-10 worst offenders (markdown sidecar; cached JSON identical):

```
rate=100.0% ( 2/ 2) tokens=100.0% longest_run=  397  [2025 Chang] ...Radiation Hardening...
rate=100.0% ( 1/ 1) tokens=100.0% longest_run=  648  [2020 Wang]  ...3D memristor array...
rate=100.0% ( 1/ 1) tokens=100.0% longest_run=  193  [2024 Liu]   ...GaOx-based memristor...
rate= 66.7% ( 2/ 3) tokens= 98.5% longest_run=  503  [2020 Wang]  ...In2Se3...
rate= 66.7% ( 2/ 3) tokens= 98.1% longest_run= 1896  [2022 Kim]   ...tantalum-oxide...
rate= 66.7% ( 2/ 3) tokens= 98.4% longest_run= 1966  [2025 Kumar] ...HfO2 a2O5...
rate= 50.0% ( 2/ 4) tokens= 97.1% longest_run= 2008  [2025 Park]  ...Frequency Switching Neuristor...
rate= 50.0% ( 1/ 2) tokens= 97.9% longest_run= 1950  [2024 Ju]    ...NbOx Al2O3 reservoir...
rate= 50.0% ( 1/ 2) tokens= 95.4% longest_run= 1920  [2024 Kim]   ...Forming-less crossbar...
rate= 50.0% ( 1/ 2) tokens= 94.8% longest_run=  164  [2024 So]    ...TiO2 WOx Heterojunction...
```

Branching-rule verdict: worst offenders are well above the 30%
contaminated-block threshold, so **Option A (VLM decode hardening) is
the next workstream** while **Option C (quarantine via the new
`FormulaContaminationError` gate) is already in place** as the safety
net. Option B is ruled out by fact (1).

## Option A Implementation (2026-05-09)

The active Docling installation
(`docling==2.86.0`, `docling_core==2.73.0`) does not expose any
generation knobs on `CodeFormulaVlmOptions`. The code-formula stage
hardwires `max_new_tokens=2048` and an `extra_generation_config`
containing only `skip_special_tokens=False`, even though the
underlying `transformers_engine` already forwards
`repetition_penalty`, `no_repeat_ngram_size`, and `stop_strings`
straight to `model.generate(...)`. Source-trace:

* Hardwired call site:
  `.venv/Lib/site-packages/docling/models/stages/code_formula/code_formula_vlm_model.py:256-269`.
* Engine forwarding contract:
  `.venv/Lib/site-packages/docling/models/inference_engines/vlm/transformers_engine.py:325-393`.
* Incomplete `_post_process` strip (closing tags only, no openers,
  only the standard bbox placeholder): same file, lines 194-219.

Upstream tracking (no fix in flight as of 2026-05-09):

* docling discussion #1254 (formula extraction quality).
* docling issues #2398, #2374, #2478 (location-token leak, spacing,
  decode hang).
* llama.cpp #16678 (granite-docling looping under temp=0,
  repeat_penalty=1.0).

The fix lives in `src/wikify/ingest/parsers/_docling_patches.py`
and is applied once per worker process from `parse()`. Two patches:

1. `CodeFormulaVlmModel.__call__` is replaced so each
   `VlmEngineInput` is built with
   `max_new_tokens=4096`, `stop_strings=["</formula>", "</code>",
   "<end_of_utterance>"]`, and an `extra_generation_config` of
   `{"skip_special_tokens": False, "repetition_penalty": 1.15,
    "no_repeat_ngram_size": 12}`.
2. `CodeFormulaVlmModel._post_process` is extended to also strip the
   `<formula>` / `<code>` opener tokens and arbitrary `<loc_NNN>`
   bbox tokens.

Both patches are idempotent and signature-guarded — if upstream
refactors away the targeted symbols the patch no-ops with a stderr
warning so the regression surfaces instead of silently stomping new
code.

Recovery measured against the audit's worst offenders:

| Paper                            | Before                                           | After                                |
|----------------------------------|--------------------------------------------------|--------------------------------------|
| [2025 Park] Frequency Switching  | rate=50.0% (2/4), tokens=97.1%, longest_run=2008 | 0/4 contaminated, longest_run=3, 33.7 s |
| [2025 Chang] Radiation Hardening | rate=100.0% (2/2), tokens=100.0%, longest_run=397| 0/2 contaminated, longest_run=3, 24.5 s |
| [2020 Wang] 3D memristor array   | rate=100.0% (1/1), tokens=100.0%, longest_run=648| 0/1 contaminated, longest_run=3, 15.0 s |

The `_assert_formula_quality` gate stays in place as defense-in-depth
for any residual leak the generation knobs do not catch.

## References

* Docling enrichment docs:
  `https://docling-project.github.io/docling/usage/enrichments/`
* Docling code/formula example:
  `https://docling-project.github.io/docling/examples/code_formula_granite_docling/`
* Docling pipeline options reference:
  `https://docling-project.github.io/docling/reference/pipeline_options/`
* Docling formula-enrichment extension scaffold:
  `https://docling-project.github.io/docling/examples/develop_formula_understanding/`
* `src/wikify/ingest/parsers/docling.py::_make_standard_options`
* `src/wikify/ingest/equations.py`
