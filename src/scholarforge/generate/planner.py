"""Plan document structure from a prompt and retrieved literature."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scholarforge.llm.client import complete_json
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import PaperPlan, SectionPlan

if TYPE_CHECKING:
    from scholarforge.export.journal_profile import JournalProfile


def plan_paper(
    prompt: str,
    context: RetrievedContext,
    target_pages: int = 10,
    journal_profile: JournalProfile | None = None,
) -> PaperPlan:
    """Generate a structured paper plan from a prompt and literature context.

    Returns a PaperPlan with sections, each mapped to source papers.
    """
    # ~250 words per page
    target_words = target_pages * 250

    paper_list = context.paper_summaries()

    # Journal-specific section requirements
    section_guidance = (
        "Include: Abstract, Introduction, main thematic sections (3-5), "
        "Discussion/Future Directions, Conclusion."
    )
    if journal_profile and journal_profile.required_sections:
        sections_list = ", ".join(journal_profile.required_sections)
        section_guidance = f"Required sections for {journal_profile.name}: {sections_list}."
        if journal_profile.word_limit:
            target_words = min(target_words, journal_profile.word_limit)

    system_msg = (
        "You are an academic writing assistant. Given a writing prompt and a list of "
        "source papers, create a detailed outline for a review/survey paper.\n\n"
        "Return a JSON object with this exact structure:\n"
        '{"title": "...", "paper_type": "lit_review", '
        f'"target_length": {target_words}, '
        '"sections": [{"heading": "...", "level": 1, "description": "what to cover", '
        '"target_tokens": N, "source_papers": ["Author Year - Title", ...], '
        '"subsections": [...]}]}\n\n'
        f"{section_guidance}\n"
        "Distribute the target word count across sections proportionally.\n"
        "Return ONLY valid JSON, no markdown fences."
    )

    # Include graph metrics if available
    graph_section = ""
    if context.graph_metrics:
        id_to_name = {p.id: p.display_name() for p in context.papers}
        graph_section = "\n\n" + context.graph_metrics.summary_for_llm(id_to_name)

    user_msg = f"Prompt: {prompt}\n\nAvailable papers:\n{paper_list}{graph_section}"

    plan_data = complete_json(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    # Build PaperPlan from response
    sections = []
    for s in plan_data.get("sections", []):
        section = SectionPlan(
            heading=s["heading"],
            level=s.get("level", 1),
            description=s.get("description", ""),
            target_tokens=s.get("target_tokens", 300),
            source_papers=s.get("source_papers", []),
            subsections=[
                SectionPlan(
                    heading=sub["heading"],
                    level=sub.get("level", 2),
                    description=sub.get("description", ""),
                    target_tokens=sub.get("target_tokens", 200),
                    source_papers=sub.get("source_papers", []),
                )
                for sub in s.get("subsections", [])
            ],
        )
        sections.append(section)

    return PaperPlan(
        title=plan_data.get("title", "Untitled Review"),
        paper_type=plan_data.get("paper_type", "lit_review"),
        target_length=plan_data.get("target_length", target_words),
        sections=sections,
    )


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
