# MCP Architecture Analysis

## TL;DR

The current MCP server has 6 tools. Of those, `list_topics` is broken (reads section
headings, not topics). The other 5 work but miss two critical capabilities: there is no
way for the LLM to trigger the generation pipeline (plan → write), and there is no
efficient corpus overview. Eleven tools are proposed below, two of which require
prerequisite code changes to ScholarForge internals before they can be implemented.

---

## Proposed Tool Set

| Tool | Status | Wraps | Key inputs |
|---|---|---|---|
| `search_papers` | OK | `retrieve.context.retrieve_for_query` | query, top_k, max_tokens |
| `get_paper` | OK | SQLite + Chunk query | pattern (title/author) |
| `deep_read` | OK | SQLite + all chunks | pattern |
| `list_papers` | OK | SQLite Paper query | limit |
| `get_graph_metrics` | OK | `graph.metrics.compute_metrics` | — |
| `list_topics` | **FIX REQUIRED** | vault linker (currently broken) | — |
| `get_corpus_summary` | **NEW** | combines list_papers + graph + vocab | — |
| `generate_section` | **NEW** | `generate.planner` + `generate.writer` | prompt, section_hint, top_k |
| `find_gaps` | **NEW** | graph metrics + topic lookup | — |
| `compare_papers` | **NEW** | two `get_paper` calls + structured output | pattern_a, pattern_b |
| `ingest_paper` | **NEW (deferred)** | `ingest.registry` | file_path or url |

---

## Fix Required Before Anything: `list_topics`

### What is wrong

`list_topics` splits `chunk.section_path` on `.`, strips numeric parts, and calls the
first token the "topic." So a paper chunked as `3.Methods.3.2.DataCollection` yields the
topic "Methods." This is structural noise, not research topics.

Real topics come from `vault/linker.py → compute_all_links()`. They are author-declared
keywords (or vocabulary-matched terms for keywordless papers), normalized and
deduplicated. But they are not stored in SQLite — they live only in:

1. `data/corpus_vocabulary.json` — the canonical vocabulary list
2. Vault note YAML frontmatter (`hasTopic: ["Resistive Switching", ...]`)

### Fix: add a `PaperTopic` table

The right fix is a `PaperTopic` junction table in SQLite, populated during ingestion and
batch refresh alongside the vault write. Schema:

```python
class PaperTopic(SQLModel, table=True):
    paper_id: str = Field(foreign_key="paper.id", primary_key=True)
    topic: str = Field(primary_key=True)      # canonical display form
    is_declared: bool = False                  # True = from paper's own keywords
```

This makes topic queries O(1) without hitting the filesystem, and lets the MCP server
join topic ↔ paper counts with a single SQL query. Without this table, `list_topics`
must parse every vault note at query time, which is slow and fragile.

**This is a prerequisite code change for `list_topics` and `find_gaps`.**

The interim fallback (if you don't want to add the table yet) is to read
`corpus_vocabulary.json` for the vocabulary list and scan vault frontmatter for
assignments — workable for corpora under ~200 papers, not beyond.

---

## Workflow Analysis

### Scenario 1: "Summarize my corpus"

**Current tool chain:**

1. `list_papers` — N papers, all metadata
2. `get_graph_metrics` — hub/bridge/frontier + PageRank ranking
3. `list_topics` — broken (returns section names)

**What's missing:** The LLM must make three tool calls and then synthesize them itself.
There is no single call that returns: paper count, year range, top authors, topic
distribution, and the three hub papers. Each call returns more data than needed for a
summary.

**Proposed fix — `get_corpus_summary` resource/tool:**

Returns a pre-synthesized text block covering everything needed. Detail below.

---

### Scenario 2: "What are the key papers on memristive switching?"

**Current tool chain:**

1. `search_papers(query="memristive switching", top_k=10)` — returns papers + chunks as JSON

**What works:** Semantic search is implemented and correct.

**What's suboptimal:** The tool returns a dict with a `chunks` list, which the LLM must
parse and reassemble into readable text. `RetrievedContext.as_text()` already produces
the right format:

```
### Kim 2021 - 4K-memristor crossbar array
chunk text...
---
### Jo 2010 - Nanoscale memristor device
chunk text...
```

The tool should return this text as its primary output, not raw JSON. Raw JSON should be
optional (`include_raw_json=False` parameter).

---

### Scenario 3: "Write me a literature review section on ALD process optimization"

**Current tool chain:** There is none. `search_papers` can retrieve context, but:

- The LLM has to hold the full literature context in its window and write from raw chunks
- The plan → write pipeline (`planner.plan_paper` + `writer.write_paper`) is never invoked
- The multi-call coherence mechanism (prior 3 sections as running context) is bypassed
- LLM response caching via diskcache is bypassed

**Proposed fix — `generate_section` tool:**

Wraps the full plan → write pipeline for a single section. The LLM asks for a section,
ScholarForge retrieves relevant literature, plans a mini-outline, and writes the body.

```python
@mcp.tool()
def generate_section(
    prompt: str,
    section_hint: str = "",   # e.g. "focus on thermal ALD vs plasma ALD"
    top_k: int = 10,
    target_words: int = 400,
    mode: str = "full",       # "full" = plan+write internally; "context_only" = return chunks
    output_file: str = "",    # if non-empty, write to data/output/<name>.md
) -> str:
    """Generate a review section from the literature corpus.

    mode="full": Retrieves relevant papers, plans the section, and writes it via
        ScholarForge's internal LLM calls. Returns formatted markdown.
    mode="context_only": Returns only the retrieved literature context as
        formatted text. The calling LLM (Claude Code) does the writing.
    """
```

**Cost warning:** `mode="full"` makes ScholarForge's own LLM API calls (via litellm)
in addition to the Claude Code session cost. This requires a separate API key configured
in ScholarForge settings (`SCHOLARFORGE_LLM_MODEL`, etc.) and incurs double API cost:
Claude Code's context window plus ScholarForge's planner + per-section writer calls.
The benefit of `mode="full"` is the multi-section coherence mechanism (prior 3 sections
as running context) — something the calling LLM can't replicate without seeing the full
generation state.

`mode="context_only"` is cheaper and simpler: ScholarForge does retrieval only and
returns the formatted literature context; Claude Code writes directly from it. This
loses the coherence mechanism but avoids the double API cost. For single-section tasks,
`context_only` is usually sufficient. `mode="full"` is worth it for full review papers
where section-to-section coherence matters.

This is the highest-leverage missing tool: it converts ScholarForge from a search
engine into a writing engine accessible from Claude Code.

---

### Scenario 4: "Compare the methodologies of Kim 2021 and Jo 2010"

**Current tool chain:**

1. `get_paper("Kim 2021")` — full paper + all chunks as JSON
2. `get_paper("Jo 2010")` — same

**What works:** This is actually fine. The LLM can synthesize a comparison from two
`get_paper` calls. The data is all there.

**What's suboptimal:** The response includes ALL chunks for each paper, which can be
thousands of tokens of full text when only the Methods sections are relevant.

**Proposed fix — `compare_papers` tool:**

```python
@mcp.tool()
def compare_papers(
    pattern_a: str,
    pattern_b: str,
    focus: str = "",     # e.g. "methodology", "results", "device structure"
) -> str:
    """Compare two papers side by side.

    Retrieves relevant chunks from each paper (filtered by focus if given)
    and returns a structured comparison.
    """
```

This is lower priority — the scenario works today, just verbosely.

---

### Scenario 5: "What gaps exist in my literature?"

**Current tool chain:**

1. `get_graph_metrics` — includes `frontier_papers` (peripheral by degree centrality)

**What works:** Frontier/peripheral papers are correctly identified as niche or emerging.

**What's missing:**
- The response names the papers but not why they're peripheral (are they frontier because
  they're new? because their topic is underrepresented? because no other corpus paper
  cites them?)
- There is no topic-level gap analysis: "You have 0 papers covering X topic despite Y
  papers referencing it"

**Proposed fix — `find_gaps` tool:**

```python
@mcp.tool()
def find_gaps() -> str:
    """Identify coverage gaps and underrepresented topics in the corpus.

    Combines graph metrics (peripheral papers) with topic analysis to surface:
    - Topics mentioned in corpus papers but with no dedicated coverage
    - Papers cited by multiple corpus papers but not ingested themselves
    - Frontier papers that may represent emerging directions
    """
```

This requires the `PaperTopic` table fix. It also needs the citation graph (papers in
bibliography sections that matched zero corpus papers — these are citation ghosts). The
`Citation` table already has `cited_paper_id = None` for unmatched references, which
is exactly this signal.

---

### Scenario 6: "Add this paper to my corpus"

**Current tool chain:** None. The LLM cannot trigger ingestion.

**Proposed fix — `ingest_paper` tool:**

```python
@mcp.tool()
def ingest_paper(
    file_path: str,         # absolute path to PDF/DOCX/etc
    library: str = "",      # override library scope if needed
) -> str:
    """Ingest a document into the knowledge base.

    Parses the document, extracts metadata and chunks, writes a vault note,
    and spawns a background corpus refresh. Returns immediately with ingestion
    status; corpus signals (citations, topics, k-NN) update asynchronously.
    """
```

**Complexity note:** Ingestion triggers a background thread for corpus refresh (topic
recomputation, citation matching, embedding, k-NN recalculation). The tool should return
immediately with `{"status": "ingested", "paper_id": "...", "corpus_refresh": "pending"}`.
The LLM should be informed that search results may not reflect the new paper for ~30
seconds. This is safe to implement — `ingest/registry.py` already handles the background
thread pattern for single-file ingestion.

---

## MCP Resources vs Tools

MCP resources are auto-injected into context without the LLM needing to call anything.
Use them for information the LLM needs in >50% of sessions.

**Good resource candidate — corpus summary:**

The corpus summary (paper count, year range, top topics, hub papers) is small, stable,
and useful for almost every query. Returning it as a resource means Claude Code always
knows the corpus shape without a tool call.

```python
@mcp.resource("wikify://corpus")
def corpus_resource() -> str:
    """Auto-injected corpus overview for every session."""
    # Returns the same output as get_corpus_summary()
```

**Bad resource candidates:**
- Full graph metrics (too large, only needed for gap/structure queries)
- All papers list (scales with corpus size, should be on-demand)
- Chunk content (definitely on-demand only)

**Practical note:** FastMCP supports resources via `@mcp.resource()`. A static resource
that returns a few hundred tokens of corpus metadata costs essentially nothing and
eliminates the "summarize my corpus" tool call sequence entirely.

**Claude Code experience:** When the `wikify://corpus` resource is defined, Claude
Code auto-injects it into every conversation. The researcher opens Claude Code, and
without calling any tool, Claude already knows "you have 63 papers on memristors and
ALD, the top hubs are Kim 2021 and Jo 2010." This is the `instructions` field in
`FastMCP(...)` taken further — instead of generic capability text, the LLM has live
corpus facts from the first message. The `instructions` field (already set in
`mcp_server.py` line 19-24) should be updated to reference the resource so Claude Code
knows to surface it.

---

## Output Format Redesign

Current tools return raw JSON. This forces the consuming LLM to parse structured data
and then reason over it — two cognitive steps instead of one.

**Proposed pattern: text-primary with optional structured data**

Every tool returns a response with:
1. **Primary output**: pre-formatted markdown text optimized for LLM consumption
2. **Optional `data` field**: raw JSON for programmatic consumers

`RetrievedContext.as_text()` already implements the right format for `search_papers`.
The same approach applies to all tools.

Example — `get_corpus_summary` output:

```
## Corpus Summary

**63 papers** ingested (2007–2024). Top authors: Kim Y. (8 papers), Jo S. (5 papers).

**Top Topics** (by paper count):
- Resistive Switching: 31 papers
- Memristors: 28 papers
- Neuromorphic Computing: 19 papers
- ALD: 14 papers

**Hub Papers** (most influential by PageRank):
1. Kim 2021 - 4K-memristor crossbar array (PR: 0.087)
2. Jo 2010 - Nanoscale memristor device as synaptic weight (PR: 0.071)
3. Ambrogio 2018 - Equivalent-accuracy accelerated neural-network training (PR: 0.058)

**Bridge Papers** (connect research clusters):
- Prezioso 2015 - Training and operation of an integrated neuromorphic memory array

**Frontier/Emerging** (peripheral papers worth exploring):
- Chen 2023 - Stochastic memristors for Bayesian inference
```

This is what the LLM needs. It does not need the full PageRank dict.

**For `search_papers` specifically:** return `as_text()` as the primary string, with the
structured dict available via a `include_data=True` parameter.

---

## `get_corpus_summary` Specification

```python
@mcp.tool()
def get_corpus_summary() -> str:
    """Return a synthesized overview of the entire corpus.

    Covers: paper count, year range, top authors, topic distribution,
    hub/bridge/frontier papers. Optimized for LLM consumption.
    Single call replaces: list_papers + get_graph_metrics + list_topics.
    """
```

**Implementation:** Calls `compute_metrics()` once, queries `PaperTopic` for topic
counts, formats as markdown. Should take <1s for a 100-paper corpus. This can be the
same function backing both the `get_corpus_summary` tool and the `wikify://corpus`
resource — the resource just calls it at session start.

---

## Future App Architecture

### The key constraint

The user's future app: researchers bring their own LLM API key or pay for API calls.
The question is whether the MCP server becomes the app backend.

**It should not.** MCP is a protocol for tool-calling by LLM clients (Claude Code,
Cursor, etc.). A user-facing app needs an HTTP API, authentication, multi-user state
management, and a frontend — none of which belong in an MCP server.

### The right architecture: shared service layer

The current code already has the right separation, it just isn't named:

```
┌─────────────────────────────────────────────────────────────┐
│                     Service Layer                            │
│  retrieve.context   graph.metrics   generate.planner        │
│  generate.writer    vault.linker    ingest.registry         │
│  (all the real logic — already implemented)                  │
└─────────────────┬───────────────────────┬───────────────────┘
                  │                       │
    ┌─────────────▼──────────┐  ┌─────────▼──────────────────┐
    │    MCP Adapter          │  │    REST API Adapter         │
    │    mcp_server.py        │  │    (future: FastAPI)        │
    │    (today's interface)  │  │    (future app backend)     │
    └─────────────────────────┘  └────────────────────────────┘
```

`mcp_server.py` tools are already thin wrappers — they call service layer functions and
format output. A future REST API does exactly the same thing with different I/O:
HTTP request/response instead of MCP tool call/return.

**The migration path:**

1. Keep service layer functions unchanged (they're already there)
2. Add a `FastAPI` app that calls the same functions
3. MCP server and REST API coexist, both thin adapters over the same code
4. When the app launches, users authenticate to the REST API; Claude Code users continue
   using the MCP server directly

**No architectural rewrite needed.** The only gap is that some MCP tools do their work
inline (e.g., the SQLite query in `get_paper` is written directly in the tool function).
These should be extracted to service-layer functions before the REST API is built:

```python
# service layer (to be extracted)
def get_paper_by_pattern(pattern: str) -> tuple[Paper | None, list[Chunk]]:
    ...

# MCP adapter (thin)
@mcp.tool()
def get_paper(pattern: str) -> str:
    paper, chunks = get_paper_by_pattern(pattern)
    return format_paper_response(paper, chunks)

# REST adapter (thin, future)
@app.get("/papers/search")
def api_get_paper(pattern: str) -> PaperResponse:
    paper, chunks = get_paper_by_pattern(pattern)
    return PaperResponse(paper=paper, chunks=chunks)
```

This refactor is not needed immediately but should happen before the REST API is built.

### LLM API key handling

The MCP server today uses `settings.llm_model` (from config/env). In the app:
- Bring-your-own-key: user supplies API key via app settings; stored per-user
- Paid API calls: ScholarForge holds API keys server-side; usage metered per user

Neither of these changes the service layer. They change only how the LLM client is
initialized — `llm/client.py` already abstracts this via litellm. Add a `api_key`
parameter to `complete()` and the service layer is ready.

---

## Implementation Priority

Ordered by researcher value-per-effort:

1. **Fix `list_topics`** — Add `PaperTopic` SQLite table, populate during ingestion and
   refresh. Prerequisite for `find_gaps`. Medium effort.

2. **Fix output format** — Change `search_papers` and `get_paper` to return `as_text()`
   as primary output. Low effort, immediate improvement.

3. **Add `get_corpus_summary`** — Single call replaces three tool calls for the most
   common scenario. Low effort once `list_topics` is fixed.

4. **Add `get_corpus_summary` as a resource** — Auto-inject corpus summary so the LLM
   always has context without a tool call. Very low effort.

5. **Add `generate_section`** — Highest leverage: makes ScholarForge a writing engine
   from within Claude Code. Medium effort (wire planner + writer).

6. **Add `find_gaps`** — Depends on `PaperTopic` table fix. Medium effort.

7. **Add `ingest_paper`** — Low effort (wraps existing registry). Needed for
   "add this paper" workflow without leaving Claude Code.

8. **Extract service layer** — Refactor inline SQLite queries in MCP tools to dedicated
   service functions. Medium effort. Prerequisite before REST API is built.

9. **Add `compare_papers`** — Lower priority; current `get_paper` x2 works adequately.

---

## Where the code lives

- `src/wikify/mcp_server.py` — FastMCP server (current)
- `src/wikify/store/models.py` — add `PaperTopic` table here
- `src/wikify/vault/linker.py` — topics source of truth
- `src/wikify/retrieve/context.py` — `as_text()` formatter
- `src/wikify/generate/planner.py` + `writer.py` — generation pipeline to wrap
