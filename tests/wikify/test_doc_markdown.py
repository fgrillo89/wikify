"""Tests for store.doc_markdown.write_doc_markdown."""

from pathlib import Path

from wikify.api import Corpus
from wikify.corpus.doc_markdown import write_doc_markdown
from wikify.models import Document


def test_doc_markdown_has_frontmatter_and_edges(tmp_path: Path) -> None:
    corpus = Corpus(root=tmp_path / "corpus")
    corpus.ensure()
    doc = Document(
        id="paper_abc",
        source_path="/tmp/paper_abc.pdf",
        kind="pdf",
        title="A Great Paper",
        metadata={
            "authors": ["Alice A", "Bob B"],
            "year": 2020,
            "doi": "10.1/x",
            "venue": "J. Appl. Phys.",
        },
        markdown_path="",
        image_dir="",
        similar_to=["paper_xyz"],
        cites=["paper_cited"],
        cites_same=["paper_coupled"],
    )
    path = write_doc_markdown(corpus, doc, "# Body\n\nSome content.\n")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "title:" in text
    assert "Alice A" in text
    assert "year: 2020" in text
    assert "venue: J. Appl. Phys." in text
    assert "[[papers/paper_cited]]" in text
    assert "[[papers/paper_xyz]]" in text
    assert "[[papers/paper_coupled]]" in text
    assert "## Edges" in text
    assert "#edge/citation" in text
    assert "#edge/similarity" in text
    assert "#edge/coupling" in text
    assert "# Body" in text
