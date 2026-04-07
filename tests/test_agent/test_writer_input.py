from __future__ import annotations

from wikify.papers.agent.fast_generate import build_one_shot_prompt
from wikify.papers.agent.research_notes import ResearchNotes, SourceSummary
from wikify.papers.agent.writer_input import build_writer_input, normalize_topic


def test_normalize_topic_falls_back_to_default():
    assert normalize_topic("  ") == "research topic"


def test_research_notes_from_precomputed_context_preserves_excerpts():
    notes = ResearchNotes.from_precomputed_context(
        topic="edge AI",
        papers=[
            {
                "display_name": "Kim 2021 - Edge AI",
                "role": "seed paper",
                "content": "Digest excerpt with device metrics.",
                "depth": "digest",
            }
        ],
        gap_analysis="Need endurance studies.",
        synthesis_opportunities="Pair materials and systems papers.",
    )

    assert notes.source_summaries[0].source_excerpt == "Digest excerpt with device metrics."
    prompt = notes.to_writer_prompt()
    assert "**Evidence Excerpt**:" in prompt
    assert "Need endurance studies." in prompt


def test_build_writer_input_adds_citations_and_extra_sections():
    notes = ResearchNotes(
        topic="edge AI",
        source_summaries=[
            SourceSummary(
                display_name="Kim 2021 - Edge AI",
                relevance="Seed paper",
                key_findings=["Improved linearity"],
            )
        ],
    )

    prompt = build_writer_input(
        notes,
        word_target=3200,
        artifact_type_id="lit_review",
        extra_sections=["## Concept Links\nA <-> B"],
        additional_instructions=["Include a short research agenda."],
    )

    assert "## Available Citations" in prompt
    assert "[REF:Kim 2021 - Edge AI]" in prompt
    assert "## Concept Links" in prompt
    assert "Write a 3200-word literature review" in prompt
    assert "Include a short research agenda." in prompt


def test_build_one_shot_prompt_uses_shared_writer_handoff():
    system_prompt, user_prompt = build_one_shot_prompt(
        {
            "topic": "edge AI",
            "papers": [
                {
                    "display_name": "Kim 2021 - Edge AI",
                    "role": "seed paper",
                    "content": "Digest excerpt with device metrics.",
                    "depth": "digest",
                }
            ],
            "gaps": "Need endurance studies.",
            "synthesis": "Bridge device and architecture evidence.",
            "concept_links": "## Concept Links\nA <-> B",
        },
        word_target=2800,
    )

    assert "review writer" in system_prompt.lower()
    assert "# Research Notes: edge AI" in user_prompt
    assert "Digest excerpt with device metrics." in user_prompt
    assert "## Available Citations" in user_prompt
    assert "## Concept Links" in user_prompt
    assert "Write a 2800-word literature review" in user_prompt
