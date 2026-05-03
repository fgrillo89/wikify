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
