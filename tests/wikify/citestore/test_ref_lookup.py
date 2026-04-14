"""Tests for citestore.ref_lookup."""

from dataclasses import dataclass, field

from wikify.citestore.models import CitationEntry
from wikify.citestore.ref_lookup import RefLookup


@dataclass
class _FakeDoc:
    id: str = "doc1"
    title: str = "Test Paper"
    metadata: dict = field(default_factory=lambda: {"doi": "10.1234/test"})
    citations: list = field(default_factory=list)
    cites: list = field(default_factory=list)


@dataclass
class _FakeChunk:
    id: str = "c1"
    doc_id: str = "doc1"
    text: str = "some chunk text about memristors and switching"
    ord: int = 0


def _make_entry(ord: int, title: str = "", doi: str = "") -> CitationEntry:
    return CitationEntry(ord=ord, title=title, doi=doi, raw_text=f"ref {ord}")


def test_parse_markers_simple():
    lookup = RefLookup([_FakeDoc()])
    assert lookup.parse_markers("text [1] more [2,3]") == [1, 2, 3]


def test_parse_markers_range():
    lookup = RefLookup([_FakeDoc()])
    assert lookup.parse_markers("refs [5-9]") == [5, 6, 7, 8, 9]


def test_parse_markers_mixed():
    lookup = RefLookup([_FakeDoc()])
    assert lookup.parse_markers("[2-4,7]") == [2, 3, 4, 7]


def test_parse_markers_no_markers():
    lookup = RefLookup([_FakeDoc()])
    assert lookup.parse_markers("no citations here") == []


def test_resolve_markers():
    cit1 = _make_entry(1, title="First Paper")
    cit2 = _make_entry(2, title="Second Paper")
    doc = _FakeDoc(citations=[cit1, cit2])
    lookup = RefLookup([doc])

    refs = lookup.resolve_markers("see [1,2] for details", "doc1")
    assert len(refs) == 2
    assert refs[0].entry.title == "First Paper"
    assert refs[1].entry.title == "Second Paper"


def test_resolve_markers_in_corpus():
    """Cited work with matching DOI should be detected as in-corpus."""
    cited = _make_entry(1, title="Cited Paper", doi="10.9999/cited")
    source = _FakeDoc(
        id="source",
        citations=[cited],
        metadata={"doi": "10.1234/source"},
    )
    corpus_paper = _FakeDoc(
        id="cited_doc",
        title="Cited Paper",
        metadata={"doi": "10.9999/cited"},
    )
    lookup = RefLookup([source, corpus_paper])

    refs = lookup.resolve_markers("[1]", "source")
    assert len(refs) == 1
    assert refs[0].in_corpus is True
    assert refs[0].corpus_doc_id == "cited_doc"


def test_resolve_markers_not_in_corpus():
    cited = _make_entry(1, title="External Paper", doi="10.9999/external")
    doc = _FakeDoc(citations=[cited])
    lookup = RefLookup([doc])

    refs = lookup.resolve_markers("[1]", "doc1")
    assert len(refs) == 1
    assert refs[0].in_corpus is False


def test_find_corpus_chunks():
    chunks = [
        _FakeChunk(id="c1", doc_id="doc1", text="memristor switching behavior observed"),
        _FakeChunk(id="c2", doc_id="doc1", text="atomic layer deposition temperature"),
        _FakeChunk(id="c3", doc_id="doc1", text="results show switching characteristics"),
    ]
    lookup = RefLookup([_FakeDoc()], chunks=chunks)

    found = lookup.find_corpus_chunks("doc1", "switching behavior", top_k=2)
    assert len(found) <= 2
    # Chunk c1 should rank highest (has both "switching" and "behavior")
    assert any("switching" in ck.text for ck in found)
