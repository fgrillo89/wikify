"""High-level workflows for paper generation and export."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from wikify.papers.agent.core import AgentResult, ScholarForgeAgent
from wikify.papers.agent.defaults import (
    build_generation_prompt,
    get_default_hooks,
    get_default_tools,
)
from wikify.papers.agent.run_context import (
    RunContext,
    add_run_warning,
    create_run_context,
    record_phase_usage,
    use_run_context,
)
from wikify.papers.agent.writer_input import build_writer_input

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from wikify.papers.agent.research_notes import ResearchNotes


def _record_agent_phase_usage(
    phase: str,
    result,
    run_context: RunContext,
) -> None:
    """Capture phase telemetry defensively for real and stubbed agent results."""
    turn_telemetry = getattr(result, "turn_telemetry", []) or []
    record_phase_usage(
        phase,
        duration_s=sum(getattr(t, "latency_ms", 0.0) for t in turn_telemetry) / 1000,
        tokens_in=getattr(result, "total_input_tokens", 0),
        tokens_out=getattr(result, "total_output_tokens", 0),
        metadata={
            "turns": getattr(result, "total_turns", 0),
            "tool_calls": len(getattr(result, "tool_calls", []) or []),
        },
        run_context=run_context,
    )


def explore_corpus(
    prompt: str,
    model: str | None = None,
    token_budget: int = 130_000,
    max_turns: int = 25,
    run_context: RunContext | None = None,
) -> "ResearchNotes":
    """Run the explorer agent to build structured research notes.

    The explorer reads the corpus, identifies gaps, and produces a
    ResearchNotes object that can feed into any output format
    (review, slides, abstract, Q&A).

    Returns ResearchNotes built from recorded paper summaries.
    """
    from wikify.papers.agent.concept_graph import reset_concept_graph
    from wikify.papers.agent.defaults import build_explorer_prompt, get_explorer_tools
    from wikify.papers.agent.reading_log import reset_reading_log
    from wikify.papers.agent.research_notes import ResearchNotes
    from wikify.papers.agent.tools import reset_paper_summaries

    context = run_context or create_run_context(topic=prompt, strategy="explore_corpus")
    with use_run_context(context):
        reset_reading_log()
        reset_paper_summaries()
        reset_concept_graph()

        system_prompt = build_explorer_prompt(prompt)
        hooks = get_default_hooks(token_budget)

        agent = ScholarForgeAgent(
            model=model,
            tools=get_explorer_tools(),
            hooks=hooks,
            system_prompt=system_prompt,
            run_context=context,
        )

        result = agent.run(prompt, max_turns=max_turns)
        _record_agent_phase_usage("explore", result, context)

        # Build notes from recorded summaries (the explorer called record_paper_summary)
        notes = ResearchNotes.from_session(topic=prompt, run_context=context)

    # Try to extract gap analysis and outline from the explorer's final message
    if result.content:
        content = result.content
        if "gap" in content.lower() or "Gap" in content:
            notes.gap_analysis = content
        # The explorer's final message often contains the outline
        if "outline" in content.lower() or "section" in content.lower():
            # Extract lines that look like outline items
            import re

            outline_lines = re.findall(r"^\d+\.\s+.+$", content, re.MULTILINE)
            if outline_lines:
                notes.proposed_outline = outline_lines

    return notes


def generate_paper(
    prompt: str,
    model: str | None = None,
    artifact_type_id: str = "lit_review",
    journal: str = "",
    token_budget: int = 200_000,
    max_turns: int = 30,
    two_agent: bool = False,
    run_context: RunContext | None = None,
) -> tuple[str, AgentResult, list]:
    """Generate a paper using the agent loop.

    Args:
        two_agent: If True, runs explorer then writer as separate agents.
            The writer receives structured ResearchNotes (~5KB) instead of
            raw tool results (~280KB). Default False (single agent with
            tool compaction).

    Returns (markdown_text, agent_result, hooks).
    The hooks list contains a CostTracker at index 0 with accumulated cost data.
    """
    if two_agent:
        return _generate_two_agent(
            prompt, model, artifact_type_id, journal, token_budget, max_turns, run_context
        )

    # Single-agent mode (with tool compaction)
    system_prompt = build_generation_prompt(
        artifact_type_id=artifact_type_id,
        journal=journal,
        field_hint=prompt,
    )

    hooks = get_default_hooks(token_budget)

    context = run_context or create_run_context(topic=prompt, strategy="generate_paper")
    agent = ScholarForgeAgent(
        model=model,
        tools=get_default_tools(),
        hooks=hooks,
        system_prompt=system_prompt,
        run_context=context,
    )

    result = agent.run(prompt, max_turns=max_turns)
    _record_agent_phase_usage("draft_single_agent", result, context)
    return result.content, result, hooks


def _generate_two_agent(
    prompt: str,
    model: str | None = None,
    artifact_type_id: str = "lit_review",
    journal: str = "",
    token_budget: int = 200_000,
    max_turns: int = 30,
    run_context: RunContext | None = None,
) -> tuple[str, AgentResult, list]:
    """Two-agent generation: explorer builds notes, writer produces prose."""
    from wikify.papers.agent.defaults import build_writer_prompt, get_writer_tools

    context = run_context or create_run_context(topic=prompt, strategy="generate_paper_two_agent")

    # Phase 1: Explore
    explorer_budget = int(token_budget * 0.65)
    notes = explore_corpus(
        prompt,
        model=model,
        token_budget=explorer_budget,
        run_context=context,
    )

    # Phase 2: Write
    writer_budget = token_budget - explorer_budget
    writer_system = build_writer_prompt(
        artifact_type_id=artifact_type_id,
        journal=journal,
        field_hint=prompt,
    )
    writer_hooks = get_default_hooks(writer_budget)

    writer = ScholarForgeAgent(
        model=model,
        tools=get_writer_tools(),
        hooks=writer_hooks,
        system_prompt=writer_system,
        run_context=context,
    )

    writer_prompt = build_writer_input(
        notes,
        artifact_type_id=artifact_type_id,
        additional_instructions=[
            "Use the proposed outline unless the notes clearly justify a better structure.",
            "Keep the draft grounded in the supplied notes before reaching for tools.",
        ],
    )
    writer_max = max(5, max_turns - 25)  # writer needs fewer turns
    result = writer.run(writer_prompt, max_turns=writer_max)
    _record_agent_phase_usage("draft_writer", result, context)

    return result.content, result, writer_hooks


def export_paper(
    markdown: str,
    output_path: str = "data/output/review.md",
    journal: str = "",
    docx: bool = True,
    pdf: bool = True,
) -> list[Path]:
    """Export a generated paper to various formats.

    Resolves [REF:...] citation markers to numbered references [N],
    builds a bibliography, applies chemistry formatting, and exports.

    Returns list of output file paths.
    """
    from wikify.papers.export.chemistry import format_formulas_unicode
    from wikify.papers.export.journal_profile import load_journal_profile

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    outputs = []

    profile = load_journal_profile(journal)

    # Resolve [REF:...] markers to numbered citations + bibliography
    resolved_md = _resolve_references(markdown, profile)

    # Post-processing: strip em-dashes used as parenthetical separators
    resolved_md = _strip_emdashes(resolved_md)

    # Markdown (with Unicode subscripts)
    md_text = format_formulas_unicode(resolved_md)
    output.write_text(md_text, encoding="utf-8")
    outputs.append(output)

    # DOCX (gets raw text — DOCX exporter handles subscripts natively)
    if docx:
        from wikify.papers.export.docx_export import DocxExporter

        exporter = DocxExporter(profile)
        docx_path = output.with_suffix(".docx")
        exporter.export(resolved_md, [], docx_path)
        outputs.append(docx_path)

    # PDF (prefer DOCX->PDF via LibreOffice/Word for native subscripts)
    if pdf:
        pdf_path = output.with_suffix(".pdf")
        docx_source = output.with_suffix(".docx")
        pdf_generated = False

        if docx and docx_source.exists():
            pdf_generated = _docx_to_pdf(docx_source, pdf_path)

        if not pdf_generated:
            if docx and docx_source.exists():
                logger.warning(
                    "DOCX to PDF conversion failed for %s; falling back to HTML->PDF export",
                    docx_source,
                )
                add_run_warning(
                    f"PDF export fell back to HTML for {docx_source.name}",
                )
            # Fallback to HTML->PDF (subscripts may render as rectangles)
            from wikify.papers.export.pdf_export import PdfExporter

            PdfExporter(profile).export(resolved_md, [], pdf_path)

        outputs.append(pdf_path)

    return outputs


def _docx_to_pdf(docx_path: Path, pdf_path: Path) -> bool:
    """Convert DOCX to PDF using LibreOffice or Word. Returns True if successful."""
    import shutil
    import subprocess

    # Try LibreOffice first (cross-platform)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(pdf_path.parent),
                    str(docx_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            # LibreOffice names the output after the input file
            lo_output = pdf_path.parent / docx_path.with_suffix(".pdf").name
            if lo_output.exists() and lo_output != pdf_path:
                lo_output.rename(pdf_path)
            return pdf_path.exists()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LibreOffice PDF conversion failed for %s: %s", docx_path, exc)

    # Try Microsoft Word via COM automation (Windows only)
    # Attempt win32com first (pywin32), then comtypes as fallback
    for _com_backend in ("win32com", "comtypes"):
        try:
            if _com_backend == "win32com":
                import win32com.client  # type: ignore[import-untyped]

                word = win32com.client.Dispatch("Word.Application")
            else:
                import comtypes.client  # type: ignore[import-untyped]

                word = comtypes.client.CreateObject("Word.Application")

            word.Visible = False
            doc = word.Documents.Open(str(docx_path.resolve()))
            doc.SaveAs(str(pdf_path.resolve()), FileFormat=17)  # 17 = wdFormatPDF
            doc.Close()
            word.Quit()
            if pdf_path.exists():
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Word PDF conversion via %s failed for %s: %s",
                _com_backend,
                docx_path,
                exc,
            )

    return False


def _strip_emdashes(md: str) -> str:
    """Replace em-dash parentheticals with comma-delimited equivalents.

    Catches: ' -- ', ' --- ', unicode em-dash, unicode en-dash used as separators.
    """
    import re

    # Paired em-dashes: "word -- aside -- word" -> "word, aside, word"
    md = re.sub(r"\s*\u2014\s*([^.!?\n\u2014]+?)\s*\u2014\s*", r", \1, ", md)
    md = re.sub(r"\s*---?\s*([^.!?\n-]+?)\s*---?\s*", r", \1, ", md)
    # Remaining single em-dashes (unpaired): " -- " -> ", "
    md = re.sub(r"\s*\u2014\s*", ", ", md)
    md = re.sub(r"\s+---?\s+", ", ", md)
    # Clean up double commas from edge cases
    md = re.sub(r",\s*,", ",", md)
    return md


def _strip_references_section(md: str) -> str:
    """Remove any existing ## References section so we can append a clean one."""
    import re

    # Match ## References (or ### References) and everything after it until the next
    # same-or-higher-level heading or end of document
    return re.sub(r"\n##\s+References\s*\n[\s\S]*?(?=\n##\s[^#]|\Z)", "", md).rstrip()


def _resolve_references(markdown: str, profile) -> str:
    """Resolve [REF:...] markers to numbered citations and append bibliography."""
    from sqlmodel import select

    from wikify.papers.generate.references import ReferenceResolver
    from wikify.store.db import get_session
    from wikify.store.models import Paper

    with get_session() as session:
        papers = session.exec(select(Paper)).all()

    if not papers:
        return markdown

    resolver = ReferenceResolver(papers)
    numbered_md, ordered_papers = resolver.resolve(markdown)

    if ordered_papers:
        # Strip any LLM-written References section before appending the real one
        numbered_md = _strip_references_section(numbered_md)
        ref_fmt = profile.reference_format if profile else ""
        bibliography = resolver.build_bibliography(ordered_papers, reference_format=ref_fmt)
        return f"{numbered_md}\n\n## References\n\n{bibliography}"

    return numbered_md
