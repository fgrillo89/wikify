"""Parser smoke tests for slice 0b (pdf/docx/pptx/html)."""

from pathlib import Path

import pytest

from wikify.ingest.parsers.registry import parse_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.mark.parametrize(
    "name,min_sections",
    [
        ("sample.pdf", 1),
        ("sample.docx", 1),
        ("sample.pptx", 2),  # two slides, two sections
        ("sample.html", 1),
    ],
)
def test_parser_round_trip(name, min_sections):
    path = FIXTURES / name
    assert path.exists(), f"missing fixture: {path}"
    kind, result = parse_file(path)
    assert kind in ("pdf", "docx", "pptx", "html")
    assert result.markdown.strip(), f"empty markdown for {name}"
    assert len(result.sections) >= min_sections, (
        f"{name}: expected >= {min_sections} sections, got {len(result.sections)}"
    )
    assert isinstance(result.metadata, dict)
    # images list is a list of DocImage (possibly empty)
    assert isinstance(result.images, list)


def test_pdf_ocr_fallback_prefers_more_usable_text():
    from wikify.ingest.parsers.pdf import _better_markdown

    assert _better_markdown("x", "usable fallback text 123") == "usable fallback text 123"
    assert _better_markdown("usable OCR text 123", "") == "usable OCR text 123"
