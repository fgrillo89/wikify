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

from scholarforge.export.chemistry import split_formula_runs
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


def _set_subscript(run) -> None:
    """Mark a run as subscript."""
    r_pr = run._r.get_or_add_rPr()
    vert_align = OxmlElement("w:vertAlign")
    vert_align.set(qn("w:val"), "subscript")
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

        If the journal profile has a ``template_docx`` set and the template
        file exists, the template is used as the base document (preserving
        its styles, headers, footers, and page setup).  Otherwise falls back
        to a blank document with programmatic styling.

        A user-supplied .docx file can also be used as a template by setting
        ``template_docx`` to its path.

        Returns the resolved output path.
        """
        from scholarforge.export.templates.registry import get_template_path

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        template = get_template_path(self.profile.template_docx)
        if template:
            doc = Document(str(template))
            self._clear_body(doc)
            console.print(f"[dim]Using template: {template.name}[/dim]")
        else:
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

    @staticmethod
    def _clear_body(doc: Document) -> None:
        """Remove all paragraphs from a template document, keeping styles/headers."""
        body = doc.element.body
        for child in list(body):
            if child.tag.endswith("}p") or child.tag.endswith("}tbl"):
                body.remove(child)

    def _style_name(self, role: str) -> str:
        """Resolve a ScholarForge role to a template style name."""
        return self.profile.style_map.get(role, role)

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
        """Add a centred Title heading."""
        title_style = self._style_name("title")
        try:
            para = doc.add_paragraph(style=title_style)
        except KeyError:
            para = doc.add_heading(level=0)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._fill_runs(para, text)

    def _add_heading(self, doc: Document, text: str, level: int) -> None:
        """Add a heading at *level* (1 or 2)."""
        style_key = f"heading{level}"
        style_name = self._style_name(style_key)
        try:
            para = doc.add_paragraph(style=style_name)
        except KeyError:
            para = doc.add_heading(level=level)
        self._fill_runs(para, text)

    def _add_body_paragraph(
        self,
        doc: Document,
        text: str,
        *,
        superscript_citations: bool = True,
        style_role: str = "body",
    ) -> None:
        """Add a body-text paragraph with inline formatting."""
        style_name = self._style_name(style_role)
        try:
            para = doc.add_paragraph(style=style_name)
        except KeyError:
            para = doc.add_paragraph(style="Normal")
        _apply_paragraph_spacing(para, self.profile.line_spacing)
        self._fill_runs(para, text, superscript_citations=superscript_citations)

    def _fill_runs(self, para, text: str, *, superscript_citations: bool = True) -> None:
        """Populate *para* with runs derived from inline markdown in *text*.

        Plain text segments are further split by chemical formula detection:
        digits in formulas like HfO2 are rendered as subscript runs.
        """
        tokens = _parse_inline(text, superscript_citations=superscript_citations)
        font = self.profile.font_family
        size = self.profile.font_size_pt
        for content, bold, italic, superscript in tokens:
            if superscript or bold or italic:
                # Styled runs are emitted as-is (no chemistry splitting)
                run = para.add_run(content)
                run.bold = bold
                run.italic = italic
                _set_run_font(run, font, size)
                if superscript:
                    _set_superscript(run)
            else:
                # Plain text: split words to detect chemical formulas
                self._fill_with_chemistry(para, content, font, size)

    def _fill_with_chemistry(self, para, text: str, font: str, size: int) -> None:
        """Emit runs for plain text, subscripting digits in chemical formulas."""
        # Split on word boundaries to check each word for formulas
        words = re.split(r"(\b\w+\b)", text)
        for word in words:
            formula_runs = split_formula_runs(word)
            if len(formula_runs) == 1 and not formula_runs[0][1]:
                # Not a formula — emit as single plain run
                run = para.add_run(word)
                _set_run_font(run, font, size)
            else:
                # Chemical formula — emit element/digit runs with subscripts
                for part, is_subscript in formula_runs:
                    run = para.add_run(part)
                    _set_run_font(run, font, size)
                    if is_subscript:
                        _set_subscript(run)
