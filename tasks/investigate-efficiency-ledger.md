# wikify-investigate efficiency ledger (Phase 1)

Goal: reduce token cost per unit of output without regressing wiki quality.
Baseline from the profiling run (`tasks/investigate-profiling-friction.md`):

- Total **4.69M haiku-eq / 15 calls** over 4 rounds -> 6 articles + 1 data artifact.
- By role: explorer 1.73M, writer 1.73M (~288k/page), data-extractor 0.95M, consolidator 0.16M, organizer 0.12M.
- Dominant waste = tool round-trips fighting contracts, not reasoning (F19 writer re-validation, F2/F6 id spelunking, F1 re-judging, F14 doc-level pagerank).

Accept gate: a fix lands only with green `ruff` + `pytest tests/wikify` and a clear round-trip / behavior reduction, no quality regression. Phase 1 = deterministic contract fixes (measurable via tool-round-trip count, near-zero quality risk).

| item | fix | round-trip / behavior delta | tests | status |
|---|---|---|---|---|
| **F19** | validator grounding tolerates dossier-style rendering noise (whitespace / control chars / inline citation markers) so a quote copied from the readable dossier validates on the first `draft check` (`validator.py:_quote_is_grounded`) | removes the 1-2 extra `draft check` re-validation passes every writer hit (~40-80k haiku-eq/page; ~the single biggest writer sink) | `test_validator_grounding.py` (7) | DONE |
| **F2** | expose `canonical_id` on every chunk row from `corpus_find`/`corpus_show`/`corpus_traverse` (`mcp/envelope.py`); explorer skill points at the field | removes per-chunk SQLite `LIKE` spelunking subagents ran to recover the canonical id (round-0 data agent spent 82 tool_uses largely on this) | `test_mcp_canonical_id.py` (3) | DONE |
| **F6** | `source_text_for` resolves a short `chunk:<hex>` handle to canonical and retries on an exact-match miss (`data/harvest.py`) | removes silent mass-rejection + SQLite spelunking on the data path (the priciest single call in the run) | `test_harvest_handle_resolution.py` (3) | DONE |
| **F14** | `find(by="chunk", rank="pagerank")` projects the doc metric onto chunks and returns chunk rows (`queries.rank_chunks_by_doc_metric`); `by="paper"` still returns docs | P5 ranks residual *chunks* (was doc-level) -> coverage driver works per-chunk -> fewer rounds to a coverage target | `store/test_chunk_pagerank_ranking.py` (3) | DONE |
| **F17** | `work tend` promotes a P5 concept suggestion only with >=2 distinct supporting chunks; deliberate `feedback concept` adds (no `chunk_id`) still promote immediately; sub-threshold suggestions accumulate across rounds (`work/tend.py`) | stops the roster bloat (8 empty `new` cards in the run) that keeps the SEED wave firing on phantom concepts | `test_work_tend.py` (3 new) | DONE |

## Result

All 5 Phase-1 items landed. `uv run ruff check src/wikify tests/wikify` clean; `uv run pytest tests/wikify -q` = **1522 passed, 1 skipped**. 19 new regression tests. Each fix removes a tool round-trip class measured in the profiling run; the combined effect targets the two biggest sinks (writer re-validation F19, id spelunking F2/F6) plus the coverage-driver efficiency (F14) and SEED-wave waste (F17). Phase 2 (structural: chunk-evidence cache, Haiku judging, dossier diet, SENSE batching, budget-aware sizing) is gated pending these.
