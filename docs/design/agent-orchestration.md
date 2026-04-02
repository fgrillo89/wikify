# Agent Orchestration

How ScholarForge explores a corpus and generates output, with token efficiency.

## Single-Agent Mode (Default)

```
User prompt: "Write a review on ALD memristors"
              |
              v
    ScholarForgeAgent (all tools, 30 turns max)
              |
    Turn 1:   get_frontier_exploration_order() -> reading order
    Turn 2:   read_paper_digest(seed_1) -> 1.5KB digest
    Turn 3:   read_section(seed_1, "results") -> targeted detail
    Turn 4:   record_paper_summary(seed_1, findings, data) -> 50 bytes
              [Turns 2-3 compacted after use]
    Turn 5:   read_paper_digest(seed_2) -> 1.5KB digest
    Turn 6:   read_section(seed_2, "methods/results") -> targeted detail
    Turn 7:   record_paper_summary(seed_2, ...) -> 50 bytes
    Turn 8-12: read_paper_digest(frontiers) + record_paper_summary each
    Turn 13:  find_corpus_gaps() -> gap analysis
    Turn 14:  find_synthesis_opportunities() -> synthesis pairs
    Turn 15:  search_papers(gap_query) -> 1 targeted search
    Turn 16:  deep_read(seed_x) only if digest + sections were insufficient
    Turn 17:  [no tool call] -> writes the review as final output
              |
              v
         export_paper() -> .md + .docx + .pdf
```

### Token flow with compaction

Without compaction: each turn resends ALL prior tool results.
```
Turn  1:  3.8K (system) + 0.5K (user) = 4.3K
Turn  2:  4.3K + 1.5K (digest_1) = 5.8K
Turn  3:  5.8K + 5K (section_1) = 10.8K
Turn 10:  4.3K + multiple digests + sections = 40-60K
Turn 17:  only rare deep_read calls push the run toward the old 70K spikes
```

With compaction: tool results truncated after the LLM responds.
```
Turn  1:  4.3K
Turn  2:  4.3K + 1.5K = 5.8K
Turn  4:  summaries persist, digest/section text is compacted
Turn 10:  active context is mostly summaries + recent targeted reads
Turn 17:  final write turn stays close to summaries-in-context rather than raw-read replay
```

Peak context now depends much more on how often `deep_read` is invoked. The
default hierarchical path keeps most runs in digest/section territory and only
hits the old 70KB spikes when a true full-paper escalation is warranted.

## Two-Agent Mode (Optional)

```
User prompt
    |
    v
EXPLORER AGENT                          WRITER AGENT
(all reading/search/gap tools)          (read_paper_digest + search_papers only)
    |                                        |
    |  1. get_frontier_order()               |
    |  2. digest + targeted sections         |
    |  3. record_summary after each read     |
    |  4. rare deep_read escalation          |
    |  5. find_corpus_gaps()                 |
    |  6. find_synthesis_opportunities()     |
    |  7. search_papers(gap)                 |
    |                                        |
    v                                        |
ResearchNotes (~5KB)  ------------------>    |
    - 10 paper summaries                     |  Receives notes as input
    - gap analysis                           |  Writes review from notes
    - synthesis opportunities                |  Can call tools if needed
    - proposed outline                       |  (but rarely does)
    - contradictions                         |
                                             v
                                        Review markdown
                                             |
                                             v
                                    export_paper() -> .md .docx .pdf
```

The writer's context is ~5KB of structured notes instead of ~280KB of raw text.
Token budget: explorer 65%, writer 35% of total.

The writer handoff is now shared across routes. The two-agent, scripted, and
fast one-shot modes all build the final writer request from `ResearchNotes`
plus the same citation list and artifact guidance, rather than each route
carrying its own prompt format.

## Run Context

Every generation or exploration run now has its own `RunContext`.

That context owns:

- the reading log
- paper summaries
- the concept graph
- phase-level usage telemetry
- non-fatal run warnings

This is important for orchestration because compaction and summary reinjection
now operate on run-local state rather than ambient process globals.

## Read-Once-Summarize Pattern

The key efficiency mechanism. After every substantive read:

```
Agent: read_paper_digest("Li 2018") -> 1.5KB response
Agent: read_section("Li 2018", "results") -> targeted detail
Agent: record_paper_summary(
    paper_name="Li 2018 - In-Memory Computing",
    key_findings=["128x64 Ta/HfO2 1T1R array", "91.7% MNIST accuracy", ...],
    quantitative_data=["2-pulse weight update", "11% failure tolerance", ...],
    relevance="Largest ALD-compatible memristor array demonstration",
    gaps_noted=["No endurance data beyond 10^4 cycles"],
) -> 50 bytes confirmation

[digest/section results compacted on next turn]

Later:
Agent: get_session_context() -> all summaries in ~2KB
```

Large reads are consumed once and discarded. The compact summary persists.

## Tool Result Compaction

Implemented in `core.py::_compact_tool_results()`:

1. Runs at the START of each turn (after turn 0)
2. Walks the message list backward to find the last assistant message
3. For all tool messages BEFORE that assistant message with content > threshold:
   - Keeps first 200 chars as preview
   - Replaces rest with compaction notice
4. The agent can call `get_session_context()` to recall summaries from the active run

Configurable via `settings.enable_tool_compaction` (default True) and
`settings.tool_compaction_threshold` (default 2000 chars).

## Structured Tool Errors

JSON-oriented tools now return stable envelopes with `ok: true/false`.
Agent-side tool execution failures are also normalized to JSON with:

- `ok`
- `tool`
- `error`

That makes a lookup failure or execution failure machine-distinguishable from a
weak-but-valid retrieval result.

`ingest_paper` follows the same contract, and export now logs an explicit
warning when DOCX-to-PDF conversion fails and the workflow falls back to
HTML-to-PDF.

## Extensibility Beyond Papers

The architecture is designed to work beyond academic papers:

- **SourceSummary** (not PaperSummary) — generic enough for patents, reports, datasets
- **ResearchNotes** — generic enough to feed reviews, slides, abstracts, Q&A
- **Tools** are the extension point: swap `deep_read` for a patent reader or dataset loader
- **Explorer prompt** and **writer prompt** are separate and swappable
- **Quality metrics** work on any text against any embedding corpus

The same explore -> summarize -> write pattern applies to:
- Patent landscape analysis
- Technical report writing
- Grant proposal background sections
- Dataset documentation
- Literature-grounded Q&A
