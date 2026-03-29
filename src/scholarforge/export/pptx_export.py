"""Export slide plans to PowerPoint presentations."""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt
from rich.console import Console

console = Console()


def export_slides(slides: list[dict], output_path: Path, title: str = "") -> Path:
    """Export a list of slide dicts to a PPTX file.

    Each slide dict has: title, bullets, notes, source_papers (optional).
    """
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    for i, slide_data in enumerate(slides):
        if i == 0:
            _add_title_slide(prs, slide_data)
        else:
            _add_content_slide(prs, slide_data)

    prs.save(str(output_path))
    console.print(f"[green]Slides saved:[/green] {output_path}")
    return output_path


def _add_title_slide(prs: Presentation, data: dict) -> None:
    """Add a title slide."""
    layout = prs.slide_layouts[0]  # Title Slide layout
    slide = prs.slides.add_slide(layout)

    slide.shapes.title.text = data.get("title", "Presentation")
    if slide.placeholders[1]:
        subtitle = "\n".join(data.get("bullets", []))
        slide.placeholders[1].text = subtitle

    if data.get("notes"):
        slide.notes_slide.notes_text_frame.text = data["notes"]


def _add_content_slide(prs: Presentation, data: dict) -> None:
    """Add a content slide with title and bullets."""
    layout = prs.slide_layouts[1]  # Title and Content layout
    slide = prs.slides.add_slide(layout)

    slide.shapes.title.text = data.get("title", "")

    # Body placeholder
    body = slide.placeholders[1]
    tf = body.text_frame
    tf.clear()

    bullets = data.get("bullets", [])
    for j, bullet in enumerate(bullets):
        if j == 0:
            tf.text = bullet
            tf.paragraphs[0].font.size = Pt(18)
        else:
            p = tf.add_paragraph()
            p.text = bullet
            p.font.size = Pt(18)
            p.space_before = Pt(6)

    # Speaker notes with source papers
    notes_parts = []
    if data.get("notes"):
        notes_parts.append(data["notes"])
    if data.get("source_papers"):
        notes_parts.append("Sources: " + ", ".join(data["source_papers"]))
    if notes_parts:
        slide.notes_slide.notes_text_frame.text = "\n".join(notes_parts)
