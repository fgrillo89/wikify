"""F2 regression: MCP chunk envelope items expose the canonical chunk id.

Subagents must cite the long canonical chunk id (``<title>_<dochex>__cNNNN_<hex>``),
not the short ``chunk:<hex>`` handle. Before this fix the MCP item builders
emitted only the short handle, forcing subagents to spelunk the corpus SQLite
with ``LIKE`` to recover the canonical id. The builders now carry it directly
under ``canonical_id``.
"""

from wikify.mcp.envelope import chunk_item, chunk_row_item, traverse_row_item
from wikify.models import Chunk

_CANON = "Atomic-Layer-Deposition_2301ec7574d8__c0007_ab12cd34"


def _chunk() -> Chunk:
    return Chunk(
        id=_CANON,
        doc_id="Atomic-Layer-Deposition_2301ec7574d8",
        ord=7,
        text="ALD proceeds via self-limiting half-reactions.",
        char_span=(0, 46),
        section_path=["Process"],
        section_type="body",
    )


def test_chunk_item_exposes_canonical_id():
    item = chunk_item(_chunk())
    assert item["canonical_id"] == _CANON
    # The short handle is still present and is distinct from the canonical id.
    assert item["handle"].startswith("chunk:")
    assert item["handle"] != item["canonical_id"]


def test_chunk_row_item_exposes_canonical_id():
    item = chunk_row_item({"id": _CANON, "doc_id": "doc:2301ec7574d8", "text": "x"})
    assert item["canonical_id"] == _CANON
    assert item["handle"].startswith("chunk:")


def test_traverse_row_item_chunk_exposes_canonical_id():
    item = traverse_row_item({"type": "chunk", "id": _CANON, "doc_id": "doc:2301ec7574d8"})
    assert item["canonical_id"] == _CANON
