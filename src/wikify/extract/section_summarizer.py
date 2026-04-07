"""Per-section summaries for hierarchical retrieval.

Two modes:
- **Extractive** (default): First 1-2 sentences of each section. Free, instant.
- **LLM** (opt-in): fast-tier LLM factual summaries. ~$0.002/paper.

Stored in Paper.section_summaries as JSON: {"section_path": "summary", ...}.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Sections smaller than this threshold use full text as summary.
_SMALL_SECTION_THRESHOLD = 300  # tokens

# Adjacent small sections batched into a single LLM call up to this limit.
_BATCH_TOKEN_LIMIT = 4000

# Section types not worth summarizing.
_SKIP_TYPES = frozenset({"references", "acknowledgments", "appendix"})


def _extract_lead_sentences(text: str, max_sentences: int = 2) -> str:
    """Extract the first 1-2 sentences from text (extractive summary)."""
    # Split on sentence boundaries (period/question/exclamation followed by space+capital)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    lead = " ".join(sentences[:max_sentences])
    # Cap at 300 chars to keep summaries concise
    if len(lead) > 300:
        lead = lead[:297] + "..."
    return lead.strip()


def summarize_sections_extractive(paper_id: str, force: bool = False) -> dict[str, str]:
    """Generate extractive section summaries (first 1-2 sentences per section).

    Free, instant, no API calls. Good enough for navigation and embedding.

    Args:
        paper_id: Paper ID to summarize.
        force: If True, re-summarize even if summaries already exist.

    Returns:
        Dict mapping section_path -> summary string.
    """
    from sqlmodel import select

    from wikify.extract.section_classifier import classify_section_path
    from wikify.core.store.db import get_session
    from wikify.core.store.models import Chunk, Paper

    with get_session() as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            logger.warning("Paper %s not found", paper_id)
            return {}

        if not force and paper.section_summaries and paper.section_summaries != "{}":
            try:
                existing = json.loads(paper.section_summaries)
                if existing:
                    return existing
            except (json.JSONDecodeError, TypeError):
                pass

        chunks = session.exec(
            select(Chunk).where(Chunk.paper_id == paper_id).order_by(Chunk.chunk_index)
        ).all()

    if not chunks:
        return {}

    # Group chunks by section_path, preserving order
    sections_ordered: list[str] = []
    section_texts: dict[str, str] = {}

    for c in chunks:
        path = c.section_path or "root"
        if path not in section_texts:
            sections_ordered.append(path)
            section_texts[path] = c.content
        else:
            section_texts[path] += "\n\n" + c.content

    summaries: dict[str, str] = {}
    for path in sections_ordered:
        sec_type = classify_section_path(path).value
        if sec_type in _SKIP_TYPES:
            continue
        summaries[path] = _extract_lead_sentences(section_texts[path])

    _persist_summaries(paper_id, summaries)
    logger.info("Generated %d extractive section summaries for %s", len(summaries), paper_id[:16])
    return summaries


def summarize_sections_llm(
    paper_id: str,
    model: str | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Generate LLM-based section summaries (1-2 factual sentences per section).

    Uses the fast tier model for cost efficiency. Opt-in for benchmarking.

    Args:
        paper_id: Paper ID to summarize.
        model: LLM model for summarization.
        force: If True, re-summarize even if summaries already exist.

    Returns:
        Dict mapping section_path -> summary string.
    """
    from sqlmodel import select

    from wikify.core.config import settings
    from wikify.core.llm.client import complete_json
    from wikify.extract.section_classifier import classify_section_path
    from wikify.core.store.db import get_session
    from wikify.core.store.models import Chunk, Paper

    with get_session() as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            logger.warning("Paper %s not found", paper_id)
            return {}

        if not force and paper.section_summaries and paper.section_summaries != "{}":
            try:
                existing = json.loads(paper.section_summaries)
                if existing:
                    return existing
            except (json.JSONDecodeError, TypeError):
                pass

        chunks = session.exec(
            select(Chunk).where(Chunk.paper_id == paper_id).order_by(Chunk.chunk_index)
        ).all()

    if not chunks:
        return {}

    # Group chunks by section_path, preserving order
    sections_ordered: list[str] = []
    section_texts: dict[str, str] = {}
    section_tokens: dict[str, int] = {}

    for c in chunks:
        path = c.section_path or "root"
        if path not in section_texts:
            sections_ordered.append(path)
            section_texts[path] = c.content
            section_tokens[path] = c.token_count
        else:
            section_texts[path] += "\n\n" + c.content
            section_tokens[path] += c.token_count

    summaries: dict[str, str] = {}
    sections_needing_llm: list[str] = []

    for path in sections_ordered:
        sec_type = classify_section_path(path).value
        if sec_type in _SKIP_TYPES:
            continue
        if section_tokens[path] <= _SMALL_SECTION_THRESHOLD:
            summaries[path] = section_texts[path][:200].strip()
        else:
            sections_needing_llm.append(path)

    if not sections_needing_llm:
        _persist_summaries(paper_id, summaries)
        return summaries

    # Batch adjacent sections for LLM calls
    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_tokens = 0

    for path in sections_needing_llm:
        tok = section_tokens[path]
        if current_batch and current_tokens + tok > _BATCH_TOKEN_LIMIT:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(path)
        current_tokens += tok

    if current_batch:
        batches.append(current_batch)

    resolved_model = model or settings.llm_fast_model

    for batch in batches:
        prompt_parts = []
        for path in batch:
            prompt_parts.append(f"=== Section: {path} ===\n{section_texts[path][:3000]}")

        user_prompt = (
            "Summarize each section below in 1-2 factual sentences. "
            "Include specific findings, numbers, and materials where present. "
            "Do NOT write meta-descriptions like 'This section discusses...'. "
            'Return JSON: {{"section_path": "summary", ...}}\n\n' + "\n\n".join(prompt_parts)
        )

        try:
            batch_summaries = complete_json(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a scientific paper summarizer. Return only valid JSON.",
                    },
                    {"role": "user", "content": user_prompt},
                ],
                model=resolved_model,
                max_tokens=1024,
                temperature=0.0,
            )
            summaries.update(batch_summaries)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Section summary LLM call failed: %s", exc)
            for path in batch:
                summaries[path] = _extract_lead_sentences(section_texts[path])

    _persist_summaries(paper_id, summaries)
    logger.info("Generated %d LLM section summaries for %s", len(summaries), paper_id[:16])
    return summaries


# Keep backwards-compatible name
summarize_sections = summarize_sections_extractive


def summarize_sections_batch(
    mode: str = "extractive",
    model: str | None = None,
    force: bool = False,
) -> int:
    """Generate section summaries for all papers that don't have them yet.

    Args:
        mode: "extractive" (free, default) or "llm" (fast tier, opt-in).
        model: LLM model (only used when mode="llm").
        force: If True, re-summarize all papers.

    Returns:
        Number of papers summarized.
    """
    from sqlmodel import select

    from wikify.core.store.db import get_session
    from wikify.core.store.models import Paper

    fn = summarize_sections_llm if mode == "llm" else summarize_sections_extractive

    with get_session() as session:
        papers = session.exec(select(Paper)).all()

    count = 0
    for paper in papers:
        if not force and paper.section_summaries and paper.section_summaries != "{}":
            continue
        try:
            if mode == "llm":
                fn(paper.id, model=model, force=force)
            else:
                fn(paper.id, force=force)
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to summarize %s: %s", paper.id[:16], exc)

    logger.info("Summarized sections for %d papers (mode=%s)", count, mode)
    return count


def _persist_summaries(paper_id: str, summaries: dict[str, str]) -> None:
    """Save section summaries to the Paper record."""
    from wikify.core.store.db import get_session

    with get_session() as session:
        from wikify.core.store.models import Paper

        paper = session.get(Paper, paper_id)
        if paper:
            paper.section_summaries = json.dumps(summaries, ensure_ascii=False)
            session.add(paper)
            session.commit()
