"""ChromaDB summary embeddings and k-NN similarity queries.

EmbeddingStore is the core class.  A module-level instance ``_store`` is used
by the convenience functions below.  Prefer dependency injection (pass an
EmbeddingStore explicitly) when you need to swap it in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import chromadb

from scholarforge.config import settings
from scholarforge.store.models import Paper

if TYPE_CHECKING:
    from chromadb.api.models.Collection import Collection
    from sentence_transformers import SentenceTransformer

_COLLECTION_NAME = "document_summaries"


class EmbeddingStore:
    """Manages ChromaDB + SentenceTransformer lifecycle.

    Designed for dependency injection: create an instance and pass it where
    needed.  The module-level ``_store`` instance is used by the convenience
    functions below.  Lazy-initializes both components on first property access.

    SentenceTransformer is only loaded when encoding is needed (search, embed).
    ChromaDB operations on stored vectors (k-NN lookup) don't require the model.
    """

    def __init__(
        self,
        chromadb_dir: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self._chromadb_dir = chromadb_dir or str(settings.chromadb_dir)
        self._model_name = model_name or settings.embedding_model
        self._model: SentenceTransformer | None = None
        self._client: chromadb.ClientAPI | None = None
        self._collection: Collection | None = None

    @property
    def model(self) -> SentenceTransformer:
        """SentenceTransformer model, loaded on first access."""
        if self._model is None:
            import os

            from sentence_transformers import SentenceTransformer

            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            self._model = SentenceTransformer(self._model_name)
        return self._model

    @property
    def collection(self) -> Collection:
        """ChromaDB collection, created on first access."""
        if self._collection is None:
            from pathlib import Path

            Path(self._chromadb_dir).mkdir(parents=True, exist_ok=True)
            if self._client is None:
                self._client = chromadb.PersistentClient(path=self._chromadb_dir)
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection


# ── Module-level instance ─────────────────────────────────────────────────────

_store = EmbeddingStore()


# ── Module-level convenience functions ───────────────────────────────────────


def _get_model() -> SentenceTransformer:
    return _store.model


def _get_collection() -> Collection:
    return _store.collection


def embed_summaries(papers: list[Paper], force: bool = False) -> int:
    """Batch-upsert summary embeddings for a list of papers.

    When force=False (default), skips papers already present in ChromaDB
    to avoid redundant SentenceTransformer inference.
    """
    eligible = [p for p in papers if p.summary and p.summary.strip()]
    if not eligible:
        return 0

    if not force:
        all_ids = [p.id for p in eligible]
        existing = _store.collection.get(ids=all_ids)
        existing_ids = set(existing.get("ids") or [])
        eligible = [p for p in eligible if p.id not in existing_ids]
        if not eligible:
            return 0

    summaries = [p.summary for p in eligible]  # type: ignore[misc]
    ids = [p.id for p in eligible]
    embeddings = _store.model.encode(summaries)

    _store.collection.upsert(
        ids=ids,
        embeddings=embeddings,  # type: ignore[arg-type]
        documents=summaries,
    )
    return len(eligible)


def query_similar(paper_id: str, n_results: int = 5) -> list[tuple[str, float]]:
    """Query ChromaDB for papers similar to the given paper."""
    collection = _store.collection

    result = collection.get(ids=[paper_id], include=["embeddings"])
    stored_embeddings = result.get("embeddings")
    if stored_embeddings is None or len(stored_embeddings) == 0:
        return []

    query_embedding = stored_embeddings[0]
    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results + 1,
        include=["distances"],
    )

    result_ids: list[str] = raw["ids"][0] if raw["ids"] else []
    distances: list[float] = raw["distances"][0] if raw["distances"] else []
    pairs = [(rid, dist) for rid, dist in zip(result_ids, distances) if rid != paper_id]
    return pairs[:n_results]


def get_all_similar(
    paper_ids: list[str],
    n_results: int = 5,
) -> dict[str, list[str]]:
    """Return similar paper IDs for each given paper ID."""
    if not paper_ids:
        return {}

    collection = _store.collection

    result = collection.get(ids=paper_ids, include=["embeddings"])
    stored_ids: list[str] = result.get("ids") or []
    raw_embeddings = result.get("embeddings")
    stored_embeddings = raw_embeddings if raw_embeddings is not None else []

    if not stored_ids:
        return {pid: [] for pid in paper_ids}

    raw = collection.query(
        query_embeddings=stored_embeddings,  # type: ignore[arg-type]
        n_results=n_results + 1,
        include=["distances"],
    )

    output: dict[str, list[str]] = {pid: [] for pid in paper_ids}
    for queried_id, result_ids in zip(stored_ids, raw["ids"]):
        similar = [rid for rid in result_ids if rid != queried_id][:n_results]
        output[queried_id] = similar

    return output
