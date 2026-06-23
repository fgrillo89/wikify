"""F6 regression: the data source-text lookup resolves short chunk handles.

``data add`` verifies a point by locating its number in the cited chunk's
source text via ``source_text_for`` -> ``read_chunks_by_id`` (an exact match).
The MCP corpus tools hand agents a short ``chunk:<hex>`` handle, which exact
match misses -> empty source -> the point is wrongly rejected. The fix resolves
the handle to its canonical id and retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wikify.api import Corpus
from wikify.data import harvest

_SHORT = "chunk:62f9c659"
_CANON = "In-Memory-Computing_88ba30b3ca12__c0004_62f9c659"


@dataclass
class _FakeChunk:
    text: str
    doc_id: str


def _corpus(tmp_path: Path) -> Corpus:
    # Point at an empty dir so harvest._connect() returns None (no asset path);
    # we only exercise the chunk-resolution branch.
    (tmp_path / "corpus").mkdir(parents=True, exist_ok=True)
    return Corpus(root=tmp_path / "corpus")


def test_short_handle_resolved_then_read(tmp_path, monkeypatch):
    seen: list[list[str]] = []

    def fake_read(corpus, ids):
        seen.append(list(ids))
        if list(ids) == [_CANON]:
            return [_FakeChunk(text="ON/OFF ratio of 10^5 measured.", doc_id="canon_doc")]
        return []  # short handle misses exact match

    def fake_resolve(corpus, short):
        assert short == _SHORT
        return _CANON

    monkeypatch.setattr(harvest, "read_chunks_by_id", fake_read)
    monkeypatch.setattr("wikify.corpus.queries.resolve_chunk_id", fake_resolve)

    text, _asset, canon_doc = harvest.source_text_for(
        _corpus(tmp_path), doc_id="some_doc", chunk_id=_SHORT
    )
    assert text == "ON/OFF ratio of 10^5 measured."
    assert canon_doc == "canon_doc"
    # Proves the retry path fired: first the short id (miss), then canonical.
    assert seen == [[_SHORT], [_CANON]]


def test_canonical_id_needs_no_resolution(tmp_path, monkeypatch):
    def fake_read(corpus, ids):
        if list(ids) == [_CANON]:
            return [_FakeChunk(text="endurance 10^6 cycles.", doc_id="canon_doc")]
        return []

    def fake_resolve(corpus, short):  # pragma: no cover - must not be called
        raise AssertionError("resolution should not run when the exact id hits")

    monkeypatch.setattr(harvest, "read_chunks_by_id", fake_read)
    monkeypatch.setattr("wikify.corpus.queries.resolve_chunk_id", fake_resolve)

    text, _asset, _doc = harvest.source_text_for(
        _corpus(tmp_path), doc_id="d", chunk_id=_CANON
    )
    assert text == "endurance 10^6 cycles."


def test_unresolvable_handle_falls_through_empty(tmp_path, monkeypatch):
    from wikify.corpus.handles import HandleNotFoundError

    monkeypatch.setattr(harvest, "read_chunks_by_id", lambda corpus, ids: [])

    def fake_resolve(corpus, short):
        raise HandleNotFoundError(short)

    monkeypatch.setattr("wikify.corpus.queries.resolve_chunk_id", fake_resolve)

    text, _asset, canon_doc = harvest.source_text_for(
        _corpus(tmp_path), doc_id="orig_doc", chunk_id="chunk:deadbeef"
    )
    assert text == ""
    assert canon_doc == "orig_doc"  # falls back to the supplied doc_id
