"""End-to-end test for similar_to population during ingest."""

from pathlib import Path

from wikify.corpus.chunks import list_documents
from wikify.corpus.vectors import VectorStore
from wikify.ingest.pipeline import ingest_corpus
from wikify.models import Chunk, Document


def test_similar_to_populated_on_overlapping_docs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    shared_body = (
        "atomic layer deposition self-limiting half reaction precursor "
        "pulse purge saturation conformal thin film growth "
    ) * 20
    (src / "a.md").write_text(f"---\ntitle: Paper A\n---\n\n{shared_body}\n", encoding="utf-8")
    (src / "b.md").write_text(f"---\ntitle: Paper B\n---\n\n{shared_body}\n", encoding="utf-8")
    (src / "c.md").write_text(
        "---\ntitle: Paper C\n---\n\nTotally unrelated content about "
        "medieval poetry and renaissance literature and baroque music.\n" * 5,
        encoding="utf-8",
    )
    corpus = ingest_corpus(src, tmp_path / "corpus")
    docs = list_documents(corpus)
    by_id = {d.id: d for d in docs}
    # A and B are near-duplicates so they should appear in each other's
    # similar_to list.
    a = next(d for d in docs if d.title == "Paper A")
    b = next(d for d in docs if d.title == "Paper B")
    assert b.id in a.similar_to
    assert a.id in b.similar_to
    # Every doc got the new fields (may be empty).
    for doc in docs:
        assert isinstance(doc.similar_to, list)
        assert isinstance(doc.cites, list)
        assert isinstance(doc.cites_same, list)
        # Per-doc markdown was rewritten with the enriched template.
        md = (corpus.markdown_dir / f"{doc.id}.md").read_text(encoding="utf-8")
        assert md.startswith("---")
        assert "## Edges" in md
    # sanity: by_id was built
    assert set(by_id.keys()) == {d.id for d in docs}


def test_doc_similarity_clears_stale_edges_for_docs_without_vectors() -> None:
    import numpy as np

    from wikify.ingest.pipeline import _compute_doc_similarity

    stale = Document(
        id="stale",
        source_path="stale.md",
        kind="md",
        title="Stale",
        metadata={},
        markdown_path="",
        image_dir="",
        similar_to=["old_neighbor"],
    )
    active = Document(
        id="active",
        source_path="active.md",
        kind="md",
        title="Active",
        metadata={},
        markdown_path="",
        image_dir="",
    )
    chunk = Chunk(
        id="active__c0000__aaaa",
        doc_id="active",
        ord=0,
        text="active text",
        char_span=(0, 11),
        section_path=["body"],
    )
    store = VectorStore(
        ids=[chunk.id],
        matrix=np.array([[1.0, 0.0]], dtype=np.float32),
    )

    _compute_doc_similarity(
        [stale, active],
        [("stale", []), ("active", [chunk])],
        store,
    )

    assert stale.similar_to == []
