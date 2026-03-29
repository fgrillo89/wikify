"""Section-by-section document generation using LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import Progress

from scholarforge.generate.persona import build_persona
from scholarforge.generate.references import ReferenceResolver
from scholarforge.llm.client import LLMOutputError, complete, validate_and_retry_text
from scholarforge.llm.schemas import SectionOutput
from scholarforge.retrieve.context import RetrievedContext
from scholarforge.store.models import PaperPlan, SectionPlan

if TYPE_CHECKING:
    from scholarforge.export.journal_profile import JournalProfile
    from scholarforge.retrieve.context import SectionContext
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

    all_sections = plan.flat_sections()

    with Progress() as progress:
        task = progress.add_task("Writing sections...", total=len(all_sections))

        for section in all_sections:
            prefix = "#" * section.level
            prior_text = "\n\n".join(sections_written[-3:]) if sections_written else ""

            # Use per-section context if the strategy produced one
            sec_ctx = context.section_contexts.get(section.heading)

            section_md = _write_section(
                section=section,
                plan=plan,
                lit_context=lit_context,
                prior_sections=prior_text,
                persona=persona,
                journal_profile=journal_profile,
                section_context=sec_ctx,
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
    section_context: SectionContext | None = None,
) -> str:
    """Generate a single section.

    If *section_context* is provided (from a per-section strategy), its
    content is used instead of the global ``lit_context``.
    """

    persona_block = f"{persona}\n\n" if persona else ""

    # Sections that should NOT receive figure instructions
    no_figure_sections = {"abstract", "conclusion", "references"}
    include_figures = section.heading.strip().lower() not in no_figure_sections

    figure_instruction = ""
    if include_figures:
        figure_instruction = (
            "\n\nFIGURE PLACEHOLDERS: Insert 1–2 figure placeholders in this section "
            "where a figure would strengthen the argument. Use this exact two-line format "
            "for each figure:\n"
            "  ![Figure N: Short descriptive title](figure_N_placeholder.png)\n"
            "  **Figure N.** Full caption: (a) what the figure shows conceptually, "
            "(b) what data/comparison it contains (axis labels, data series, units), "
            "(c) key takeaway for the reader. State the figure type explicitly "
            "(e.g. schematic, bar chart, line plot, heatmap, TEM image, SEM image, "
            "AFM image, scatter plot). Make the caption detailed enough that a "
            "figure-generation agent can create the figure from the caption alone. "
            "Reference each figure in the preceding text (e.g. 'as shown in Figure N'). "
            "Use consecutive figure numbers continuing from any figures already "
            "in prior sections."
        )

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
        f"{figure_instruction}"
    )

    source_hint = ""
    if section.source_papers:
        source_hint = f"\nKey sources for this section: {', '.join(section.source_papers)}"

    # Prefer per-section context if available
    if section_context and (section_context.chunks or section_context.synthesis_notes):
        effective_context = section_context.as_text()[:8000]
    else:
        effective_context = lit_context[:8000]

    user_msg = (
        f"Paper title: {plan.title}\n"
        f"Section: {section.heading}\n"
        f"Section description: {section.description}\n"
        f"{source_hint}\n\n"
        f"--- Previously written sections (for coherence) ---\n{prior_sections}\n\n"
        f"--- Literature context ---\n{effective_context}"
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    # Abstract sections don't need citations
    is_abstract = section.heading.strip().lower() == "abstract"

    try:
        raw_text, _validated = validate_and_retry_text(
            messages=messages,
            response_model=SectionOutput,
            content_field="content",
            temperature=0.3,
            max_tokens=2048,
            max_retries=2,
            skip_citation_check=is_abstract,
        )
        return raw_text
    except LLMOutputError:
        # Fallback: return whatever we got (best effort)
        return complete(
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )
