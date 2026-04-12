"""Tests for compute_related_pages (feature 1).

Exercises:
- top-k by combined token-overlap + Jaccard score
- Jaccard computation edge cases (empty sets, identical sets)
- k cap
- self-exclusion
- 500-char body_excerpt cap
- see_also extraction
- related_pages field populated in WriteRequest
"""

from wikify.distill.write_prep import (
    _jaccard,
    _tokenise,
    compute_related_pages,
)
from wikify.models import Evidence, WikiPage


def _page(pid: str, title: str, aliases=None, body="", doc_ids=None) -> WikiPage:
    evidence = [
        Evidence(marker=f"e{i+1}", chunk_id=f"{d}__c0000__abc", doc_id=d, quote="q")
        for i, d in enumerate(doc_ids or [])
    ]
    return WikiPage(
        id=pid,
        kind="article",
        title=title,
        aliases=aliases or [],
        body_markdown=body,
        evidence=evidence,
    )


# --- _jaccard -----------------------------------------------------------


def test_jaccard_identical():
    a = frozenset({"foo", "bar"})
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint():
    assert _jaccard(frozenset({"foo"}), frozenset({"bar"})) == 0.0


def test_jaccard_empty():
    assert _jaccard(frozenset(), frozenset()) == 0.0


def test_jaccard_partial():
    a = frozenset({"foo", "bar"})
    b = frozenset({"bar", "baz"})
    # intersection=1, union=3
    assert abs(_jaccard(a, b) - 1 / 3) < 1e-9


# --- _tokenise ----------------------------------------------------------


def test_tokenise_drops_stopwords():
    tokens = _tokenise("the atomic layer deposition")
    assert "the" not in tokens
    assert "atomic" in tokens
    assert "layer" in tokens
    assert "deposition" in tokens


def test_tokenise_case_insensitive():
    assert _tokenise("ALD") == _tokenise("ald")


# --- compute_related_pages ----------------------------------------------


def test_self_excluded():
    page = _page("Foo", "Foo Bar", doc_ids=["doc1"])
    pages = [page]
    result = compute_related_pages(page, pages, k=5)
    assert result == []


def test_top_k_cap():
    target = _page("Target", "Resistive Switching", doc_ids=["d1", "d2"])
    others = [
        _page(f"p{i}", f"Resistive Switching variant {i}", doc_ids=["d1"])
        for i in range(10)
    ]
    all_pages = [target] + others
    result = compute_related_pages(target, all_pages, k=3)
    assert len(result) <= 3


def test_high_overlap_ranked_first():
    target = _page("T", "Hafnium Oxide Memory", doc_ids=["d1", "d2"])
    high = _page("H", "Hafnium Oxide", doc_ids=["d1", "d2"])
    medium = _page("M", "Hafnium based material", doc_ids=["d3"])
    result = compute_related_pages(target, [target, high, medium], k=5)
    assert result[0]["id"] == "H"
    # High overlaps both token and doc; medium only tokens.
    assert result[0]["topic_overlap"] > result[1]["topic_overlap"]


def test_body_excerpt_capped_at_500():
    long_body = "x" * 2000
    other = _page("O", "Memristor device", body=long_body, doc_ids=["d1"])
    target = _page("T", "Memristor", doc_ids=["d1"])
    result = compute_related_pages(target, [target, other], k=5)
    assert result
    assert len(result[0]["body_excerpt"]) <= 500


def test_see_also_extracted():
    body = "Some prose.\n\n## See also\n\n- Memristor\n- Resistive RAM\n\n## References\n\n[^e1]: x"
    other = _page("O", "Flash Memory device", body=body, doc_ids=["d1"])
    target = _page("T", "Flash Memory", doc_ids=["d1"])
    result = compute_related_pages(target, [target, other], k=5)
    assert result
    assert "Memristor" in result[0]["see_also"] or "Resistive RAM" in result[0]["see_also"]


def test_result_structure():
    other = _page("O", "Atomic Layer Deposition", doc_ids=["d1"])
    target = _page("T", "Atomic Deposition", doc_ids=["d1"])
    result = compute_related_pages(target, [target, other], k=5)
    assert result
    r = result[0]
    expected_keys = {"id", "title", "topic_overlap", "body_excerpt", "see_also", "evidence_doc_ids"}
    assert set(r.keys()) == expected_keys
    assert isinstance(r["topic_overlap"], float)
    assert 0.0 <= r["topic_overlap"] <= 1.0


def test_no_overlap_excluded():
    target = _page("T", "Quantum Computing", doc_ids=["dA"])
    other = _page("O", "Resistive Switching", doc_ids=["dB"])
    result = compute_related_pages(target, [target, other], k=5)
    # score=0 means it should not appear
    assert result == []


def test_related_pages_in_write_request(tmp_path):
    """build_write_request populates related_pages on the WriteRequest."""
    from wikify.distill.dossier import DossierStore
    from wikify.distill.write_prep import WriteRequestConfig, build_write_request
    from wikify.models import Evidence, WikiPage
    from wikify.store.images_index import ImageIndex

    page = WikiPage(
        id="Resistive Switching",
        kind="article",
        title="Resistive Switching",
        aliases=["RS"],
        body_markdown="",
        evidence=[
            Evidence(marker="e1", chunk_id="d1__c0__aa", doc_id="d1", quote="q")
        ],
    )
    neighbor = WikiPage(
        id="Memristor",
        kind="article",
        title="Memristor",
        aliases=[],
        body_markdown="Memristor body",
        evidence=[
            Evidence(marker="e1", chunk_id="d1__c0__bb", doc_id="d1", quote="q")
        ],
    )
    cfg = WriteRequestConfig(
        model_id="haiku",
        writer_tier="S",
        prompt_name="wikify/write",
        style_text="",
        field_text="",
        artifact_text="",
        person_artifact_text="",
        persona_text="",
    )
    dossier_store = DossierStore(tmp_path)
    req = build_write_request(
        page=page,
        all_pages=[page, neighbor],
        briefs={},
        dossier_store=dossier_store,
        chunks_by_id={},
        images_index=ImageIndex([]),
        cfg=cfg,
    )
    assert isinstance(req.related_pages, list)
    assert len(req.related_pages) >= 1
    assert req.related_pages[0]["id"] == "Memristor"
