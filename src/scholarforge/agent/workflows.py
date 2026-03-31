"""High-level workflows for paper generation and export."""

from __future__ import annotations

from pathlib import Path

from scholarforge.agent.core import AgentResult, ScholarForgeAgent
from scholarforge.agent.defaults import (
    build_generation_prompt,
    get_default_hooks,
    get_default_tools,
)


def generate_paper(
    prompt: str,
    model: str | None = None,
    artifact_type_id: str = "lit_review",
    journal: str = "",
    token_budget: int = 200_000,
    max_turns: int = 30,
) -> tuple[str, AgentResult, list]:
    """Generate a paper using the agent loop.

    Returns (markdown_text, agent_result, hooks).
    The hooks list contains a CostTracker at index 0 with accumulated cost data.
    """
    system_prompt = build_generation_prompt(
        artifact_type_id=artifact_type_id,
        journal=journal,
        field_hint=prompt,
    )

    hooks = get_default_hooks(token_budget)

    agent = ScholarForgeAgent(
        model=model,
        tools=get_default_tools(),
        hooks=hooks,
        system_prompt=system_prompt,
    )

    result = agent.run(prompt, max_turns=max_turns)
    return result.content, result, hooks


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
    from scholarforge.export.chemistry import format_formulas_unicode
    from scholarforge.export.journal_profile import load_journal_profile

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
        from scholarforge.export.docx_export import DocxExporter

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
            # Fallback to HTML->PDF (subscripts may render as rectangles)
            from scholarforge.export.pdf_export import PdfExporter

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
        except Exception:  # noqa: BLE001
            pass

    # Try Microsoft Word via COM automation (Windows only)
    try:
        import comtypes.client  # type: ignore[import-untyped]

        word = comtypes.client.CreateObject("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(docx_path.resolve()))
        doc.SaveAs(str(pdf_path.resolve()), FileFormat=17)  # 17 = wdFormatPDF
        doc.Close()
        word.Quit()
        return pdf_path.exists()
    except Exception:  # noqa: BLE001
        pass

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

    from scholarforge.generate.references import ReferenceResolver
    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

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
