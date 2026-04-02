"""Fast generation pipeline: maximize pre-computation, minimize LLM turns.

The standard agent loop takes 20-25 min because the LLM spends turns
deciding what to read, processing 70KB deep_reads, and building the
concept graph. This pipeline pre-computes everything offline and gives
the LLM a single dense prompt to write from.

Phases:
1. OFFLINE (Python, no LLM): frontier order + gap analysis + digests + concept links
2. ONE-SHOT (LLM, 1 call): write the review from pre-computed context

Target: <5 min total, quality comparable to S5 tools-only (composite ~0.6).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_TOPIC = "research topic"


def _normalize_topic(topic: str | None) -> str:
    """Return a safe topic label for prompts and metadata."""
    value = (topic or "").strip()
    return value or DEFAULT_TOPIC


def _artifact_section_guidance(artifact_type_id: str, topic: str) -> str:
    """Build artifact-driven section guidance without hardcoded domain taxonomies."""
    from scholarforge.generate.artifact_types import get_artifact_type

    artifact = get_artifact_type(artifact_type_id)
    required_sections = ", ".join(artifact.sections)
    lines = [
        f"Document type: {artifact.name}. Follow the high-level structure: {required_sections}.",
    ]

    if artifact_type_id == "lit_review":
        lines.append(
            "Use 4-6 thematic body sections between Introduction and Conclusion. "
            "Name those sections from the evidence and the topic, "
            "not from a fixed subject taxonomy."
        )
    else:
        lines.append(
            "Adapt the middle sections to the evidence and the topic instead of forcing a "
            "domain-specific outline."
        )

    lines.append(f"Keep the writing focused on: {topic}.")
    return " ".join(lines)


@dataclass
class FastGenerateResult:
    """Result of a fast generation run."""

    review_text: str = ""
    precompute_time_s: float = 0.0
    llm_time_s: float = 0.0
    total_time_s: float = 0.0
    context_chars: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    papers_used: int = 0


def precompute_context(
    max_papers: int = 15,
    n_deep_digest: int = 5,
    topic: str = DEFAULT_TOPIC,
) -> dict:
    """Pre-compute all context needed for review writing. No LLM.

    Returns a dict with everything the LLM needs in one prompt:
    - Paper digests (abstract + key sections, ~2KB each)
    - Gap analysis
    - Synthesis opportunities
    - Pre-computed concept links from chunk embeddings
    - Frontier order with rationale

    Args:
        max_papers: Papers in the frontier order.
        n_deep_digest: Number of papers to get full digests for
            (rest get abstract-only via get_paper).
    """

    from scholarforge.agent.tools import (
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_paper,
        read_paper_digest,
    )
    from scholarforge.evaluate.frontier import frontier_exploration_order

    topic = _normalize_topic(topic)
    start = time.time()

    # 1. Frontier order
    order = frontier_exploration_order(max_papers=max_papers)

    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

    with get_session() as session:
        papers_db = {p.id: p for p in session.exec(select(Paper)).all()}

    # 2. Read digests for top papers, metadata for the rest
    paper_contexts: list[dict] = []
    for i, (pid, depth, rationale) in enumerate(order):
        p = papers_db.get(pid)
        if not p:
            continue
        name = p.display_name()
        # Use title substring for matching (more reliable than full display_name)
        pattern = p.title[:30] if p.title else name[:30]

        if i < n_deep_digest:
            text = read_paper_digest(pattern, reason=rationale)
            if "No paper found" in text:
                text = get_paper(pattern, reason=rationale)
            paper_contexts.append(
                {
                    "display_name": name,
                    "role": rationale,
                    "content": text[:3000],
                    "depth": "digest",
                }
            )
        else:
            text = get_paper(pattern, reason=rationale)
            paper_contexts.append(
                {
                    "display_name": name,
                    "role": rationale,
                    "content": text[:1000],
                    "depth": "metadata",
                }
            )

    # 3. Gap analysis + synthesis
    gaps = find_corpus_gaps()
    synthesis = find_synthesis_opportunities()

    # 4. Pre-compute concept links from chunk embedding similarity
    # (no LLM — pure vector math)
    concept_links = _precompute_concept_links(papers_db, max_links=30)

    elapsed = time.time() - start
    logger.info("Pre-computation done: %d papers in %.0fs", len(paper_contexts), elapsed)

    return {
        "papers": paper_contexts,
        "gaps": gaps[:3000],
        "synthesis": synthesis[:2000],
        "concept_links": concept_links,
        "topic": topic,
        "precompute_time": elapsed,
    }


def _precompute_concept_links(papers_db: dict, max_links: int = 30) -> str:
    """Load cached concept links or compute from chunk embedding similarity."""
    # Try cached links first (section-filtered, boilerplate-free)
    try:
        from scholarforge.store.precompute import load_concept_links

        links = load_concept_links()
        if links:
            formatted = [
                f"  {link['paper_a'][:40]} <-> {link['paper_b'][:40]} "
                f"(sim={link['chunk_sim']}): {link['shared_label']}"
                for link in links[:max_links]
            ]
            return "## Concept Links (shared scientific content)\n" + "\n".join(formatted)
    except Exception:  # noqa: BLE001
        pass

    # Fall back to original computation
    import numpy as np

    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.embeddings import get_chunk_embeddings, get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()
    if not vibes:
        return ""

    pids = list(vibes.keys())
    vibe_matrix = np.array([vibes[pid] for pid in pids])
    paper_sims = vibe_matrix @ vibe_matrix.T

    # Find moderately similar paper pairs (shared concepts but not duplicates)
    chunks = load_corpus_chunks()
    stored = get_chunk_embeddings([c.id for c in chunks])

    paper_chunks: dict[str, list] = {}
    for c in chunks:
        if c.id in stored:
            paper_chunks.setdefault(c.paper_id, []).append(c)

    links = []
    pairs_checked = 0
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            if not (0.7 < paper_sims[i, j] < 0.9):
                continue
            pairs_checked += 1
            if pairs_checked > 200:
                break

            ca = paper_chunks.get(pids[i], [])
            cb = paper_chunks.get(pids[j], [])
            if not ca or not cb:
                continue

            embs_a = np.array([stored[c.id] for c in ca if c.id in stored])
            embs_b = np.array([stored[c.id] for c in cb if c.id in stored])
            if len(embs_a) == 0 or len(embs_b) == 0:
                continue

            na = np.linalg.norm(embs_a, axis=1, keepdims=True)
            na[na == 0] = 1
            embs_a = embs_a / na
            nb = np.linalg.norm(embs_b, axis=1, keepdims=True)
            nb[nb == 0] = 1
            embs_b = embs_b / nb

            sim = embs_a @ embs_b.T
            bi, bj = np.unravel_index(np.argmax(sim), sim.shape)
            best_sim = float(sim[bi, bj])

            if best_sim > 0.75:
                pa = papers_db.get(pids[i])
                pb = papers_db.get(pids[j])
                concept = ca[bi].content[:80].replace("\n", " ")
                links.append(
                    f"  {pa.display_name()[:40] if pa else '?'} <-> "
                    f"{pb.display_name()[:40] if pb else '?'} "
                    f"(sim={best_sim:.2f}): {concept}..."
                )

        if pairs_checked > 200:
            break

    if not links:
        return ""
    return "## Pre-computed concept links\n" + "\n".join(links[:max_links])


def build_one_shot_prompt(
    context: dict,
    topic: str = DEFAULT_TOPIC,
    word_target: int = 4000,
    artifact_type_id: str = "lit_review",
    journal: str = "",
) -> tuple[str, str]:
    """Build a single-shot system + user prompt from pre-computed context.

    Returns (system_prompt, user_prompt) ready for one litellm.completion call.
    """
    from scholarforge.agent.defaults import build_writer_prompt

    resolved_topic = _normalize_topic(context.get("topic") or topic)
    artifact_guidance = _artifact_section_guidance(artifact_type_id, resolved_topic)

    system_prompt = build_writer_prompt(
        artifact_type_id=artifact_type_id,
        journal=journal,
        field_hint=resolved_topic,
    )

    # Build the user prompt with ALL context
    sections = [f"# Write a {word_target}-word review on: {resolved_topic}\n"]

    # Citation reference list
    citations = "\n".join(f"- [REF:{p['display_name']}]" for p in context["papers"])
    sections.append(f"## Available Citations (copy EXACTLY)\n{citations}\n")

    # Paper summaries
    sections.append("## Paper Summaries\n")
    for p in context["papers"]:
        sections.append(
            f"### {p['display_name']} [{p['depth']}]\nRole: {p['role']}\n{p['content'][:2000]}\n"
        )

    # Gaps
    sections.append(f"## Gaps in the Literature\n{context['gaps']}\n")

    # Concept links
    if context["concept_links"]:
        sections.append(f"{context['concept_links']}\n")

    # Writing instructions
    sections.append(
        f"## Instructions\n"
        f"Write exactly {word_target} words.\n"
        f"{artifact_guidance}\n"
        f"If the artifact is a literature review, keep the body thematic and gap-aware.\n"
        f"Use [REF:DisplayName] citations from the list above.\n"
        f"Include 3-5 figure placeholders where they strengthen the argument.\n"
        f"No em-dashes. One concept per sentence. Every claim cited.\n"
        f"No method disclosure. No banned words.\n"
    )

    return system_prompt, "\n".join(sections)


def fast_generate(
    topic: str = DEFAULT_TOPIC,
    model: str | None = None,
    word_target: int = 4000,
    max_papers: int = 15,
    artifact_type_id: str = "lit_review",
    journal: str = "",
    output_path: str = "data/output/review_fast.md",
) -> FastGenerateResult:
    """Fast generation: pre-compute everything, write in one LLM call.

    Target: <5 min total, comparable quality to S5 tools-only.
    """
    import litellm

    from scholarforge.agent.workflows import export_paper
    from scholarforge.config import settings

    model = model or settings.llm_model
    topic = _normalize_topic(topic)
    total_start = time.time()

    # Phase 1: Pre-compute (no LLM)
    context = precompute_context(max_papers=max_papers, topic=topic)
    precompute_time = context["precompute_time"]

    # Phase 2: Build prompt
    system_prompt, user_prompt = build_one_shot_prompt(
        context,
        topic=topic,
        word_target=word_target,
        artifact_type_id=artifact_type_id,
        journal=journal,
    )
    context_chars = len(system_prompt) + len(user_prompt)

    # Phase 3: One-shot LLM call
    llm_start = time.time()
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=min(16384, word_target * 3),
    )
    llm_time = time.time() - llm_start

    review_text = resp.choices[0].message.content or ""
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0

    # Phase 4: Export
    if review_text:
        export_paper(review_text, output_path, journal=journal, docx=True, pdf=True)

    total_time = time.time() - total_start

    return FastGenerateResult(
        review_text=review_text,
        precompute_time_s=precompute_time,
        llm_time_s=llm_time,
        total_time_s=total_time,
        context_chars=context_chars,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        papers_used=len(context["papers"]),
    )
