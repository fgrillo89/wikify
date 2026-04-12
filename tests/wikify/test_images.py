"""Image extraction + sidecar round-trip tests (slice 0c)."""

import json
from pathlib import Path

import pytest

from wikify.ingest.images import save_doc_images
from wikify.ingest.parsers.registry import parse_file
from wikify.models import Document
from wikify.store.corpus import read_doc_images

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.mark.parametrize(
    "name,kind",
    [
        ("sample.pdf", "pdf"),
        ("sample.docx", "docx"),
        ("sample.pptx", "pptx"),
    ],
)
def test_image_extraction_and_sidecar_round_trip(tmp_path, name, kind):
    path = FIXTURES / name
    assert path.exists()
    parsed_kind, result = parse_file(path)
    assert parsed_kind == kind

    assert result.raw_images, f"{name}: expected at least one raw image"

    doc_id = f"{path.stem}_test"
    image_dir = tmp_path / "corpus" / "images" / doc_id
    saved = save_doc_images(doc_id, image_dir, result.raw_images)
    assert saved, "save_doc_images returned nothing"

    # (a) binary exists at corpus/images/{doc_id}/...
    first = saved[0]
    bin_path = Path(first.path)
    assert bin_path.exists(), f"binary missing: {bin_path}"
    assert bin_path.parent == image_dir

    # (b) sidecar JSON exists with the expected keys
    side = bin_path.with_suffix(bin_path.suffix + ".json")
    assert side.exists(), f"sidecar missing: {side}"
    data = json.loads(side.read_text(encoding="utf-8"))
    for key in ("id", "caption", "alt_text", "page", "near_chunk_ids"):
        assert key in data, f"sidecar missing key {key}"

    # (c) read_doc_images round-trips via a Document handle
    doc = Document(
        id=doc_id,
        source_path=str(path),
        kind=kind,
        title=path.stem,
        metadata={},
        markdown_path="",
        image_dir=str(image_dir),
    )
    loaded = read_doc_images(doc)
    assert len(loaded) == len(saved)
    assert {im.id for im in loaded} == {im.id for im in saved}


def test_pdf_caption_matched():
    """The PDF fixture has a 'Figure 1.' caption under the image."""
    path = FIXTURES / "sample.pdf"
    _, result = parse_file(path)
    assert result.raw_images
    # At least one record should have picked up the Figure 1 caption.
    labels = [r.label for r in result.raw_images]
    assert any(lbl and "1" in lbl.lower() for lbl in labels), labels


def test_html_remote_image_url_sidecar(tmp_path):
    html_src = tmp_path / "page.html"
    html_src.write_text(
        """<!doctype html><html><head><title>t</title></head>
        <body><p>hello world this is plenty of text for trafilatura to
        consider extracting as the main body content of the page.</p>
        <img src="https://example.com/remote.png" alt="remote figure"></body></html>""",
        encoding="utf-8",
    )
    _, result = parse_file(html_src)
    assert result.raw_images and result.raw_images[0].url == "https://example.com/remote.png"

    doc_id = "html_test"
    image_dir = tmp_path / "corpus" / "images" / doc_id
    saved = save_doc_images(doc_id, image_dir, result.raw_images)
    assert saved
    # URL-only sidecar is fig_000.url.json.
    side = image_dir / "fig_000.url.json"
    assert side.exists()
    data = json.loads(side.read_text(encoding="utf-8"))
    assert data["source_url"] == "https://example.com/remote.png"
    assert data["alt_text"] == "remote figure"
