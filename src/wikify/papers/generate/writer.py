"""Section-by-section document generation using LLM."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import Progress

from wikify.papers.generate.persona import build_persona
from wikify.papers.generate.references import ReferenceResolver
from wikify.papers.generate.verifier import verify_paper, verify_section_against_plan
from wikify.llm.client import LLMOutputError, complete, validate_and_retry_text
from wikify.llm.schemas import SectionOutput
from wikify.core.retrieve.context import RetrievedContext
from wikify.store.models import PaperPlan, SectionPlan

if TYPE_CHECKING:
    from wikify.papers.export.journal_profile import JournalProfile
    from wikify.core.retrieve.context import SectionContext
    from wikify.store.models import Paper

console = Console()

# Stopwords for key-term extraction in context compaction.
_COMPACT_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "not",
        "no",
        "as",
        "if",
        "so",
        "than",
        "too",
        "very",
        "also",
        "such",
        "each",
        "which",
        "who",
        "whom",
        "what",
        "where",
        "when",
        "how",
        "all",
        "any",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "their",
        "them",
        "they",
        "we",
        "our",
        "us",
        "he",
        "she",
        "his",
        "her",
        "you",
        "your",
        "i",
        "me",
        "my",
    }
)


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
    # Pre-task context sufficiency check (P1)
    warnings = _check_context_sufficiency(plan, context)
    if warnings:
        console.print("[bold yellow]Context sufficiency warnings:[/bold yellow]")
        for w in warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")

    persona = build_persona(context=context, journal_profile=journal_profile)
    lit_context = context.as_text()
    paper_map = {p.id: p for p in context.papers}
    sections_written: list[str] = []
    source_paper_names = [p.display_name() for p in context.papers]

    all_sections = plan.flat_sections()

    with Progress() as progress:
        task = progress.add_task("Writing sections...", total=len(all_sections))

        for section in all_sections:
            prefix = "#" * section.level

            # Context compaction (P1): compacted summary + full preceding section
            prior_text = _compact_prior_sections(sections_written)

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
                paper_map=paper_map,
            )

            # Plan verification loop (P0): check compliance, retry once if issues
            issues = verify_section_against_plan(section_md, section, source_paper_names)
            if issues:
                feedback = "Issues with your output:\n" + "\n".join(
                    f"- {issue}" for issue in issues
                )
                console.print(
                    f"[yellow]Section '{section.heading}' "
                    "has plan compliance issues, retrying...[/yellow]"
                )
                section_md = _write_section(
                    section=section,
                    plan=plan,
                    lit_context=lit_context,
                    prior_sections=prior_text,
                    persona=persona,
                    journal_profile=journal_profile,
                    section_context=sec_ctx,
                    plan_feedback=feedback,
                    paper_map=paper_map,
                )

            sections_written.append(f"{prefix} {section.heading}\n\n{section_md}")
            progress.advance(task)

    # Assemble full document
    title_block = f"# {plan.title}\n\n"
    raw_markdown = title_block + "\n\n".join(sections_written)

    # Independent verification pass (P0)
    verification = verify_paper(raw_markdown, plan)
    if verification.passed:
        console.print("[green]Paper verification passed.[/green]")
    else:
        console.print("[bold yellow]Paper verification issues:[/bold yellow]")
        for issue in verification.issues:
            console.print(f"  [yellow]⚠ {issue}[/yellow]")
    console.print(
        f"[dim]  Words: {verification.total_words} | "
        f"Sections: {verification.sections_found}/{verification.sections_planned}[/dim]"
    )

    if not resolve_references:
        return raw_markdown

    # Resolve [REF:...] markers to numbered citations + build bibliography
    resolver = ReferenceResolver(context.papers)
    numbered_md, ordered_papers = resolver.resolve(raw_markdown)

    # Strip any LLM-written References section before appending the real one
    import re

    numbered_md = re.sub(
        r"\n##\s+References\s*\n[\s\S]*?(?=\n##\s[^#]|\Z)", "", numbered_md
    ).rstrip()

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
    plan_feedback: str = "",
    paper_map: dict[str, Paper] | None = None,
) -> str:
    """Generate a single section.

    If *section_context* is provided (from a per-section strategy), its
    content is used instead of the global ``lit_context``.

    If *plan_feedback* is non-empty, it is appended to the user message
    to guide the LLM toward plan compliance on a retry.
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
        effective_context = section_context.as_text(paper_map=paper_map)[:8000]
    else:
        effective_context = lit_context[:8000]

    feedback_block = (
        f"\n\n--- Plan compliance feedback ---\n{plan_feedback}" if plan_feedback else ""
    )

    user_msg = (
        f"Paper title: {plan.title}\n"
        f"Section: {section.heading}\n"
        f"Section description: {section.description}\n"
        f"{source_hint}\n\n"
        f"--- Previously written sections (for coherence) ---\n{prior_sections}\n\n"
        f"--- Literature context ---\n{effective_context}"
        f"{feedback_block}"
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


# ── Context compaction (P1) ─────────────────────────────────────────────────


def _compact_prior_sections(sections_written: list[str]) -> str:
    """Generate a running summary of all sections written so far.

    For each section: extract the heading, first sentence (topic sentence),
    citation markers found, and key technical terms.
    ~50 words per section keeps context manageable regardless of paper length.

    Returns a formatted string combining the compacted summary of all prior
    sections with the full text of the immediately preceding section.
    """
    if not sections_written:
        return ""

    summaries: list[str] = []
    for section_text in sections_written[:-1]:  # All but last (last gets full text)
        heading, topic, citations, terms = _extract_section_summary(section_text)
        parts = [f"**{heading}**" if heading else "**Section**"]
        if topic:
            parts.append(f"  Topic: {topic}")
        if citations:
            parts.append(f"  Citations: {', '.join(citations[:8])}")
        if terms:
            parts.append(f"  Key terms: {', '.join(sorted(terms)[:8])}")
        summaries.append("\n".join(parts))

    result_parts: list[str] = []
    if summaries:
        result_parts.append("--- Paper so far (compacted) ---\n" + "\n\n".join(summaries))

    # Always include the immediately preceding section in full
    if sections_written:
        result_parts.append("--- Immediately preceding section (full) ---\n" + sections_written[-1])

    return "\n\n".join(result_parts)


def _extract_section_summary(
    section_text: str,
) -> tuple[str, str, list[str], set[str]]:
    """Extract heading, topic sentence, citations, and key terms from a section.

    Returns (heading, first_sentence, citation_markers, key_terms).
    """
    lines = section_text.strip().splitlines()

    # Extract heading (first markdown heading line)
    heading = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^#{1,4}\s+", stripped):
            heading = re.sub(r"^#{1,4}\s+", "", stripped).strip()
            body_start = i + 1
            break

    body = "\n".join(lines[body_start:]).strip()

    # First sentence (topic sentence)
    first_sentence = ""
    sentences = re.split(r"(?<=[.!?])\s+", body)
    if sentences:
        first_sentence = sentences[0].strip()
        # Cap at ~60 words
        words = first_sentence.split()
        if len(words) > 60:
            first_sentence = " ".join(words[:60]) + "..."

    # Citation markers: both [REF:...] and [N] forms
    ref_markers = re.findall(r"\[REF:[^\]]+\]", body)
    num_markers = re.findall(r"\[\d+(?:[,\-\s]*\d+)*\]", body)
    citations = ref_markers + num_markers

    # Key terms: words appearing 2+ times, excluding stopwords
    words_raw = re.findall(r"[a-zA-Z]{3,}", body.lower())
    counts = Counter(words_raw)
    key_terms = {w for w, c in counts.items() if c >= 2 and w not in _COMPACT_STOPWORDS}

    return heading, first_sentence, citations, key_terms


# ── Context sufficiency check (P1) ──────────────────────────────────────────


def _check_context_sufficiency(
    plan: PaperPlan,
    context: RetrievedContext,
) -> list[str]:
    """Check if we have enough context to write the paper.

    Returns a list of warnings (empty = sufficient). Purely deterministic.
    """
    warnings: list[str] = []

    # Check total context tokens
    if context.total_tokens < 1000:
        warnings.append(
            f"Total context is only {context.total_tokens} tokens (< 1000). "
            "Output may be generic due to insufficient source material."
        )

    # Check minimum paper count
    if len(context.papers) < 3:
        warnings.append(
            f"Only {len(context.papers)} paper(s) in context (< 3). "
            "A review paper typically needs more sources."
        )

    # Check each section with assigned source_papers has at least 1 paper in context
    paper_names = {p.display_name().lower() for p in context.papers}
    paper_ids = {p.id for p in context.papers}
    for section in plan.flat_sections():
        if not section.source_papers:
            continue
        found = False
        for sp in section.source_papers:
            sp_lower = sp.lower()
            # Check by display name (fuzzy: substring match)
            if any(sp_lower in pn or pn in sp_lower for pn in paper_names):
                found = True
                break
            # Check by ID
            if sp in paper_ids:
                found = True
                break
        if not found:
            warnings.append(
                f"Section '{section.heading}' references source papers "
                f"not found in context: {', '.join(section.source_papers[:3])}"
            )

    return warnings
