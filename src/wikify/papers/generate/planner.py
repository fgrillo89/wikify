"""Plan document structure from a prompt and retrieved literature."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wikify.llm.client import complete_json, complete_structured, schema_to_prompt
from wikify.llm.schemas import PaperPlanOutput
from wikify.papers.retrieve.context import RetrievedContext
from wikify.store.models import PaperPlan, SectionPlan

if TYPE_CHECKING:
    from wikify.papers.export.journal_profile import JournalProfile


def _plan_output_to_paper_plan(output: PaperPlanOutput) -> PaperPlan:
    """Convert a validated PaperPlanOutput to the existing PaperPlan model."""

    def _convert_sections(sections):
        result = []
        for s in sections:
            result.append(
                SectionPlan(
                    heading=s.heading,
                    level=s.level,
                    description=s.description,
                    target_tokens=s.target_tokens,
                    source_papers=s.source_papers,
                    subsections=_convert_sections(s.subsections),
                )
            )
        return result

    return PaperPlan(
        title=output.title,
        paper_type=output.paper_type,
        target_length=output.target_length,
        sections=_convert_sections(output.sections),
    )


def plan_paper(
    prompt: str,
    context: RetrievedContext,
    target_pages: int = 10,
    journal_profile: JournalProfile | None = None,
    artifact_type_id: str = "lit_review",
) -> PaperPlan:
    """Generate a structured paper plan from a prompt and literature context.

    Returns a PaperPlan with sections, each mapped to source papers.
    """
    from wikify.papers.generate.artifact_types import get_artifact_type

    # ~250 words per page
    target_words = target_pages * 250

    paper_list = context.paper_summaries()

    # Artifact type determines default structure
    artifact = get_artifact_type(artifact_type_id)
    section_guidance = (
        f"Document type: {artifact.name}. "
        f"Required sections: {', '.join(artifact.sections)}. "
        "Add 3-5 thematic body sections between Introduction and Conclusion."
    )

    # Journal overrides sections if specified
    if journal_profile and journal_profile.required_sections:
        sections_list = ", ".join(journal_profile.required_sections)
        section_guidance = (
            f"Document type: {artifact.name}. "
            f"Required sections for {journal_profile.name}: {sections_list}."
        )
        if journal_profile.word_limit:
            target_words = min(target_words, journal_profile.word_limit)

    # Type-specific instructions for the planner
    type_hint = ""
    if artifact_type_id == "lit_review":
        type_hint = (
            "This is a LITERATURE REVIEW. Body sections must be organized by "
            "themes/concepts, NOT by individual papers. Group related findings "
            "across multiple papers in each section."
        )

    # Schema instructions for structured output
    schema_instructions = schema_to_prompt(PaperPlanOutput)

    system_msg = (
        f"You are an academic writing assistant. Given a writing prompt and a list of "
        f"source papers, create a detailed outline for a {artifact.name}.\n\n"
        f"{schema_instructions}\n\n"
        f"{section_guidance}\n"
        f"{type_hint}\n"
        "Distribute the target word count across sections proportionally."
    )

    # Include graph metrics if available
    graph_section = ""
    if context.graph_metrics:
        id_to_name = {p.id: p.display_name() for p in context.papers}
        graph_section = "\n\n" + context.graph_metrics.summary_for_llm(id_to_name)

    user_msg = f"Prompt: {prompt}\n\nAvailable papers:\n{paper_list}{graph_section}"

    plan_output = complete_structured(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        response_model=PaperPlanOutput,
        temperature=0.3,
        max_tokens=4096,
    )

    return _plan_output_to_paper_plan(plan_output)


def plan_slides(
    prompt: str,
    context: RetrievedContext,
    num_slides: int = 10,
) -> list[dict]:
    """Generate a slide deck plan.

    Returns a list of slide dicts: {"title": ..., "bullets": [...], "notes": ...}
    """
    paper_list = context.paper_summaries()

    system_msg = (
        "You are a presentation designer. Given a topic and source papers, "
        f"create a {num_slides}-slide presentation outline.\n\n"
        "Return a JSON array of slide objects:\n"
        '[{"title": "Slide Title", "bullets": ["point 1", "point 2", ...], '
        '"notes": "speaker notes", "source_papers": ["Author Year"]}]\n\n'
        "Include: title slide, outline, 6-7 content slides, conclusion/future work.\n"
        "Each slide should have 3-5 bullet points.\n"
        "Return ONLY valid JSON, no markdown fences."
    )

    user_msg = f"Topic: {prompt}\n\nSource papers:\n{paper_list}"

    return complete_json(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=4096,
    )
