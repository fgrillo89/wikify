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
    pdf: bool = False,
) -> list[Path]:
    """Export a generated paper to various formats.

    Returns list of output file paths.
    """
    from scholarforge.export.chemistry import format_formulas_unicode
    from scholarforge.export.journal_profile import load_journal_profile

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    outputs = []

    # Markdown (with Unicode subscripts)
    md_text = format_formulas_unicode(markdown)
    output.write_text(md_text, encoding="utf-8")
    outputs.append(output)

    # DOCX
    if docx:
        from scholarforge.export.docx_export import DocxExporter

        profile = load_journal_profile(journal)
        exporter = DocxExporter(profile)
        docx_path = output.with_suffix(".docx")
        exporter.export(markdown, [], docx_path)
        outputs.append(docx_path)

    # PDF
    if pdf:
        from scholarforge.export.pdf_export import PdfExporter

        profile = load_journal_profile(journal)
        pdf_path = output.with_suffix(".pdf")
        PdfExporter(profile).export(markdown, [], pdf_path)
        outputs.append(pdf_path)

    return outputs
