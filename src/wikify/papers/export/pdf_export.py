"""Export generated papers to PDF using xhtml2pdf and Jinja2 templates."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa

from wikify.papers.export.journal_profile import JournalProfile
from wikify.core.store.models import Paper

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PdfExporter:
    """Convert numbered Markdown to a formatted academic PDF."""

    def __init__(self, journal_profile: JournalProfile) -> None:
        self._profile = journal_profile
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
        )

    def export(
        self,
        numbered_markdown: str,
        ordered_papers: list[Paper],
        output_path: Path,
    ) -> Path:
        """Render *numbered_markdown* to a PDF and write it to *output_path*.

        Parameters
        ----------
        numbered_markdown:
            Markdown source that may contain citation markers like ``[1]``.
        ordered_papers:
            Source papers in citation order (currently unused in rendering but
            available for future bibliography enrichment).
        output_path:
            Destination ``.pdf`` path (parent directories are created as needed).

        Returns
        -------
        Path
            The resolved path to the written PDF file.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        body_html = self._markdown_to_html(numbered_markdown)
        html_string = self._render_template(body_html)
        self._write_pdf(html_string, output_path)
        return output_path

    # ── Private helpers ───────────────────────────────────────────────────────

    def _markdown_to_html(self, md_text: str) -> str:
        """Convert Markdown to HTML, promote citations and fix subscripts."""
        # Convert Unicode subscripts to HTML <sub> before markdown processing
        # (xhtml2pdf can't render Unicode subscript chars like ₂ ₃)
        unicode_subs = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
        # Find sequences of Unicode subscript digits and wrap in <sub>
        md_text = re.sub(
            r"[₀₁₂₃₄₅₆₇₈₉]+",
            lambda m: "<sub>" + m.group().translate(unicode_subs) + "</sub>",
            md_text,
        )

        html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
        # Replace bare [N] citation markers with superscript elements.
        html = re.sub(r"\[(\d+)\]", r"<sup>[\1]</sup>", html)
        return html

    def _render_template(self, body_html: str) -> str:
        """Inject *body_html* and profile CSS values into the Jinja2 template."""
        p = self._profile
        template = self._env.get_template("paper.html.j2")
        return template.render(
            title=p.name,
            font_family=p.font_family,
            font_size_pt=p.font_size_pt,
            line_spacing=p.line_spacing,
            body_html=body_html,
        )

    @staticmethod
    def _write_pdf(html_string: str, output_path: Path) -> None:
        """Feed the rendered HTML to xhtml2pdf and write the result to disk."""
        buf = BytesIO()
        result = pisa.CreatePDF(html_string, dest=buf)
        if result.err:
            raise RuntimeError(f"xhtml2pdf reported {result.err} error(s) while generating PDF")
        output_path.write_bytes(buf.getvalue())
