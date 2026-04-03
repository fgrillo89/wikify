"""Domain persona generation and caching for wiki article authoring.

A domain persona is a 150-200 word expert perspective statement generated
once from a sample of corpus sources. It is prepended to all reduce-phase
LLM prompts so that article tone and register stay consistent across the
entire wiki domain.
"""

from __future__ import annotations

import json
import logging

from wikify.llm.client import complete
from wikify.store.db import get_session
from wikify.store.models import DomainPersona, Paper, PaperTopic

logger = logging.getLogger(__name__)

_PERSONA_PROMPT_TEMPLATE = """\
You are about to write wiki articles for a personal knowledge base on the domain: {domain}.

Here is a sample of sources in this knowledge base:
{source_sample}

Define the expert perspective from which all articles should be written.
Your response must address:
1. REGISTER: What technical vocabulary and level of precision is appropriate?
2. CLAIMS: What distinguishes a strong claim from an opinion or speculation in this field?
3. UNCERTAINTY: How is uncertainty qualified? (e.g., "not yet reproduced", "context-dependent",
   "practitioner consensus but no RCTs")
4. DEBATES: What are the active disputes in this field that should appear in "Contested" sections?
5. READER: Who reads this wiki -- researcher, engineer, practitioner, designer?
   What do they most need from each article?

Write 150-200 words in second person ("You are a senior..."). Be specific to this domain,
not generic.\
"""


def generate_domain_persona(domain: str, model: str | None = None) -> str:
    """Generate and store a domain persona for the given domain.

    Queries the Paper table for up to 20 sources in this domain (via PaperTopic),
    calls the LLM once, stores the result in DomainPersona table (upsert), and
    returns the persona text.

    Args:
        domain: Domain name, e.g. "material_science" or "machine_learning".
        model: litellm model string. Defaults to settings.llm_model.

    Returns:
        The generated persona text (150-200 words).
    """
    from sqlmodel import select

    with get_session() as session:
        # Find papers whose topics contain the domain keyword
        all_topics = session.exec(select(PaperTopic)).all()
        domain_lower = domain.lower()
        matching_paper_ids: list[str] = [
            t.paper_id for t in all_topics if domain_lower in t.topic.lower()
        ]

        if matching_paper_ids:
            # Load matched papers (up to 20)
            papers: list[Paper] = []
            seen: set[str] = set()
            for pid in matching_paper_ids:
                if pid in seen:
                    continue
                seen.add(pid)
                p = session.get(Paper, pid)
                if p is not None:
                    papers.append(p)
                if len(papers) >= 20:
                    break
        else:
            # Fall back to all papers, limited to 20
            papers = session.exec(select(Paper).limit(20)).all()

        source_sample_items = [f"{p.title} ({p.doc_type})" for p in papers]

    source_sample_str = "\n".join(f"- {item}" for item in source_sample_items)
    if not source_sample_str:
        source_sample_str = "(no sources found in corpus)"

    prompt = _PERSONA_PROMPT_TEMPLATE.format(
        domain=domain,
        source_sample=source_sample_str,
    )

    persona_text = complete(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.3,
        max_tokens=400,
    )

    # Upsert: delete existing row then insert new one
    resolved_model = model or ""
    with get_session() as session:
        existing = session.get(DomainPersona, domain)
        if existing is not None:
            session.delete(existing)
            session.commit()

        row = DomainPersona(
            domain=domain,
            persona_text=persona_text,
            source_sample=json.dumps(source_sample_items),
            model=resolved_model,
        )
        session.add(row)
        session.commit()

    logger.info("Generated domain persona for %r (%d chars)", domain, len(persona_text))
    return persona_text


def get_or_create_persona(domain: str, model: str | None = None) -> str:
    """Return the stored persona for domain, generating it if absent.

    Args:
        domain: Domain name.
        model: litellm model string used only if generation is needed.

    Returns:
        Persona text.
    """
    with get_session() as session:
        row = session.get(DomainPersona, domain)
        if row is not None:
            return row.persona_text

    return generate_domain_persona(domain, model=model)


def invalidate_persona(domain: str) -> None:
    """Delete the stored persona for domain so it is regenerated on next use.

    Args:
        domain: Domain name whose persona should be invalidated.
    """
    with get_session() as session:
        row = session.get(DomainPersona, domain)
        if row is not None:
            session.delete(row)
            session.commit()
            logger.info("Invalidated domain persona for %r", domain)
        else:
            logger.debug("invalidate_persona: no row found for domain %r", domain)
