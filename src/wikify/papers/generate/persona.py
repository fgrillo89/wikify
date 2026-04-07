"""Build a field-specific writer persona from corpus topics."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.store.db import get_session
from wikify.store.models import PaperTopic

if TYPE_CHECKING:
    from wikify.papers.export.journal_profile import JournalProfile
    from wikify.core.retrieve.context import RetrievedContext


def _find_style_guide() -> Path | None:
    """Locate the style guide from multiple candidate paths."""
    candidates = [
        # Relative to package source (works with uv run / editable install)
        Path(__file__).parent.parent / "prompts" / "style_guide.md",
        # Relative to working directory
        Path.cwd() / "src" / "wikify" / "prompts" / "style_guide.md",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


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
    artifact_type_id: str = "lit_review",
    user_prompt: str = "",
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

    # Inject the combined writing guide: base style + artifact type rules
    style_guide = _load_style_guide()
    if style_guide:
        from wikify.papers.generate.artifact_types import get_artifact_type

        try:
            artifact = get_artifact_type(artifact_type_id)
            combined = artifact.full_instructions(style_guide)
        except ValueError:
            combined = style_guide
        parts.append(f"\n--- Writing Instructions ---\n{combined}")

    # Inject field-specific writing guide (auto-detected from corpus topics)
    from wikify.papers.generate.field_guide import get_field_instructions

    field_query = user_prompt or (context.query if context and hasattr(context, "query") else "")
    field_instructions = get_field_instructions(field_query, topics)
    if field_instructions:
        parts.append(field_instructions)

    return "\n".join(parts)


def _load_style_guide() -> str:
    """Load the academic writing style guide if it exists."""
    path = _find_style_guide()
    if path:
        return path.read_text(encoding="utf-8")
    return ""


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
