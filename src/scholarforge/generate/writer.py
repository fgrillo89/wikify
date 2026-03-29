"""Section-by-section document generation using LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import Progress

from scholarforge.generate.persona import build_persona
from scholarforge.generate.references import ReferenceResolver
from scholarforge.llm.client import complete
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import PaperPlan, SectionPlan

if TYPE_CHECKING:
    from scholarforge.export.journal_profile import JournalProfile
    from scholarforge.store.models import Paper

console = Console()


def write_paper(
    plan: PaperPlan,
    context: RetrievedContext,
    journal_profile: JournalProfile | None = None,
    resolve_references: bool = True,
) -> str | tuple[str, list[Paper]]:
    """Generate a full paper from a plan and retrieved context.

    Writes section-by-section with running context to maintain coherence.

    If resolve_references is True (default), returns (numbered_markdown, ordered_papers).
    If False, returns raw markdown with [REF:...] markers (backward compat).
    """
    persona = build_persona(context=context, journal_profile=journal_profile)
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
                persona=persona,
                journal_profile=journal_profile,
            )

            sections_written.append(f"{prefix} {section.heading}\n\n{section_md}")
            progress.advance(task)

    # Assemble full document
    title_block = f"# {plan.title}\n\n"
    raw_markdown = title_block + "\n\n".join(sections_written)

    if not resolve_references:
        return raw_markdown

    # Resolve [REF:...] markers to numbered citations + build bibliography
    resolver = ReferenceResolver(context.papers)
    numbered_md, ordered_papers = resolver.resolve(raw_markdown)
    ref_fmt = journal_profile.reference_format if journal_profile else ""
    bibliography = resolver.build_bibliography(ordered_papers, reference_format=ref_fmt)
    full_document = f"{numbered_md}\n\n## References\n\n{bibliography}"

    return full_document, ordered_papers


def _write_section(
    section: SectionPlan,
    plan: PaperPlan,
    lit_context: str,
    prior_sections: str,
    persona: str = "",
    journal_profile: JournalProfile | None = None,
) -> str:
    """Generate a single section."""
    persona_block = f"{persona}\n\n" if persona else ""

    system_msg = (
        f"{persona_block}"
        "Write the following section of a review paper based on the literature provided. "
        "Cite sources using [REF:display_name] markers, where display_name matches "
        "the identifier shown in each paper's context header "
        "(e.g. [REF:Kim 2021 - 4K-memristor...]). "
        "Be precise, technical, and thorough. "
        "Do NOT include the section heading — just the body text. "
        f"Target approximately {section.target_tokens} words. "
        "After drafting, self-revise: check for banned words, nominalizations, "
        "passive voice overuse, vague quantifiers, and LLM structural tells "
        "(em-dashes, uniform hedging, rule-of-three). Fix any violations."
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
