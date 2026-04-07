"""Wikipedia-format article writer for the epoch model (Pass 3).

For each ConceptRecord, this module:
1. Maps corpus chunks to the concept topic (fast tier, map phase).
2. Queries ConceptRelation rows and neighbor ConceptRecords.
3. Builds a Wikipedia-format article body using a custom reduce prompt
   that requests Definition / Mechanism-Process / Key Facts /
   In This Corpus / Relationships / Open Questions sections.
4. Records SourceCoverage rows after writing.

Upgrade path (existing stubs/drafts):
- detect_contradiction() per extraction -> revisionary_update or additive_update.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlmodel import or_, select

from wikify.config import settings
from wikify.llm.client import complete
from wikify.store.db import get_session
from wikify.store.models import ConceptRecord, ConceptRelation
from wikify.wiki.maintenance import additive_update, detect_contradiction, revisionary_update
from wikify.wiki.mapreduce import (
    FAST_MODEL,
    SourceExtraction,
    _build_evidence_block,
    map_chunks_to_topic,
    record_coverage,
)
from wikify.wiki.persona import get_or_create_persona

logger = logging.getLogger(__name__)


# ── Private helpers ───────────────────────────────────────────────────────────


def _build_relationships_table(concept_id: str, neighbors: list[ConceptRecord]) -> str:
    """Build a Markdown relationships table for a concept.

    Queries ConceptRelation rows where source_concept or target_concept equals
    concept_id, then cross-references the supplied neighbor list for display
    names.  Neighbor ConceptRecords not present in DB relations are included
    with a blank relation type so that graph-proximity is still surfaced.

    Args:
        concept_id: ConceptRecord.id (slug) of the focal concept.
        neighbors: List of ConceptRecord instances from concept_graph.

    Returns:
        Markdown table string (header + rows).  Returns an empty string when
        there are no neighbors AND no DB relations.
    """
    neighbor_map: dict[str, ConceptRecord] = {n.id: n for n in neighbors}

    with get_session() as session:
        relations: list[ConceptRelation] = list(
            session.exec(
                select(ConceptRelation).where(
                    or_(
                        ConceptRelation.source_concept == concept_id,
                        ConceptRelation.target_concept == concept_id,
                    )
                )
            ).all()
        )

    # Build rows: (slug, display_name, relation_type)
    rows: list[tuple[str, str, str]] = []
    seen_slugs: set[str] = set()

    for rel in relations:
        # The "other" end of the relation
        other_id = rel.target_concept if rel.source_concept == concept_id else rel.source_concept
        if other_id in seen_slugs:
            continue
        seen_slugs.add(other_id)

        # Prefer the ConceptRecord display name; fall back to the slug
        neighbor_rec = neighbor_map.get(other_id)
        display = neighbor_rec.name if neighbor_rec else other_id.replace("_", " ").title()
        rows.append((other_id, display, rel.relation_type))

    # Add graph-neighbors not yet in the relation rows
    for n in neighbors:
        if n.id in seen_slugs or n.id == concept_id:
            continue
        seen_slugs.add(n.id)
        rows.append((n.id, n.name, ""))

    if not rows:
        return ""

    lines: list[str] = [
        "| Related Concept | Relation | Notes |",
        "|----------------|----------|-------|",
    ]
    for slug, display, rel_type in rows:
        rel_label = rel_type if rel_type else "related"
        lines.append(f"| [[{slug}]] | {rel_label} | |")

    return "\n".join(lines)


def _wikipedia_reduce_prompt(
    topic: str,
    definition: str,
    evidence_block: str,
    persona: str,
    relationships_table: str,
) -> tuple[str, str]:
    """Build the system and user messages for Wikipedia-format article generation.

    Args:
        topic: Concept display name (article title).
        definition: One-line concept definition from ConceptRecord.
        evidence_block: Pre-formatted EVIDENCE block from _build_evidence_block().
        persona: Domain persona text (used as system prefix).
        relationships_table: Pre-built Markdown relationships table, or "".

    Returns:
        (system_message, user_message) tuple ready for complete().
    """
    system_msg = (
        f"{persona}\n\n"
        "You are writing Wikipedia-style articles for a personal knowledge base. "
        "Follow the domain persona above strictly.\n"
        "Voice and style rules:\n"
        "- Write in the voice described by the persona.\n"
        "- Use [[wikilinks]] for every related concept on first mention.\n"
        "- One concept per sentence -- never stack two unfamiliar terms.\n"
        "- No em-dashes as parenthetical separators.\n"
        "- No meta-commentary ('this article covers...').\n"
        "- Do not invent claims not present in the evidence."
    )

    rel_section = relationships_table if relationships_table else "_No relations recorded yet._"

    user_msg = (
        f"Write a Wikipedia-style article for the concept: **{topic}**\n\n"
        f"Seed definition (from discovery phase): {definition or '(none provided)'}\n\n"
        "Use the following evidence extracted from the corpus:\n\n"
        "--- EVIDENCE ---\n"
        f"{evidence_block}\n"
        "--- END EVIDENCE ---\n\n"
        "Produce the article using exactly these sections in order:\n\n"
        "## Definition\n"
        "1-2 sentences.  State what the concept IS.  No citations here.\n\n"
        "## Mechanism / Process\n"
        "How it works, how it is applied, or how it manifests.  "
        "Use inline citations [REF:display_name] immediately after each claim.\n\n"
        "## Key Facts\n"
        "Bulleted list of established, corpus-supported facts.  "
        "Each bullet: one fact + one citation.\n\n"
        "## In This Corpus\n"
        "What the user's specific corpus emphasises about this concept.  "
        "Which sources discuss it most.  Any corpus-specific usage or framing.\n\n"
        "## Relationships\n"
        f"{rel_section}\n\n"
        "(Extend the table above if the evidence reveals additional relationships "
        "not already listed.)\n\n"
        "## Open Questions\n"
        "What remains unresolved or unanswered in the corpus.  No citations -- "
        "this section documents the absence of evidence.\n\n"
        "Rules:\n"
        "- Inline citations immediately after the claim they support.\n"
        "- No em-dashes.\n"
        "- No meta-commentary.\n"
        "- Do not invent claims not in the evidence."
    )

    return system_msg, user_msg


# ── Public API ────────────────────────────────────────────────────────────────


def should_write_full(concept: ConceptRecord, extractions: list[SourceExtraction]) -> bool:
    """Return True when the concept warrants a full article (not a stub).

    Heuristic:
    - At least 3 relevant extractions from the corpus, AND
    - concept.importance > 0.3 (set by concept graph in Pass 2).

    Args:
        concept: The ConceptRecord being evaluated.
        extractions: Output from map_chunks_to_topic() for this concept.

    Returns:
        True -> write full article; False -> write stub.
    """
    relevant_count = sum(1 for e in extractions if e.is_relevant)
    return relevant_count >= 3 and concept.importance > 0.3


def write_concept_article(
    concept: ConceptRecord,
    neighbors: list[ConceptRecord],
    domain: str,
    model: str | None = None,
    extractions: list[SourceExtraction] | None = None,
) -> str:
    """Write a Wikipedia-format article body for a ConceptRecord.

    Steps:
    1. Retrieve or generate domain persona.
    2. Map corpus chunks to the concept topic (fast tier map phase).
    3. Build a Relationships table from graph neighbors + ConceptRelation rows.
    4. Call the LLM with a Wikipedia-format reduce prompt.
    5. Record SourceCoverage for all relevant extractions.

    Args:
        concept: The ConceptRecord to write an article for.
        neighbors: Graph neighbors (ConceptRecord list) from concept_graph.
        domain: Domain name (e.g. "material_science").
        model: litellm model string for the reduce phase.
                Defaults to settings.llm_model.

    Returns:
        Article body markdown (no frontmatter -- builder.py adds that).
    """
    reduce_model = model or settings.llm_model

    # Step 1: persona
    persona = get_or_create_persona(domain, model=reduce_model)

    # Step 2: map phase (always fast tier)
    extractions = extractions or map_chunks_to_topic(
        topic_query=concept.name,
        scope=concept.definition or concept.name,
        domain=domain,
        model=FAST_MODEL,
    )

    relevant = [e for e in extractions if e.is_relevant]
    evidence_block = _build_evidence_block(relevant)
    if not evidence_block.strip():
        evidence_block = "(No relevant evidence extracted from corpus.)"

    # Step 3: relationships table
    relationships_table = _build_relationships_table(concept.id, neighbors)

    # Step 4: Wikipedia-format reduce via direct complete() call
    system_msg, user_msg = _wikipedia_reduce_prompt(
        topic=concept.name,
        definition=concept.definition,
        evidence_block=evidence_block,
        persona=persona,
        relationships_table=relationships_table,
    )

    article_body = complete(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        model=reduce_model,
        temperature=0.3,
        max_tokens=2500,
        use_cache=False,
    )

    # Step 5: record coverage
    record_coverage(concept.id, domain, extractions)

    logger.info(
        "write_concept_article(%r): %d relevant sources, %d chars, neighbors=%d",
        concept.name,
        len(relevant),
        len(article_body),
        len(neighbors),
    )
    return article_body


def upgrade_concept_article(
    concept: ConceptRecord,
    article_path: Path,
    new_extractions: list[SourceExtraction],
    domain: str,
    model: str | None = None,
) -> str:
    """Update an existing stub or draft article with new evidence.

    Reads the existing article, checks each new extraction for contradictions
    (cheap embedding-based check), then delegates to either revisionary_update
    (contradictions found) or additive_update (no contradictions).

    Args:
        concept: The ConceptRecord whose article is being upgraded.
        article_path: Path to the existing .md article file.
        new_extractions: list[SourceExtraction] from a fresh map phase.
        domain: Domain name (for persona lookup).
        model: litellm model string.  Defaults to settings.llm_model.

    Returns:
        Updated article body markdown (no frontmatter).
    """
    upgrade_model = model or settings.llm_model
    persona = get_or_create_persona(domain, model=upgrade_model)

    existing_body = article_path.read_text(encoding="utf-8", errors="replace")

    relevant = [e for e in new_extractions if e.is_relevant]

    contradicting: list[SourceExtraction] = []
    for ext in relevant:
        if detect_contradiction(existing_body, ext.extraction):
            contradicting.append(ext)

    if contradicting:
        logger.info(
            "upgrade_concept_article(%r): %d contradiction(s) -> revisionary_update",
            concept.name,
            len(contradicting),
        )
        updated_body = revisionary_update(
            article_path=article_path,
            new_extractions=contradicting,
            persona=persona,
            model=upgrade_model,
        )
    else:
        logger.info(
            "upgrade_concept_article(%r): %d new extractions -> additive_update",
            concept.name,
            len(relevant),
        )
        updated_body = additive_update(
            article_path=article_path,
            new_extractions=relevant,
            persona=persona,
            model=upgrade_model,
        )

    return updated_body
