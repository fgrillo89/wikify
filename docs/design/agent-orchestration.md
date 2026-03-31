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
    Turn 2:   deep_read(seed_1) -> 70KB text
    Turn 3:   record_paper_summary(seed_1, findings, data) -> 50 bytes
              [Turn 2 tool result compacted to 200 bytes]
    Turn 4:   deep_read(seed_2) -> 70KB text
    Turn 5:   record_paper_summary(seed_2, ...) -> 50 bytes
              [Turn 4 compacted]
    Turn 6-10: read_paper_digest(frontiers) + record_paper_summary each
    Turn 11:  find_corpus_gaps() -> gap analysis
    Turn 12:  find_synthesis_opportunities() -> synthesis pairs
    Turn 13:  search_papers(gap_query) -> 1 targeted search
    Turn 14:  [no tool call] -> writes the review as final output
              |
              v
         export_paper() -> .md + .docx + .pdf
```

### Token flow with compaction

Without compaction: each turn resends ALL prior tool results.
```
Turn  1:  3.8K (system) + 0.5K (user) = 4.3K
Turn  2:  4.3K + 70K (deep_read_1) = 74.3K
Turn  5:  4.3K + 70K + 70K + ... = 214K (growing)
Turn 14:  4.3K + 280K (4 deep reads) + 50K (digests) = 334K  <- peak
```

With compaction: tool results truncated after the LLM responds.
```
Turn  1:  4.3K
Turn  2:  4.3K + 70K = 74.3K (deep_read in context)
Turn  3:  4.3K + 0.2K (compacted) + 0.05K (summary) = 4.6K  <- dropped!
Turn  5:  4.6K + 70K = 74.6K (next deep_read)
Turn  6:  4.6K + 0.4K (2 compacted) + 0.1K (2 summaries) = 5.1K
Turn 14:  ~15K total (all compacted, summaries in context)
```

Peak context drops from ~334K to ~75K. Cumulative tokens saved: ~300K per run.

## Two-Agent Mode (Optional)

```
User prompt
    |
    v
EXPLORER AGENT                          WRITER AGENT
(all reading/search/gap tools)          (read_paper_digest + search_papers only)
    |                                        |
    |  1. get_frontier_order()               |
    |  2. deep_read + record_summary (x3)   |
    |  3. digest + record_summary (x7)      |
    |  4. find_corpus_gaps()                 |
    |  5. find_synthesis_opportunities()     |
    |  6. search_papers(gap)                 |
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

## Read-Once-Summarize Pattern

The key efficiency mechanism. After every deep_read:

```
Agent: deep_read("Li 2018") -> 70KB response
Agent: record_paper_summary(
    paper_name="Li 2018 - In-Memory Computing",
    key_findings=["128x64 Ta/HfO2 1T1R array", "91.7% MNIST accuracy", ...],
    quantitative_data=["2-pulse weight update", "11% failure tolerance", ...],
    relevance="Largest ALD-compatible memristor array demonstration",
    gaps_noted=["No endurance data beyond 10^4 cycles"],
) -> 50 bytes confirmation

[deep_read result compacted on next turn]

Later:
Agent: get_session_context() -> all summaries in ~2KB
```

The 70KB is consumed once and discarded. The 200-byte summary persists.

## Tool Result Compaction

Implemented in `core.py::_compact_tool_results()`:

1. Runs at the START of each turn (after turn 0)
2. Walks the message list backward to find the last assistant message
3. For all tool messages BEFORE that assistant message with content > threshold:
   - Keeps first 200 chars as preview
   - Replaces rest with compaction notice
4. The agent can call `get_session_context()` to recall summaries

Configurable via `settings.enable_tool_compaction` (default True) and
`settings.tool_compaction_threshold` (default 2000 chars).

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
