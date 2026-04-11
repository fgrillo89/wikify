"""BM25 text search for fast lexical retrieval (Tier 2).

Provides a lightweight BM25 index over corpus chunks and wiki articles.
The index is built lazily on first query and rebuilt when invalidated.

This is a fast-path before ChromaDB embedding search: when the query
contains exact terms that appear in the corpus, BM25 finds them in
~100ms without loading the SentenceTransformer model.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import Chunk

logger = logging.getLogger(__name__)

# Minimum BM25 score to consider a result confident enough to skip embeddings
_BM25_CONFIDENCE_THRESHOLD = 2.0

# Minimum gap between top-1 and top-2 scores to consider top-1 decisive
_BM25_SCORE_GAP_RATIO = 1.5


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words (alphanumeric only)."""
    return re.findall(r"[a-z0-9]+", text.lower())


@dataclass
class BM25Index:
    """In-memory BM25 index over documents.

    Parameters:
        k1: Term frequency saturation parameter (default 1.5).
        b: Length normalization parameter (default 0.75).
    """

    k1: float = 1.5
    b: float = 0.75

    # Index state
    doc_ids: list[str] = field(default_factory=list)
    doc_tokens: list[list[str]] = field(default_factory=list)
    doc_lengths: list[int] = field(default_factory=list)
    avgdl: float = 0.0
    n_docs: int = 0
    df: dict[str, int] = field(default_factory=dict)  # document frequency

    def build(self, doc_ids: list[str], doc_texts: list[str]) -> None:
        """Build the index from document texts.

        Args:
            doc_ids: Document identifiers (e.g. Chunk.id).
            doc_texts: Corresponding document contents.
        """
        self.doc_ids = doc_ids
        self.doc_tokens = [_tokenize(t) for t in doc_texts]
        self.doc_lengths = [len(t) for t in self.doc_tokens]
        self.n_docs = len(doc_ids)
        self.avgdl = sum(self.doc_lengths) / max(self.n_docs, 1)

        # Compute document frequency for each term
        self.df = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] = self.df.get(term, 0) + 1

        logger.info(
            "BM25Index.build: %d docs, %d unique terms, avgdl=%.1f",
            self.n_docs,
            len(self.df),
            self.avgdl,
        )

    def query(
        self,
        query_text: str,
        n_results: int = 20,
    ) -> list[tuple[str, float]]:
        """Score all documents against a query and return top results.

        Args:
            query_text: Natural language query.
            n_results: Maximum number of results to return.

        Returns:
            List of (doc_id, bm25_score) sorted by score descending.
        """
        if not self.doc_ids:
            return []

        query_tokens = _tokenize(query_text)
        if not query_tokens:
            return []

        query_tf = Counter(query_tokens)
        scores: list[tuple[str, float]] = []

        for i in range(self.n_docs):
            score = 0.0
            doc_toks = self.doc_tokens[i]
            doc_len = self.doc_lengths[i]
            doc_tf = Counter(doc_toks)

            for term, qtf in query_tf.items():
                if term not in self.df:
                    continue

                tf = doc_tf.get(term, 0)
                if tf == 0:
                    continue

                # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
                idf = math.log((self.n_docs - self.df[term] + 0.5) / (self.df[term] + 0.5) + 1.0)

                # TF normalization
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                )

                score += idf * tf_norm

            if score > 0:
                scores.append((self.doc_ids[i], score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:n_results]


class _ChunkIndexCache:
    """Singleton-style cache for the BM25 chunk index.

    Replaces a module-level mutable global with a class instance whose
    state lives on the instance, not on the module. The instance itself
    is created exactly once at import time and exposes ``get`` and
    ``invalidate``.
    """

    def __init__(self) -> None:
        self._index: BM25Index | None = None

    def get(self) -> BM25Index:
        if self._index is not None:
            return self._index

        index = BM25Index()
        with get_session() as session:
            chunks: list[Chunk] = list(
                session.exec(select(Chunk).where(Chunk.token_count > 10)).all()
            )
        if chunks:
            index.build(
                doc_ids=[c.id for c in chunks],
                doc_texts=[c.content for c in chunks],
            )
        self._index = index
        return index

    def invalidate(self) -> None:
        self._index = None
        logger.info("BM25 chunk index invalidated")


_chunk_index_cache = _ChunkIndexCache()


def get_chunk_bm25_index() -> BM25Index:
    """Return the BM25 index over corpus chunks, building if needed."""

    return _chunk_index_cache.get()


def invalidate_bm25_index() -> None:
    """Invalidate the cached BM25 index (e.g. after new ingestion)."""

    _chunk_index_cache.invalidate()


def bm25_search(
    query: str,
    n_results: int = 20,
) -> list[tuple[str, float]]:
    """Search corpus chunks via BM25.

    Args:
        query: Natural language query.
        n_results: Maximum results.

    Returns:
        List of (chunk_id, bm25_score) sorted by relevance.
    """
    index = get_chunk_bm25_index()
    return index.query(query, n_results=n_results)


def bm25_is_confident(results: list[tuple[str, float]]) -> bool:
    """Check if BM25 results are confident enough to skip embedding search.

    Confident = top result score >= threshold AND score gap to #2 is large.
    """
    if not results:
        return False

    top_score = results[0][1]
    if top_score < _BM25_CONFIDENCE_THRESHOLD:
        return False

    if len(results) < 2:
        return True

    second_score = results[1][1]
    if second_score == 0:
        return True

    return top_score / second_score >= _BM25_SCORE_GAP_RATIO
