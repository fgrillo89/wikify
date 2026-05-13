"""Assets and chunk_assets tests."""

from __future__ import annotations

from wikify.corpus.store import Store
from wikify.models import Chunk, Document


def _doc(doc_id="d1") -> Document:
    return Document(
        id=doc_id, source_path=f"/p/{doc_id}.pdf", kind="pdf",
        title="t", metadata={},
        markdown_path=f"m/{doc_id}.md", image_dir=f"i/{doc_id}/",
    )


def test_assets_upsert_and_get():
    s = Store(":memory:")
    s.upsert_document(_doc())
    s.upsert_assets("d1", [
        {"id": "d1/fig_01", "type": "figure", "page": 1, "path": "i/d1/fig_01.png",
         "caption": "Schematic"},
        {"id": "d1/eq_01", "type": "equation", "content": r"E = mc^2"},
    ])
    rows = s.get_assets("d1")
    assert {r["asset_id"] for r in rows} == {"d1/fig_01", "d1/eq_01"}
    eqn = next(r for r in rows if r["asset_type"] == "equation")
    assert eqn["content"] == r"E = mc^2"


def test_chunk_assets_create_edges():
    s = Store(":memory:")
    s.upsert_document(_doc())
    s.upsert_chunks([Chunk(id="d1/c0", doc_id="d1", ord=0, text="t",
                           char_span=(0, 1), section_path=[])])
    s.upsert_assets("d1", [{"id": "d1/fig_01", "type": "figure", "page": 1}])
    s.upsert_chunk_assets("d1", [
        {"chunk_id": "d1/c0", "asset_id": "d1/fig_01", "relation": "near", "confidence": 0.9},
    ])
    has_asset = [tuple(r) for r in s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges WHERE kind='has_asset'",
    )]
    assert ("d1", "d1/fig_01") in has_asset
    near = [tuple(r) for r in s.con.execute(
        "SELECT src_id, dst_id, kind FROM graph_edges WHERE src_type='chunk'",
    )]
    assert ("d1/c0", "d1/fig_01", "near") in near


def test_chunk_assets_drops_dangling_refs():
    """Mappings whose chunk_id or asset_id is missing from the
    corresponding tables must be filtered out, not raise FK errors.
    Resumes from crashed ingests can leave ``chunk.equation_ids``
    pointing at equations that were filtered during extraction; those
    stale refs should silently drop instead of aborting the whole
    refresh.
    """
    s = Store(":memory:")
    s.upsert_document(_doc())
    s.upsert_chunks([Chunk(id="d1/c0", doc_id="d1", ord=0, text="t",
                           char_span=(0, 1), section_path=[])])
    s.upsert_assets("d1", [{"id": "d1/fig_01", "type": "figure", "page": 1}])
    s.upsert_chunk_assets("d1", [
        {"chunk_id": "d1/c0", "asset_id": "d1/fig_01", "relation": "near"},
        {"chunk_id": "d1/MISSING", "asset_id": "d1/fig_01", "relation": "near"},
        {"chunk_id": "d1/c0", "asset_id": "d1/MISSING_EQ", "relation": "contains"},
    ])
    rows = [tuple(r) for r in s.con.execute(
        "SELECT chunk_id, asset_id, relation FROM chunk_assets",
    )]
    assert rows == [("d1/c0", "d1/fig_01", "near")]


# ---------------------------------------------------------------------------
# author_key hyphen-collapse (root-cause fix C4)
# ---------------------------------------------------------------------------


def test_author_key_collapses_hyphenated_romanization():
    """Romanized Chinese / Korean given names appear in two forms across
    publishers: ``Tianyu Wang`` and ``Tian-Yu Wang``. Both must hash to
    the same key so they merge into one author record instead of two.
    The bug: the old ``_NORM_RE`` substitution replaced hyphens with
    spaces, splitting ``Tian-Yu`` into two tokens; the no-hyphen form
    had one given-name token.
    """
    from wikify.corpus.store.authors import author_key

    assert author_key("Tianyu Wang") == author_key("Tian-Yu Wang")
    assert author_key("Jialin Meng") == author_key("Jia-Lin Meng")
    assert author_key("Qingqing Sun") == author_key("Qing-Qing Sun")
    # The non-breaking hyphen (U+2010) is also collapsed.
    assert author_key("Tianyu Wang") == author_key("Tian‐Yu Wang")


def test_author_key_distinct_authors_stay_distinct():
    """The hyphen-collapse must not over-merge across unrelated authors."""
    from wikify.corpus.store.authors import author_key

    assert author_key("Tianyu Wang") != author_key("Tianyu Zhao")
    assert author_key("Sungjun Kim") != author_key("Hyungjin Kim")


def test_author_key_matches_all_mirrors():
    """``author_key`` is defined in four places that index author rows
    independently: the SQLite authors store, the graph_build builder,
    the kg store helper, and the bundle/draft author_context summariser.
    All four must compute identical keys for the same input or rows
    inserted by one path won't join with rows looked up by another.
    """
    from wikify.bundle.draft.author_context import _author_key as ac_key
    from wikify.corpus.graph_build import _author_key as graph_key
    from wikify.corpus.store.authors import author_key as store_key
    from wikify.corpus.store.kg import author_key as kg_key

    cases = (
        "Tianyu Wang", "Tian-Yu Wang", "J. Joshua Yang", "van der Waals",
        "Hyung-Ho Park", "Bernabé Linares-Barranco",
        # transliteration apostrophes must survive into the key
        "Keʻalohi", "Suʹne",
    )
    for name in cases:
        keys = {
            "store": store_key(name),
            "graph_build": graph_key(name),
            "kg": kg_key(name),
            "author_context": ac_key(name),
        }
        unique = set(keys.values())
        assert len(unique) == 1, (name, keys)
