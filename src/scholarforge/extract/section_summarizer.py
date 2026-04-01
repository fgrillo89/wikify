"""Generate per-section summaries using Haiku for cost-efficient indexing.

Produces 1-2 factual sentences per section, stored in Paper.section_summaries.
Cost: ~$0.002 per paper (3-5 Haiku calls for a typical 15-section paper).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Sections smaller than this get their raw text as the summary (no LLM call).
_SMALL_SECTION_THRESHOLD = 300  # tokens

# Adjacent small sections are batched into a single LLM call up to this limit.
_BATCH_TOKEN_LIMIT = 4000


def summarize_sections(
    paper_id: str,
    model: str = "claude-haiku-4-5-20251001",
    force: bool = False,
) -> dict[str, str]:
    """Generate 1-2 sentence summaries for each section of a paper.

    Groups chunks by section_path, batches small sections, and calls
    the LLM to produce factual summaries with specific findings/numbers.

    Args:
        paper_id: Paper ID to summarize.
        model: LLM model for summarization.
        force: If True, re-summarize even if summaries already exist.

    Returns:
        Dict mapping section_path -> summary string.
    """
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Chunk, Paper

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

    # Skip references/acknowledgments — not worth summarizing
    skip_types = {"references", "acknowledgments", "appendix"}
    from scholarforge.extract.section_classifier import classify_section_path

    summaries: dict[str, str] = {}

    # For small sections, use the text directly (trimmed)
    sections_needing_llm: list[str] = []
    for path in sections_ordered:
        sec_type = classify_section_path(path).value
        if sec_type in skip_types:
            continue
        if section_tokens[path] <= _SMALL_SECTION_THRESHOLD:
            # Use first 200 chars as summary for tiny sections
            summaries[path] = section_texts[path][:200].strip()
        else:
            sections_needing_llm.append(path)

    if not sections_needing_llm:
        _persist_summaries(paper_id, summaries)
        return summaries

    # Batch adjacent small-ish sections, send larger ones individually
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

    # Call LLM for each batch
    import litellm

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
            response = litellm.completion(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a scientific paper summarizer. Return only valid JSON.",
                    },
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1024,
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip()
            # Extract JSON from response (handle markdown code blocks)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            batch_summaries = json.loads(raw)
            summaries.update(batch_summaries)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Section summary LLM call failed: %s", exc)
            # Fallback: use first 200 chars of each section
            for path in batch:
                summaries[path] = section_texts[path][:200].strip()

    _persist_summaries(paper_id, summaries)
    logger.info("Generated %d section summaries for %s", len(summaries), paper_id[:16])
    return summaries


def summarize_sections_batch(
    model: str = "claude-haiku-4-5-20251001",
    force: bool = False,
) -> int:
    """Generate section summaries for all papers that don't have them yet.

    Returns:
        Number of papers summarized.
    """
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

    with get_session() as session:
        papers = session.exec(select(Paper)).all()

    count = 0
    for paper in papers:
        if not force and paper.section_summaries and paper.section_summaries != "{}":
            continue
        try:
            summarize_sections(paper.id, model=model, force=force)
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to summarize %s: %s", paper.id[:16], exc)

    logger.info("Summarized sections for %d papers", count)
    return count


def _persist_summaries(paper_id: str, summaries: dict[str, str]) -> None:
    """Save section summaries to the Paper record."""
    from scholarforge.store.db import get_session

    with get_session() as session:
        from scholarforge.store.models import Paper

        paper = session.get(Paper, paper_id)
        if paper:
            paper.section_summaries = json.dumps(summaries, ensure_ascii=False)
            session.add(paper)
            session.commit()
