"""Factory functions for common ScholarForge agent configurations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from scholarforge.llm.hooks import LLMHook


def get_default_tools() -> list[Callable]:
    """Return the standard set of KB tools for agent use."""
    from scholarforge.agent.tools import (
        deep_read,
        get_corpus_summary,
        get_graph_metrics,
        get_paper,
        get_sections,
        list_papers,
        list_topics,
        search_papers,
    )

    return [
        list_papers,
        search_papers,
        deep_read,
        get_paper,
        get_graph_metrics,
        get_sections,
        list_topics,
        get_corpus_summary,
    ]


def get_default_hooks(token_budget: int = 200_000) -> list[LLMHook]:
    """Return standard hooks: cost tracker + token budget + call logger."""
    from scholarforge.llm.hooks import CallLogger, CostTracker, TokenBudget

    return [CostTracker(), TokenBudget(token_budget), CallLogger()]


def build_generation_prompt(
    artifact_type_id: str = "lit_review",
    journal: str = "",
    field_hint: str = "",
) -> str:
    """Build the full system prompt for paper generation.

    Combines: base style guide + artifact type rules + field guide + journal constraints.
    """
    from scholarforge.export.journal_profile import load_journal_profile
    from scholarforge.generate.persona import build_persona

    journal_profile = load_journal_profile(journal) if journal else None
    persona = build_persona(journal_profile=journal_profile, artifact_type_id=artifact_type_id)

    # Add agent-specific instructions
    agent_instructions = (
        "\n\nYou have access to a knowledge base of academic papers via tools. "
        "Use them to explore the corpus before writing. Workflow:\n"
        "1. Call list_papers or get_corpus_summary to understand the corpus\n"
        "2. Call get_graph_metrics to identify hub papers\n"
        "3. Call deep_read on key papers to get full content\n"
        "4. Call search_papers for specific topics\n"
        "5. Plan the paper structure\n"
        "6. Write each section with [REF:AuthorName Year - Title] citation markers\n"
        "7. Write the full paper as markdown with # headings\n"
    )

    return persona + agent_instructions
