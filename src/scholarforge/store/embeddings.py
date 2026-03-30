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
_CHUNK_COLLECTION_NAME = "chunk_embeddings"


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
        """ChromaDB collection for paper summaries, created on first access."""
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

    @property
    def chunk_collection(self) -> Collection:
        """ChromaDB collection for chunk embeddings, created on first access."""
        if not hasattr(self, "_chunk_collection") or self._chunk_collection is None:
            from pathlib import Path

            Path(self._chromadb_dir).mkdir(parents=True, exist_ok=True)
            if self._client is None:
                self._client = chromadb.PersistentClient(path=self._chromadb_dir)
            self._chunk_collection = self._client.get_or_create_collection(
                name=_CHUNK_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._chunk_collection


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


def embed_chunks(chunks: list, force: bool = False) -> int:
    """Batch-upsert chunk embeddings into the chunk_embeddings collection.

    Args:
        chunks: List of Chunk objects with .id, .content, .paper_id fields.
        force: If True, re-embed even if already present.

    Returns:
        Number of chunks embedded.
    """
    eligible = [c for c in chunks if c.content and c.content.strip()]
    if not eligible:
        return 0

    if not force:
        all_ids = [c.id for c in eligible]
        # Check in batches (ChromaDB has a limit on get() batch size)
        existing_ids: set[str] = set()
        batch_size = 500
        for i in range(0, len(all_ids), batch_size):
            batch = all_ids[i : i + batch_size]
            result = _store.chunk_collection.get(ids=batch)
            existing_ids.update(result.get("ids") or [])
        eligible = [c for c in eligible if c.id not in existing_ids]
        if not eligible:
            return 0

    texts = [c.content for c in eligible]
    ids = [c.id for c in eligible]
    metadatas = [{"paper_id": c.paper_id, "token_count": c.token_count} for c in eligible]

    embeddings = _store.model.encode(texts, batch_size=64, show_progress_bar=False)

    # Upsert in batches
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        _store.chunk_collection.upsert(
            ids=ids[i : i + batch_size],
            embeddings=embeddings[i : i + batch_size],  # type: ignore[arg-type]
            metadatas=metadatas[i : i + batch_size],
        )

    return len(eligible)


def get_chunk_embeddings(chunk_ids: list[str]) -> dict[str, list[float]]:
    """Retrieve stored chunk embeddings by ID.

    Returns:
        Dict mapping chunk_id -> embedding vector (384-dim list).
    """
    if not chunk_ids:
        return {}

    result: dict[str, list[float]] = {}
    batch_size = 500
    for i in range(0, len(chunk_ids), batch_size):
        batch = chunk_ids[i : i + batch_size]
        raw = _store.chunk_collection.get(ids=batch, include=["embeddings"])
        raw_ids = raw.get("ids") or []
        raw_embs = raw.get("embeddings")
        if raw_embs is None or (hasattr(raw_embs, "__len__") and len(raw_embs) == 0):
            continue
        for cid, emb in zip(raw_ids, raw_embs):
            result[cid] = emb
    return result


def get_paper_vibe_vectors() -> dict[str, list[float]]:
    """Compute paper vibe vectors as token-weighted centroids of chunk embeddings.

    Uses stored chunk embeddings from ChromaDB (no re-encoding needed).
    Returns a dict mapping paper_id -> normalized 384-dim centroid vector.
    """
    import numpy as np
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Chunk

    with get_session() as session:
        chunks = session.exec(select(Chunk)).all()

    if not chunks:
        return {}

    # Group chunks by paper
    paper_chunks: dict[str, list[Chunk]] = {}
    for c in chunks:
        paper_chunks.setdefault(c.paper_id, []).append(c)

    # Fetch all chunk embeddings at once
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    vibes: dict[str, list[float]] = {}
    for paper_id, p_chunks in paper_chunks.items():
        embeddings = []
        weights = []
        for c in p_chunks:
            emb = stored.get(c.id)
            if emb is not None:
                embeddings.append(emb)
                weights.append(c.token_count)

        if not embeddings:
            continue

        emb_array = np.array(embeddings)
        weight_array = np.array(weights, dtype=float)
        weight_array /= weight_array.sum() + 1e-9
        centroid = np.average(emb_array, axis=0, weights=weight_array)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        vibes[paper_id] = centroid.tolist()

    return vibes


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
