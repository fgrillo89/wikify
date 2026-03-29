"""Build a field-specific writer persona from corpus topics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from scholarforge.store.db import get_session
from scholarforge.store.models import PaperTopic

if TYPE_CHECKING:
    from scholarforge.export.journal_profile import JournalProfile
    from scholarforge.retrieve.context import RetrievedContext


# Phrases that betray LLM-generated text — banned from output.
BANNED_PHRASES = [
    "it is worth noting",
    "it should be noted",
    "importantly,",
    "furthermore,",
    "moreover,",
    "notably,",
    "interestingly,",
    "in conclusion,",
    "in summary,",
    "delve into",
    "delves into",
    "in the realm of",
    "this paper aims to",
    "a comprehensive overview",
    "a comprehensive review",
    "plays a crucial role",
    "plays a pivotal role",
    "has garnered significant attention",
    "has attracted considerable interest",
    "paving the way",
    "it is important to note",
    "shed light on",
    "a burgeoning field",
    "paradigm shift",
]


def build_persona(
    context: RetrievedContext | None = None,
    journal_profile: JournalProfile | None = None,
) -> str:
    """Build a system prompt prefix that defines the writer's persona.

    Deterministic — no LLM call. Uses corpus topics from the PaperTopic table
    and journal profile to shape the writing voice.
    """
    # Get top topics from corpus
    topics = _get_top_topics(limit=5)

    # Build field description
    if topics:
        field_str = ", ".join(topics)
        field_line = f"You are a researcher specialising in {field_str}."
    else:
        field_line = "You are a researcher writing an academic paper."

    # Journal-specific register
    journal_line = ""
    if journal_profile and journal_profile.name != "Generic Academic":
        journal_line = (
            f"Write in the style and register of papers published in {journal_profile.name}."
        )

    # Anti-LLM-ism block
    banned_list = "; ".join(f'"{p}"' for p in BANNED_PHRASES[:12])
    style_block = (
        "Write like a human researcher, not a language model. "
        "Use direct, precise academic prose. "
        "Do not use em-dashes as parenthetical separators. "
        "Do not use bullet points or numbered lists in prose sections. "
        f"Never use these phrases: {banned_list}. "
        "Vary sentence length. Prefer active voice where natural. "
        "State findings and claims directly without hedging qualifiers."
    )

    parts = [field_line]
    if journal_line:
        parts.append(journal_line)
    parts.append(style_block)

    return "\n".join(parts)


def _get_top_topics(limit: int = 5) -> list[str]:
    """Get the most frequent topics from the PaperTopic table."""
    try:
        with get_session() as session:
            # Raw SQL for GROUP BY count — sqlmodel doesn't expose func easily
            from sqlalchemy import func

            results = session.exec(
                select(PaperTopic.topic, func.count(PaperTopic.paper_id).label("cnt"))
                .group_by(PaperTopic.topic)
                .order_by(func.count(PaperTopic.paper_id).desc())
                .limit(limit)
            ).all()
            return [row[0] for row in results]
    except Exception:
        return []
