"""Baseline strategy configuration and deterministic helpers.

Replaces the old `baselines.pipeline` module after the legacy
`run_baseline()` orchestrator was retired in the skill-pivot. The
skill-driven workflow under `.claude/skills/wikify/workflows/run-baseline.md`
consumes only these knobs and the per-page evidence-retrieval helper.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineConfig:
    """Knobs for the abstract-first baseline.

    Cost shaves the baseline takes:

    - ``writer_skip_cited_corpus_chunks``: don't pass the knowledge graph
      to the writer, so cited-corpus-chunk graph walks are skipped. Saves
      ~5-12k input tokens per write call.
    - ``writer_max_length_chars``: cap writer output via a planted default
      ``EditorBrief``. Without this the writer drifts to 2-4k tokens of
      output; tight cap saves ~30k heq per write at tier M.
    - ``min_evidence_chunks``: minimum evidence a page must accumulate
      before the writer runs on it. Trades page count for per-page
      substance.
    """

    # Hardwired write fraction; baseline does not honor strategy overrides.
    write_fraction: float = 0.35
    # Fraction of the extract budget consumed by abstract seeding.
    abstract_fraction: float = 1.0 / 3.0
    # PageRank weight in the greedy seed objective; coverage_gain weight is
    # ``1 - pagerank_weight``.
    pagerank_weight: float = 0.7
    # Per-page evidence retrieval.
    evidence_top_k: int = 8
    evidence_max_per_source: int = 2
    # Hard cap on number of seed documents the greedy ranker will return.
    max_seeds: int = 20
    # Cost shaves on the writer side.
    writer_skip_cited_corpus_chunks: bool = True
    # 8000 chars ~ 2000 tokens output; solid Wikipedia article length.
    writer_max_length_chars: int = 8000
    # Minimum evidence floor a page must satisfy before the writer runs.
    min_evidence_chunks: int = 3


def select_evidence_chunks_for_page(
    *,
    page_title: str,
    kg,
    top_k: int,
    max_per_source: int,
    seen_chunk_ids: set[str] | None = None,
) -> list[str]:
    """Pull top_k evidence chunks for one page, applying per-source cap.

    Public entry point for the skill-driven `wikify kg evidence` CLI.
    Preserves the ranking and dedup behaviour from the retired
    `_select_evidence_chunks` loop body.
    """
    seen = set(seen_chunk_ids or set())
    hits = kg.chunks().search(page_title, top_k=top_k * 4)
    out: list[str] = []
    per_doc: dict[str, int] = {}
    for hit in hits:
        cid = hit.get("id") or hit.get("chunk_id")
        if not cid or cid in seen:
            continue
        doc_id = hit.get("source_id") or hit.get("doc_id") or ""
        if per_doc.get(doc_id, 0) >= max_per_source:
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        out.append(cid)
        seen.add(cid)
        if len(out) >= top_k:
            break
    return out
