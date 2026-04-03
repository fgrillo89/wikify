"""Shared writer-input assembly for all note-driven generation routes."""

from __future__ import annotations

from wikify.agent.research_notes import ResearchNotes

DEFAULT_TOPIC = "research topic"


def normalize_topic(topic: str | None) -> str:
    """Return a safe topic label for prompts and metadata."""
    value = (topic or "").strip()
    return value or DEFAULT_TOPIC


def build_artifact_section_guidance(artifact_type_id: str, topic: str) -> str:
    """Build artifact-driven section guidance without domain-specific hardcoding."""
    from wikify.generate.artifact_types import get_artifact_type

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


def build_writer_input(
    notes: ResearchNotes,
    word_target: int = 4000,
    artifact_type_id: str = "lit_review",
    extra_sections: list[str] | None = None,
    additional_instructions: list[str] | None = None,
) -> str:
    """Build the canonical user prompt for note-driven writing routes."""
    from wikify.generate.artifact_types import get_artifact_type

    topic = normalize_topic(notes.topic)
    artifact = get_artifact_type(artifact_type_id)
    artifact_guidance = build_artifact_section_guidance(artifact_type_id, topic)

    cite_list = "\n".join(f"- [REF:{s.display_name}]" for s in notes.source_summaries)
    if not cite_list:
        cite_list = "- No sources supplied"

    sections = [
        notes.to_writer_prompt(),
        "## Available Citations (copy these EXACTLY)\n" + cite_list,
    ]

    for section in extra_sections or []:
        if section and section.strip():
            sections.append(section.strip())

    instructions = [
        f"Write a {word_target}-word {artifact.name.lower()} focused on: {topic}.",
        "CITATION FORMAT: Use [REF:DisplayName] where DisplayName is copied exactly "
        "from the list above.",
        artifact_guidance,
        "No em-dashes. One concept per sentence. Every claim needs a citation.",
    ]
    instructions.extend(additional_instructions or [])
    sections.append("## Instructions\n" + "\n".join(instructions))

    return "\n\n".join(sections)
