"""ChromaDB summary embeddings and k-NN similarity queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import chromadb

from scholarforge.config import settings
from scholarforge.store.models import Paper

if TYPE_CHECKING:
    from chromadb.api.models.Collection import Collection
    from sentence_transformers import SentenceTransformer

# Module-level singletons (lazy-initialized)
_model: SentenceTransformer | None = None
_collection: Collection | None = None

_COLLECTION_NAME = "document_summaries"


def _get_model() -> SentenceTransformer:
    """Return the SentenceTransformer model, lazy-initialized.

    The import is deferred so modules that only need ChromaDB (graph metrics,
    similarity lookups on stored vectors) don't pay the 10s sentence-transformers
    import + model-load cost.
    """
    global _model
    if _model is None:
        import os

        from sentence_transformers import SentenceTransformer

        # Suppress HuggingFace HTTP calls that check for model updates
        # on every load. The model is cached locally after first download.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def _get_collection() -> Collection:
    """Return the ChromaDB collection, lazy-initialized as a singleton."""
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(settings.chromadb_dir))
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def embed_summaries(papers: list[Paper]) -> int:
    """Batch-upsert summary embeddings for a list of papers.

    Skips papers with null or empty summaries.

    Args:
        papers: List of Paper objects to embed.

    Returns:
        Count of papers actually embedded.
    """
    eligible = [p for p in papers if p.summary and p.summary.strip()]
    if not eligible:
        return 0

    model = _get_model()
    collection = _get_collection()

    summaries = [p.summary for p in eligible]  # type: ignore[misc]
    ids = [p.id for p in eligible]

    embeddings = model.encode(summaries)

    collection.upsert(
        ids=ids,
        embeddings=embeddings,  # type: ignore[arg-type]
        documents=summaries,
    )

    return len(eligible)


def query_similar(
    paper_id: str,
    n_results: int = 5,
) -> list[tuple[str, float]]:
    """Query ChromaDB for papers similar to the given paper.

    Args:
        paper_id: ID of the paper to find similar papers for.
        n_results: Maximum number of similar papers to return (excluding self).

    Returns:
        List of (similar_paper_id, distance) pairs sorted by ascending distance.
        Returns an empty list if the paper has no stored embedding.
    """
    collection = _get_collection()

    # Retrieve the paper's own embedding to use as query vector
    result = collection.get(ids=[paper_id], include=["embeddings"])
    stored_embeddings = result.get("embeddings")
    if not stored_embeddings or len(stored_embeddings) == 0:
        return []

    query_embedding = stored_embeddings[0]

    # Request one extra to account for the self-match
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
    """Return similar paper IDs for each given paper ID.

    Args:
        paper_ids: List of paper IDs to query.
        n_results: Maximum number of similar paper IDs to return per paper.

    Returns:
        Mapping of paper_id -> list of similar paper IDs (self excluded).
    """
    if not paper_ids:
        return {}

    collection = _get_collection()

    # Fetch all embeddings in a single batch
    result = collection.get(ids=paper_ids, include=["embeddings"])
    stored_ids: list[str] = result.get("ids") or []
    raw_embeddings = result.get("embeddings")
    stored_embeddings = raw_embeddings if raw_embeddings is not None else []

    if not stored_ids:
        return {pid: [] for pid in paper_ids}

    # Batch query — one query vector per stored paper
    raw = collection.query(
        query_embeddings=stored_embeddings,  # type: ignore[arg-type]
        n_results=n_results + 1,  # +1 to drop self-match
        include=["distances"],
    )

    output: dict[str, list[str]] = {pid: [] for pid in paper_ids}

    for queried_id, result_ids in zip(stored_ids, raw["ids"]):
        similar = [rid for rid in result_ids if rid != queried_id][:n_results]
        output[queried_id] = similar

    # Papers with no stored embedding get an empty list (already set above)
    return output
