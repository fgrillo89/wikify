"""Export generated markdown to DOCX with journal-profile styling."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from rich.console import Console

from scholarforge.export.journal_profile import JournalProfile
from scholarforge.store.models import Paper

console = Console()

# Regex to split a text segment into bold/italic/citation tokens and plain text.
# Processed in this order: **bold**, *italic*, [N] superscript citation.
_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|\[\d+\])")


def _parse_inline(
    text: str, *, superscript_citations: bool = True
) -> list[tuple[str, bool, bool, bool]]:
    """Return list of (text, bold, italic, superscript) tuples for a line.

    When *superscript_citations* is False, [N] markers are kept as plain text
    (used in the bibliography section where numbers should not be superscripted).
    """
    parts: list[tuple[str, bool, bool, bool]] = []
    for segment in _INLINE_RE.split(text):
        if not segment:
            continue
        if segment.startswith("**") and segment.endswith("**"):
            parts.append((segment[2:-2], True, False, False))
        elif segment.startswith("*") and segment.endswith("*"):
            parts.append((segment[1:-1], False, True, False))
        elif re.fullmatch(r"\[\d+\]", segment):
            if superscript_citations:
                parts.append((segment[1:-1], False, False, True))  # strip brackets
            else:
                parts.append((segment, False, False, False))  # keep as-is
        else:
            parts.append((segment, False, False, False))
    return parts


def _set_run_font(run, font_family: str, font_size_pt: int | None = None) -> None:
    """Apply font family (and optional size) to a run."""
    run.font.name = font_family
    # Also set the complex-script font so it applies consistently
    r_pr = run._r.get_or_add_rPr()
    r_fonts = r_pr.get_or_add_rFonts()
    r_fonts.set(qn("w:ascii"), font_family)
    r_fonts.set(qn("w:hAnsi"), font_family)
    r_fonts.set(qn("w:cs"), font_family)
    if font_size_pt is not None:
        run.font.size = Pt(font_size_pt)


def _set_superscript(run) -> None:
    """Mark a run as superscript."""
    r_pr = run._r.get_or_add_rPr()
    vert_align = OxmlElement("w:vertAlign")
    vert_align.set(qn("w:val"), "superscript")
    r_pr.append(vert_align)


def _apply_paragraph_spacing(paragraph, line_spacing: float) -> None:
    """Apply line spacing multiplier to a paragraph."""
    paragraph.paragraph_format.line_spacing = line_spacing


class DocxExporter:
    """Export a numbered-markdown string to a styled DOCX file."""

    def __init__(self, journal_profile: JournalProfile) -> None:
        self.profile = journal_profile

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        numbered_markdown: str,
        ordered_papers: list[Paper],  # noqa: ARG002 — reserved for future use
        output_path: Path,
    ) -> Path:
        """Parse *numbered_markdown* and write a styled DOCX to *output_path*.

        Returns the resolved output path.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        doc = Document()
        self._configure_document(doc)
        self._parse_markdown(doc, numbered_markdown)

        doc.save(str(output_path))
        console.print(f"[green]DOCX exported:[/green] {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Document configuration
    # ------------------------------------------------------------------

    def _configure_document(self, doc: Document) -> None:
        """Set page margins and default body-text style."""
        for section in doc.sections:
            section.left_margin = Inches(1)
            section.right_margin = Inches(1)
            section.top_margin = Inches(1)
            section.bottom_margin = Inches(1)

        # Patch the Normal/Body Text style defaults
        normal = doc.styles["Normal"]
        normal.font.name = self.profile.font_family
        normal.font.size = Pt(self.profile.font_size_pt)
        normal.paragraph_format.line_spacing = self.profile.line_spacing

        # Patch heading styles' fonts
        for level in range(1, 4):
            style_name = f"Heading {level}"
            try:
                heading_style = doc.styles[style_name]
                heading_style.font.name = self.profile.font_family
            except KeyError:
                pass

    # ------------------------------------------------------------------
    # Markdown parser
    # ------------------------------------------------------------------

    def _parse_markdown(self, doc: Document, markdown: str) -> None:
        """Convert markdown line-by-line to Word paragraphs."""
        lines = markdown.splitlines()
        in_references = False

        for line in lines:
            stripped = line.strip()

            if not stripped:
                continue  # skip blank lines

            if stripped.startswith("### "):
                self._add_heading(doc, stripped[4:], level=2)
            elif stripped.startswith("## "):
                heading_text = stripped[3:]
                self._add_heading(doc, heading_text, level=1)
                # Detect the References section
                in_references = heading_text.strip().lower() == "references"
            elif stripped.startswith("# "):
                self._add_title(doc, stripped[2:])
                in_references = False
            else:
                self._add_body_paragraph(doc, stripped, superscript_citations=not in_references)

    # ------------------------------------------------------------------
    # Element builders
    # ------------------------------------------------------------------

    def _add_title(self, doc: Document, text: str) -> None:
        """Add a centred Title heading (level 0)."""
        para = doc.add_heading(level=0)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._fill_runs(para, text)

    def _add_heading(self, doc: Document, text: str, level: int) -> None:
        """Add a heading at *level* (1 or 2)."""
        para = doc.add_heading(level=level)
        self._fill_runs(para, text)

    def _add_body_paragraph(
        self,
        doc: Document,
        text: str,
        *,
        superscript_citations: bool = True,
    ) -> None:
        """Add a body-text paragraph with inline formatting."""
        para = doc.add_paragraph(style="Normal")
        _apply_paragraph_spacing(para, self.profile.line_spacing)
        self._fill_runs(para, text, superscript_citations=superscript_citations)

    def _fill_runs(self, para, text: str, *, superscript_citations: bool = True) -> None:
        """Populate *para* with runs derived from inline markdown in *text*."""
        tokens = _parse_inline(text, superscript_citations=superscript_citations)
        for content, bold, italic, superscript in tokens:
            run = para.add_run(content)
            run.bold = bold
            run.italic = italic
            _set_run_font(run, self.profile.font_family, self.profile.font_size_pt)
            if superscript:
                _set_superscript(run)
