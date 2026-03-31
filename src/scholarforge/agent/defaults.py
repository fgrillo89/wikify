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
        evaluate_coverage,
        find_corpus_gaps,
        find_jump_target,
        find_synthesis_opportunities,
        get_corpus_summary,
        get_coverage_gaps,
        get_frontier_exploration_order,
        get_graph_metrics,
        get_paper,
        get_paper_vibes,
        get_reading_log_text,
        get_sections,
        get_session_context,
        list_papers,
        list_topics,
        read_paper_digest,
        record_paper_summary,
        save_reading_log,
        scan_all_abstracts,
        search_papers,
        suggest_next_papers,
    )

    return [
        scan_all_abstracts,
        list_papers,
        search_papers,
        read_paper_digest,
        deep_read,
        record_paper_summary,
        get_session_context,
        get_paper,
        get_graph_metrics,
        get_paper_vibes,
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_frontier_exploration_order,
        suggest_next_papers,
        get_coverage_gaps,
        find_jump_target,
        get_sections,
        list_topics,
        get_corpus_summary,
        evaluate_coverage,
        get_reading_log_text,
        save_reading_log,
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
    persona = build_persona(
        journal_profile=journal_profile,
        artifact_type_id=artifact_type_id,
        user_prompt=field_hint,
    )

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


def get_explorer_tools() -> list[Callable]:
    """Tools for the explorer agent (reads corpus, builds research notes)."""
    from scholarforge.agent.tools import (
        deep_read,
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_corpus_summary,
        get_frontier_exploration_order,
        get_graph_metrics,
        get_paper_vibes,
        get_sections,
        get_session_context,
        list_papers,
        read_paper_digest,
        record_paper_summary,
        search_papers,
        suggest_next_papers,
    )

    return [
        get_frontier_exploration_order,
        deep_read,
        read_paper_digest,
        record_paper_summary,
        get_session_context,
        search_papers,
        get_sections,
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_graph_metrics,
        get_paper_vibes,
        suggest_next_papers,
        list_papers,
        get_corpus_summary,
    ]


def get_writer_tools() -> list[Callable]:
    """Limited tools for the writer agent (rarely needed, notes should suffice)."""
    from scholarforge.agent.tools import (
        read_paper_digest,
        search_papers,
    )

    return [read_paper_digest, search_papers]


def build_explorer_prompt(topic: str) -> str:
    """System prompt for the explorer agent."""
    return (
        "You are a research explorer. Your job is to read a corpus of academic "
        "papers and build structured research notes for a writer agent.\n\n"
        "## Workflow\n"
        "1. Call get_frontier_exploration_order() to get the optimal reading order\n"
        "2. Deep-read the seed papers. After EACH deep_read, immediately call "
        "record_paper_summary() to distill key findings.\n"
        "3. Digest frontier and bridge papers. Record summaries for each.\n"
        "4. Call find_corpus_gaps() and find_synthesis_opportunities()\n"
        "5. Do ONE search_papers call for the most promising gap\n"
        "6. When done, emit a final message with your research notes summary. "
        "Include: all paper summaries, gap analysis, synthesis opportunities, "
        "key contradictions found, and a proposed section outline.\n\n"
        "## Rules\n"
        "- After every deep_read or read_paper_digest, call record_paper_summary\n"
        "- Extract SPECIFIC numbers, measurements, and data points\n"
        "- Note contradictions between papers explicitly\n"
        "- Propose 5-7 thematic sections for the review\n\n"
        f"## Topic: {topic}\n"
    )


def build_writer_prompt(
    artifact_type_id: str = "lit_review",
    journal: str = "",
    field_hint: str = "",
) -> str:
    """System prompt for the writer agent (style guide + writing rules)."""
    from scholarforge.export.journal_profile import load_journal_profile
    from scholarforge.generate.persona import build_persona

    journal_profile = load_journal_profile(journal) if journal else None
    persona = build_persona(
        journal_profile=journal_profile,
        artifact_type_id=artifact_type_id,
        user_prompt=field_hint,
    )

    writer_instructions = (
        "\n\nYou are a review writer. You will receive structured research notes "
        "containing paper summaries, gap analysis, and a proposed outline. "
        "Transform these notes into a polished review.\n\n"
        "## Rules\n"
        "- Use [REF:DisplayName] citation markers matching the display_name values "
        "in the research notes\n"
        "- Name every gap from the gap analysis explicitly\n"
        "- State contradictions between papers\n"
        "- Include 3-5 figure placeholders with detailed captions\n"
        "- Target the Short tier (3000-4000 words) unless instructed otherwise\n"
        "- You CAN call read_paper_digest or search_papers if the notes are "
        "insufficient, but this should be rare (< 2 calls)\n"
    )

    return persona + writer_instructions
