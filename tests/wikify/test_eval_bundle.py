"""Tests for the tolerant evidence-block parser in store.wiki_bundle."""

from wikify.bundle.wiki.page import _extract_evidence, load_bundle


def _one(body: str):
    evs = _extract_evidence(body)
    assert len(evs) == 1, f"expected 1, got {evs}"
    return evs[0]


def test_canonical_format():
    body = '[^e1]: ABC123 (DOC456) > "hello world"'
    ev = _one(body)
    assert ev.marker == "e1"
    assert ev.chunk_id == "ABC123"
    assert ev.doc_id == "DOC456"
    assert ev.quote == "hello world"
    assert ev.locator == ""


def test_whitespace_tolerance():
    body = '[^e2]:   ABC123   (  DOC456  )  >   "hi"  '
    ev = _one(body)
    assert ev.marker == "e2"
    assert ev.chunk_id == "ABC123"
    assert ev.doc_id == "DOC456"
    assert ev.quote == "hi"


def test_curly_quotes():
    body = "[^e1]: ABC (DOC) > \u201chello\u201d"
    ev = _one(body)
    assert ev.quote == "hello"


def test_multi_line_quote():
    body = '[^e1]: ABC (DOC) > "hello\nworld"'
    ev = _one(body)
    assert ev.quote == "hello world"


def test_empty_evidence_block():
    body = "## Evidence\n\n"
    assert _extract_evidence(body) == []


def test_spaces_in_chunk_and_doc_ids():
    """Real writer output: chunk stems contain spaces and brackets."""
    chunk = "[2018 Yang] Paper Title_cb5e__c0069__abcd"
    doc = "[2018 Yang] Paper Title_cb5e"
    body = f'[^e1]: {chunk} ({doc}) > "ALD|Atomic Layer Deposition"'
    ev = _one(body)
    assert ev.chunk_id == chunk
    assert ev.doc_id == doc
    assert ev.quote == "ALD|Atomic Layer Deposition"


def test_locator_split_only_for_page_like_suffix():
    body = '[^e1]: CHUNK (Doc, with comma) > "q"'
    ev = _one(body)
    # "with comma" is not a page-like locator, so the whole thing stays as doc.
    assert ev.doc_id == "Doc, with comma"
    assert ev.locator == ""

    body2 = '[^e2]: CHUNK (Doc Title, p.3) > "q"'
    ev2 = _one(body2)
    assert ev2.doc_id == "Doc Title"
    assert ev2.locator == "p.3"


def test_multiple_markers_in_block():
    body = '[^e1]: C1 (D1) > "first quote"\n[^e2]: C2 (D2) > "second quote"\n'
    evs = _extract_evidence(body)
    assert len(evs) == 2
    assert evs[0].chunk_id == "C1" and evs[1].chunk_id == "C2"


def test_load_bundle_roundtrip(tmp_path):
    """Tiny bundle with overlapping doc ids loads and parses evidence."""
    concepts = tmp_path / "articles"
    concepts.mkdir(parents=True)
    for i, (pid, doc) in enumerate(
        [("concept-a", "DOC_X"), ("concept-b", "DOC_X"), ("concept-c", "DOC_Y")]
    ):
        (concepts / f"{pid}.md").write_text(
            f"---\nid: {pid}\nkind: article\ntitle: {pid}\n---\n\n"
            f"# {pid}\n\nBody text here [^e1].\n\n"
            f'## Evidence\n\n[^e1]: chunk_{i} ({doc}) > "some quote"\n',
            encoding="utf-8",
        )
    bundle = load_bundle(tmp_path)
    assert len(bundle.pages) == 3
    assert all(len(p.evidence) == 1 for p in bundle.pages)
    doc_ids = {p.evidence[0].doc_id for p in bundle.pages}
    assert doc_ids == {"DOC_X", "DOC_Y"}
