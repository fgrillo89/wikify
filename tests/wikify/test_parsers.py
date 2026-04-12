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
