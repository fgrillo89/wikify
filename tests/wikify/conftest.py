"""Test-suite fixtures for ``wikify``.

Pins the embedder backend to ``hash`` for the duration of every test
that doesn't explicitly override it. This preserves the historical
test behaviour after the production default flipped from ``hash`` →
``fastembed``: a real semantic embedder produces different similarity
graphs → different sampler decisions → different golden assertions in
tests like ``test_iteration_history`` that depend on a stable sampling
order.

Tests that genuinely need a real embedder must override
``WIKIFY_EMBEDDER`` themselves (e.g. via ``monkeypatch.setenv``)
inside the test body.
"""

from __future__ import annotations

import pytest

from wikify.api import Corpus
from wikify.corpus.store import Store, transaction
from wikify.corpus.store.sync import project_documents
from wikify.models import Chunk, Document


@pytest.fixture(autouse=True)
def _pin_embedder_to_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIFY_EMBEDDER", "hash")


@pytest.fixture
def make_sqlite_corpus(tmp_path):
    """Build a SQLite-only corpus from a list of (Document, [Chunk]) pairs."""

    counter = {"n": 0}

    def _make(docs_chunks: list[tuple[Document, list[Chunk]]]) -> Corpus:
        counter["n"] += 1
        slug = "corpus" if counter["n"] == 1 else f"corpus_{counter['n']}"
        corpus = Corpus(root=tmp_path / slug)
        corpus.ensure()
        store = Store(corpus.sqlite_path)
        try:
            with transaction(store.con):
                by_doc = {d.id: list(ch) for d, ch in docs_chunks}
                project_documents(
                    store, [d for d, _ in docs_chunks], by_doc,
                )
            store.fts_rebuild()
        finally:
            store.close()
        return corpus

    return _make
