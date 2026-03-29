"""Section-by-section document generation using LLM."""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress

from scholarforge.llm.client import complete
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import PaperPlan, SectionPlan

console = Console()


def write_paper(plan: PaperPlan, context: RetrievedContext) -> str:
    """Generate a full paper from a plan and retrieved context.

    Writes section-by-section with running context to maintain coherence.
    Returns the complete markdown document.
    """
    lit_context = context.as_text()
    sections_written: list[str] = []

    all_sections = _flatten_sections(plan.sections)

    with Progress() as progress:
        task = progress.add_task("Writing sections...", total=len(all_sections))

        for section in all_sections:
            prefix = "#" * section.level
            prior_text = "\n\n".join(sections_written[-3:]) if sections_written else ""

            section_md = _write_section(
                section=section,
                plan=plan,
                lit_context=lit_context,
                prior_sections=prior_text,
            )

            sections_written.append(f"{prefix} {section.heading}\n\n{section_md}")
            progress.advance(task)

    # Assemble full document
    title_block = f"# {plan.title}\n\n"
    return title_block + "\n\n".join(sections_written)


def _write_section(
    section: SectionPlan,
    plan: PaperPlan,
    lit_context: str,
    prior_sections: str,
) -> str:
    """Generate a single section."""
    system_msg = (
        "You are an academic writer producing a review paper. "
        "Write the following section based on the literature provided. "
        "Use inline citations like (Author, Year). "
        "Be precise, technical, and thorough. "
        "Do NOT include the section heading — just the body text. "
        f"Target approximately {section.target_tokens} words."
    )

    source_hint = ""
    if section.source_papers:
        source_hint = f"\nKey sources for this section: {', '.join(section.source_papers)}"

    user_msg = (
        f"Paper title: {plan.title}\n"
        f"Section: {section.heading}\n"
        f"Section description: {section.description}\n"
        f"{source_hint}\n\n"
        f"--- Previously written sections (for coherence) ---\n{prior_sections}\n\n"
        f"--- Literature context ---\n{lit_context[:8000]}"
    )

    return complete(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=2048,
    )


def _flatten_sections(sections: list[SectionPlan]) -> list[SectionPlan]:
    """Flatten nested sections into a linear list for sequential writing."""
    result: list[SectionPlan] = []
    for s in sections:
        result.append(s)
        if s.subsections:
            result.extend(_flatten_sections(s.subsections))
    return result
