"""Scripted exploration + LLM writing workflow.

Unlike the skill route (where the model orchestrates everything via tool_use),
the scripted route is a Python control flow that calls tools directly:

1. EXPLORE: Python code calls tools (frontier order, deep_read, digests, gaps)
2. SUMMARIZE: LLM extracts structured summaries from each paper (one call per paper)
3. WRITE: LLM writes the review from structured notes (one call, no tools)

This enables:
- Local models (7-14B) that can't orchestrate agent loops but can write prose
- Deterministic exploration (same papers read every time)
- Lower token cost (no multi-turn context accumulation)
- Model-agnostic (any litellm-supported model for the write step)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wikify.papers.agent.run_context import record_phase_usage
from wikify.papers.agent.writer_input import DEFAULT_TOPIC, build_writer_input, normalize_topic

if TYPE_CHECKING:
    from wikify.papers.agent.research_notes import ResearchNotes

    pass

logger = logging.getLogger(__name__)


@dataclass
class ScriptedRunResult:
    """Result of a scripted exploration + writing run."""

    review_text: str = ""
    notes_text: str = ""  # serialized research notes
    papers_read: int = 0
    gaps_found: str = ""
    synthesis_found: str = ""
    explore_time_s: float = 0.0
    summarize_time_s: float = 0.0
    write_time_s: float = 0.0
    total_time_s: float = 0.0
    model_used: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


def scripted_explore(
    max_papers: int = 12,
    n_deep: int = 3,
    topic: str = DEFAULT_TOPIC,
    run_context=None,
) -> dict:
    """Phase 1: Python-scripted exploration. No LLM needed.

    Calls tools directly to read papers and collect raw content.
    Returns a dict with all the raw materials for summarization.

    Args:
        max_papers: Total papers in the frontier order.
        n_deep: Number of papers to deep-read (rest are digested).
    """
    from wikify.papers.agent.concept_graph import reset_concept_graph
    from wikify.papers.agent.reading_log import reset_reading_log
    from wikify.papers.agent.run_context import create_run_context, use_run_context
    from wikify.papers.agent.tools import (
        deep_read,
        find_corpus_gaps,
        find_synthesis_opportunities,
        read_paper_digest,
        reset_paper_summaries,
    )
    from wikify.papers.evaluate.frontier import frontier_exploration_order

    topic = normalize_topic(topic)
    context = run_context or create_run_context(topic=topic, strategy="scripted_explore")
    logger.info(
        "Scripted exploration: topic=%s, %d papers, %d deep reads", topic, max_papers, n_deep
    )
    start = time.time()

    with use_run_context(context):
        reset_reading_log()
        reset_paper_summaries()
        reset_concept_graph()

        # Get optimal reading order
        order = frontier_exploration_order(max_papers=max_papers)

        # Resolve paper IDs to display names
        from sqlmodel import select

        from wikify.store.db import get_session
        from wikify.store.models import Paper

        with get_session() as session:
            papers_db = {p.id: p for p in session.exec(select(Paper)).all()}

        # Read papers
        papers_content: list[dict] = []
        for i, (pid, depth, rationale) in enumerate(order):
            paper = papers_db.get(pid)
            if not paper:
                continue

            name = paper.display_name()
            pattern = paper.title[:40] if paper.title else name[:40]
            if i < n_deep:
                # Deep read for seeds
                raw = deep_read(pattern, reason=rationale)
                actual_depth = "full"
                warning = ""
                try:
                    data = json.loads(raw)
                    if data.get("error"):
                        actual_depth = "digest"
                        warning = data["error"]
                        text = read_paper_digest(
                            pattern, reason=f"{rationale} (deep_read fallback)"
                        )[:3000]
                    else:
                        text = data.get("full_text", "")[:5000]  # cap for summarization
                except (json.JSONDecodeError, TypeError):
                    text = raw[:5000]
                papers_content.append(
                    {
                        "name": name,
                        "depth": actual_depth,
                        "role": rationale,
                        "text": text,
                        "warning": warning,
                    }
                )
            else:
                # Digest for the rest
                raw = read_paper_digest(pattern, reason=rationale)
                papers_content.append(
                    {
                        "name": name,
                        "depth": "digest",
                        "role": rationale,
                        "text": raw[:3000],
                    }
                )

        # Gap analysis
        gaps = find_corpus_gaps()
        synthesis = find_synthesis_opportunities()

    elapsed = time.time() - start
    logger.info("Exploration complete: %d papers in %.0fs", len(papers_content), elapsed)

    return {
        "papers": papers_content,
        "gaps": gaps,
        "synthesis": synthesis,
        "topic": topic,
        "explore_time": elapsed,
        "run_context": context,
    }


def scripted_summarize(
    exploration: dict,
    model: str | None = None,
) -> dict:
    """Phase 2: LLM summarizes each paper into structured notes.

    One LLM call per paper (small, focused). Builds ResearchNotes from
    the summaries. This is where the LLM adds value: extracting key
    findings, quantitative data, and gaps from raw text.

    Can use a local model — each call is short and independent.
    """
    import litellm

    from wikify.papers.agent.research_notes import ResearchNotes, SourceSummary
    from wikify.papers.agent.tools import record_paper_summary
    from wikify.config import settings

    model = model or settings.llm_model
    start = time.time()
    summaries: list[SourceSummary] = []
    total_in = 0
    total_out = 0

    for paper in exploration["papers"]:
        prompt = (
            f"Extract structured information from this paper.\n\n"
            f"Paper: {paper['name']}\n"
            f"Role: {paper['role']}\n\n"
            f"Text:\n{paper['text'][:3000]}\n\n"
            f"Return a JSON object with these fields:\n"
            f'{{"key_findings": ["finding 1", "finding 2", ...], '
            f'"quantitative_data": ["specific number", "measurement", ...], '
            f'"relevance": "one sentence", '
            f'"gaps_noted": ["gap 1", ...]}}'
        )

        try:
            resp = litellm.completion(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Extract structured info from academic papers. "
                        "Return valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
            )
            content = resp.choices[0].message.content or "{}"
            total_in += resp.usage.prompt_tokens if resp.usage else 0
            total_out += resp.usage.completion_tokens if resp.usage else 0

            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = {
                    "key_findings": ["See paper text"],
                    "quantitative_data": [],
                    "relevance": paper["role"],
                    "gaps_noted": [],
                }

            role = "hub" if "PageRank" in paper["role"] or "greedy" in paper["role"] else "frontier"

            # Record in session state
            record_paper_summary(
                paper_name=paper["name"],
                key_findings=data.get("key_findings", []),
                quantitative_data=data.get("quantitative_data", []),
                relevance=data.get("relevance", ""),
                gaps_noted=data.get("gaps_noted", []),
                read_depth=paper["depth"],
                role=role,
            )

            summaries.append(
                SourceSummary(
                    display_name=paper["name"],
                    key_findings=data.get("key_findings", []),
                    quantitative_data=data.get("quantitative_data", []),
                    relevance=data.get("relevance", ""),
                    gaps_noted=data.get("gaps_noted", []),
                    read_depth=paper["depth"],
                )
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to summarize %s: %s", paper["name"], exc)
            summaries.append(
                SourceSummary(
                    display_name=paper["name"],
                    key_findings=["Summarization failed"],
                    relevance=paper["role"],
                    read_depth=paper["depth"],
                )
            )

    elapsed = time.time() - start
    logger.info("Summarization complete: %d papers in %.0fs", len(summaries), elapsed)

    # Build ResearchNotes
    notes = ResearchNotes(
        topic=normalize_topic(exploration.get("topic")),
        source_summaries=summaries,
        gap_analysis=exploration["gaps"],
        synthesis_opportunities=exploration["synthesis"],
    )

    return {
        "notes": notes,
        "summarize_time": elapsed,
        "tokens_in": total_in,
        "tokens_out": total_out,
    }


def scripted_write(
    notes: "ResearchNotes",
    model: str | None = None,
    word_target: int = 4000,
    artifact_type_id: str = "lit_review",
    journal: str = "",
) -> dict:
    """Phase 3: LLM writes the review from structured notes.

    Single LLM call with notes as input. The LLM writes prose, no tools.
    For local models with VRAM limits, can be called per-section.

    Args:
        notes: ResearchNotes from Phase 2.
        model: LLM model string (litellm format).
        word_target: Target word count.
        artifact_type_id: Document type for style guide.
        journal: Target journal for formatting.
    """
    import litellm

    from wikify.papers.agent.defaults import build_writer_prompt
    from wikify.config import settings

    model = model or settings.llm_model
    start = time.time()

    system_prompt = build_writer_prompt(
        artifact_type_id=artifact_type_id,
        journal=journal,
        field_hint=notes.topic,
    )

    writer_input = build_writer_input(
        notes,
        word_target=word_target,
        artifact_type_id=artifact_type_id,
        additional_instructions=[
            "Name every gap from the gap analysis explicitly.",
            "State contradictions between papers when the notes support them.",
        ],
    )

    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": writer_input},
        ],
        max_tokens=min(16384, word_target * 3),  # rough tokens-to-words ratio
    )

    content = resp.choices[0].message.content or ""
    elapsed = time.time() - start
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0

    logger.info("Writing complete: %d words in %.0fs", len(content.split()), elapsed)

    return {
        "review_text": content,
        "write_time": elapsed,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def run_scripted(
    topic: str = DEFAULT_TOPIC,
    model: str | None = None,
    summarize_model: str | None = None,
    write_model: str | None = None,
    max_papers: int = 12,
    n_deep: int = 3,
    word_target: int = 4000,
    artifact_type_id: str = "lit_review",
    journal: str = "",
    output_path: str = "data/output/review_scripted.md",
) -> ScriptedRunResult:
    """Run the full scripted pipeline: explore -> summarize -> write -> export.

    Args:
        topic: Review topic.
        model: Default LLM for both summarize and write (litellm format).
        summarize_model: Override model for summarization (e.g., local 7B).
        write_model: Override model for writing (e.g., local 14B or cloud).
        max_papers: Papers in frontier order.
        n_deep: Papers to deep-read.
        word_target: Target word count.
        artifact_type_id: Document type.
        journal: Target journal.
        output_path: Where to save the review.
    """
    total_start = time.time()

    # Phase 1: Explore (no LLM)
    exploration = scripted_explore(max_papers=max_papers, n_deep=n_deep, topic=topic)
    record_phase_usage(
        "scripted_explore",
        duration_s=exploration["explore_time"],
        metadata={"papers": len(exploration["papers"]), "deep_reads": n_deep},
        run_context=exploration["run_context"],
    )

    # Phase 2: Summarize (LLM extracts structured notes)
    sum_model = summarize_model or model
    summarization = scripted_summarize(exploration, model=sum_model)
    notes = summarization["notes"]
    record_phase_usage(
        "scripted_summarize",
        duration_s=summarization["summarize_time"],
        tokens_in=summarization["tokens_in"],
        tokens_out=summarization["tokens_out"],
        metadata={"papers": len(exploration["papers"])},
        run_context=exploration["run_context"],
    )

    # Phase 3: Write (LLM writes prose from notes)
    wr_model = write_model or model
    writing = scripted_write(
        notes,
        model=wr_model,
        word_target=word_target,
        artifact_type_id=artifact_type_id,
        journal=journal,
    )
    record_phase_usage(
        "scripted_write",
        duration_s=writing["write_time"],
        tokens_in=writing["tokens_in"],
        tokens_out=writing["tokens_out"],
        metadata={"word_target": word_target},
        run_context=exploration["run_context"],
    )

    # Phase 4: Export
    from wikify.papers.agent.workflows import export_paper

    review_text = writing["review_text"]
    if review_text:
        export_paper(review_text, output_path, journal=journal, docx=True, pdf=True)

    total_time = time.time() - total_start

    return ScriptedRunResult(
        review_text=review_text,
        notes_text=notes.to_writer_prompt(),
        papers_read=len(exploration["papers"]),
        gaps_found=exploration["gaps"][:500],
        synthesis_found=exploration["synthesis"][:500],
        explore_time_s=exploration["explore_time"],
        summarize_time_s=summarization["summarize_time"],
        write_time_s=writing["write_time"],
        total_time_s=total_time,
        model_used=wr_model or "default",
        tokens_in=summarization["tokens_in"] + writing["tokens_in"],
        tokens_out=summarization["tokens_out"] + writing["tokens_out"],
    )
