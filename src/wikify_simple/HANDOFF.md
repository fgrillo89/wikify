# wikify_simple — Handoff & Restart Guide

## What was done this session

### Architecture: Editor-Writer Pipeline

Redesigned the distillation pipeline from a flat extract→write loop into a
four-role architecture:

1. **Extractor** — reads chunks, produces rich dossiers (definition, summary,
   parameters, mechanisms, relationships, equations per concept)
2. **Compactor** — consolidates raw dossier entries when they exceed a threshold
   (dedup definitions, merge parameters, rank evidence)
3. **Editor** — reads compacted dossiers + wiki index, decides write-readiness,
   produces section-by-section briefs for the writer
4. **Writer** — follows the editor's brief, writes the article

All four roles have:
- Pydantic schemas (`agents/schema.py`)
- Protocols (`agents/protocols.py`)
- Fake bindings for testing (`bindings/fake.py`)
- ClaudeCode dispatcher bindings (`bindings/claude_code.py`)
- Dispatch directories (`data/dispatch/{extract,compact,edit,write}/`)
- Drain skills (`.claude/skills/wikify_simple/{extract,compact,edit,write}.md`)
- Prompts (`prompts/{extract_v2,compact_v1,edit_v1,write_v2}.yaml`)

### Dossier System

Per-concept dossiers persist at `<bundle>/_dossiers/<id>.json`. They accumulate
across extract calls and survive `--feed` incremental runs. Each dossier has:
- Raw entries (one per chunk that mentions the concept)
- Compacted fields (canonical definition, merged parameters, etc.)
- Substance heuristic (2+ entries, has definition or summary)

### Corpus Profiling

`store/corpus_profile.py` computes:
- PageRank on unified doc graph (cites + doc_similar — works for any document type)
- Louvain community detection (via networkx)
- Betweenness centrality for bridge detection
- Hub chunks by similarity-graph degree

### Ingestion Improvements

- Section type classification (ported from legacy: abstract/methods/results/conclusion/etc.)
- Conclusion fallback (promotes last substantive section)
- Chunk overlap between consecutive chunks
- Extended boilerplate cleaning (copyright, DOI, volume/issue, dates)
- Journal name filter + non-name word filter in author validation
- Equation extraction schema (mathematical + chemical formulas)

### HTML Rendering

- Skeleton pages filtered (only pages with 200+ chars prose)
- Journal/garbage person pages filtered
- See Also with co-occurrence fallback
- Infobox, article cards, multi-column people list
- Evidence formatted as bibliographic references (Author (Year). *Title.*)
- Chunk hashes stripped, "Evidence" → "References"

### What produced good results

The mvp20_v2 run produced 60 concept pages with sonnet-quality prose. The
articles were written by spawning sonnet subagents for the write dispatch
requests. The Crossbar Array, Hafnium Oxide, and Resistive Switching articles
are representative of the quality level achieved.

Output: `data/wikify_simple/wikis/mvp20_v2/M_2000000_seed0_20260409T134347/_html/`

## The speed problem

The dispatcher pattern (write request file → poll for response file → subagent
reads/processes/writes → pipeline picks up) adds ~30-60 seconds per call.
With 60+ extract + 13+ write calls, a full run takes 30-90 minutes.

The legacy pipeline was faster because it called the model directly via
litellm/anthropic SDK. The dispatcher pattern exists because wikify_simple
was designed to run without an API key, using Claude Code subagents as the
model backend.

### Fix options (choose one):

1. **Set ANTHROPIC_API_KEY** and use `scripts/drain_extract.py` which calls
   litellm directly. Each call takes ~2 seconds instead of ~45. This is the
   right solution for production use.

2. **Use the drain script with litellm** (`scripts/drain_extract.py`):
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   uv run python scripts/drain_extract.py --poll-seconds 2
   ```
   This handles all four dispatch types (extract, compact, edit, write) and
   calls the model directly. ~2s per extract, ~5s per write.

3. **Run with `--binding fake` for testing**: instant but produces placeholder
   content. Good for validating pipeline mechanics, not for output quality.

## How to restart the work

### Quick test (fake binding, validates pipeline)
```bash
WIKIFY_SIMPLE_EMBEDDER=sentence_transformers uv run python -m wikify_simple.cli distill \
  --strategy M --binding fake --budget 1x --seed 0 \
  --corpus data/wikify_simple/corpora/mvp20_v2 \
  --out data/wikify_simple/wikis/test_run
```

### Real run with API key (recommended)
```bash
# Terminal 1: start the pipeline
export WIKIFY_SIMPLE_ALLOW_NETWORK=1
export WIKIFY_SIMPLE_EMBEDDER=sentence_transformers
uv run python -m wikify_simple.cli distill \
  --strategy M --binding claude_code --budget 2000000 --seed 0 \
  --corpus data/wikify_simple/corpora/mvp20_v2 \
  --out data/wikify_simple/wikis/mvp20_v3

# Terminal 2: start the drain (needs API key)
export ANTHROPIC_API_KEY=sk-ant-...
uv run python scripts/drain_extract.py --poll-seconds 2
```

### Real run with subagent drain (no API key, slow)
```bash
# Terminal 1: start the pipeline (same as above)

# Terminal 2: in Claude Code, spawn ONE sonnet subagent that monitors
# ALL FOUR dispatch directories (extract, compact, edit, write) and
# processes requests as they arrive. See the drain skills in
# .claude/skills/wikify_simple/ for the response schemas.
```

### After the run: render HTML
```bash
BUNDLE=data/wikify_simple/wikis/mvp20_v3/M_*
uv run python -m wikify_simple.cli html --bundle $BUNDLE --out $BUNDLE/_html
```

### Compare v2 vs v3
```bash
# v2 output (60 concept pages, sonnet-written):
# data/wikify_simple/wikis/mvp20_v2/M_2000000_seed0_20260409T134347/_html/

# v3 output (with editor-writer architecture):
# data/wikify_simple/wikis/mvp20_v3/M_*/_html/
```

## Key files

| File | Purpose |
|------|---------|
| `distill/pipeline.py` | Main pipeline loop |
| `distill/dossier.py` | Dossier model + persistence |
| `agents/schema.py` | All Pydantic schemas (v2 extraction, EditorBrief, etc.) |
| `agents/protocols.py` | Extractor, Compactor, Editor, Writer protocols |
| `bindings/claude_code.py` | Dispatcher bindings for all 4 roles |
| `bindings/fake.py` | Deterministic fakes for testing |
| `store/corpus_profile.py` | PageRank, Louvain, betweenness |
| `ingest/section_classifier.py` | Section type detection |
| `ingest/chunker.py` | Section-aware chunking with overlap |
| `render/html/render.py` | HTML renderer with filtering + formatting |
| `prompts/extract_v2.yaml` | Rich extraction prompt |
| `prompts/edit_v1.yaml` | Editor brief prompt |
| `prompts/write_v2.yaml` | Writer prompt (follows brief) |
| `prompts/compact_v1.yaml` | Compactor prompt |
| `scripts/drain_extract.py` | litellm-based drain (needs API key) |
| `scripts/drain_heuristic.py` | Heuristic drain (no model calls) |
| `docs/design/editor-writer-architecture.md` | Architecture design doc |

## Open issues

1. **Speed**: Dispatcher pattern is 15-30x slower than direct API calls.
   Setting ANTHROPIC_API_KEY and using drain_extract.py is the fix.

2. **Heuristic extraction still used as fallback**: The drain_heuristic.py
   script uses regex patterns for extraction. This produces thin dossiers
   (no definitions, summaries, parameters). Real model-based extraction
   via extract_v2.yaml is needed for quality output.

3. **Editor not yet tested with model**: The FakeEditor produces rule-based
   briefs. A real model-backed editor (via ClaudeCodeEditor dispatcher or
   litellm) would produce per-page editorial judgment.

4. **Dossier substance check too strict**: `has_substance` requires a
   definition or summary, which only model-based extraction provides.
   Heuristic extraction always fails this check.

5. **20-paper corpus limitations**: PageRank, communities, and bridge
   detection work but don't differentiate much with only 20 papers.
   The profiling shines at 50+ documents.
