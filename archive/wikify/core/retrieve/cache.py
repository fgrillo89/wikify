"""Query cache with exact-hash and Jaccard-similarity near-hit detection.

Tier 0: exact query match (hash lookup)          -- ~0ms
Tier 1: fuzzy near-hit (Jaccard >= threshold)     -- ~1ms

The cache is in-memory with a configurable max size. Entries expire
after a TTL to prevent stale results. The cache is process-scoped
and not persisted to disk.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 256
_DEFAULT_TTL_SECONDS = 600  # 10 minutes
_JACCARD_THRESHOLD = 0.6


def _tokenize(text: str) -> set[str]:
    """Tokenize a query into a set of lowercase words."""
    return {w for w in text.lower().split() if len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _query_hash(query: str) -> str:
    """Compute a stable hash for a query string."""
    return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:16]


@dataclass
class CacheEntry:
    """A cached query result with metadata."""

    query: str
    tokens: set[str]
    query_hash: str
    result: Any
    created_at: float
    hits: int = 0


class QueryCache:
    """In-memory query cache with exact and fuzzy matching.

    Attributes:
        max_size: Maximum number of entries in the cache.
        ttl: Time-to-live in seconds for each entry.
        jaccard_threshold: Minimum Jaccard similarity for a fuzzy hit.
    """

    def __init__(
        self,
        max_size: int = _DEFAULT_MAX_SIZE,
        ttl: float = _DEFAULT_TTL_SECONDS,
        jaccard_threshold: float = _JACCARD_THRESHOLD,
    ) -> None:
        self.max_size = max_size
        self.ttl = ttl
        self.jaccard_threshold = jaccard_threshold
        self._entries: dict[str, CacheEntry] = {}
        self._stats = {"hits_exact": 0, "hits_fuzzy": 0, "misses": 0}

    def get(self, query: str) -> tuple[Any | None, str]:
        """Look up a query in the cache.

        Returns:
            (result, tier) where tier is "exact", "fuzzy", or "miss".
            result is None on a miss.
        """
        now = time.monotonic()
        qhash = _query_hash(query)

        # Tier 0: exact hash match
        entry = self._entries.get(qhash)
        if entry is not None:
            if now - entry.created_at < self.ttl:
                entry.hits += 1
                self._stats["hits_exact"] += 1
                logger.debug("QueryCache: exact hit for %r", query[:40])
                return entry.result, "exact"
            # Expired
            del self._entries[qhash]

        # Tier 1: Jaccard fuzzy match
        qtokens = _tokenize(query)
        best_sim = 0.0
        best_entry: CacheEntry | None = None

        for entry in self._entries.values():
            if now - entry.created_at >= self.ttl:
                continue
            sim = _jaccard(qtokens, entry.tokens)
            if sim > best_sim:
                best_sim = sim
                best_entry = entry

        if best_entry is not None and best_sim >= self.jaccard_threshold:
            best_entry.hits += 1
            self._stats["hits_fuzzy"] += 1
            logger.debug(
                "QueryCache: fuzzy hit (J=%.2f) for %r -> %r",
                best_sim,
                query[:30],
                best_entry.query[:30],
            )
            return best_entry.result, "fuzzy"

        self._stats["misses"] += 1
        return None, "miss"

    def put(self, query: str, result: Any) -> None:
        """Store a query result in the cache.

        Evicts the oldest entry if the cache is full.
        """
        # Evict expired entries first
        now = time.monotonic()
        expired = [k for k, v in self._entries.items() if now - v.created_at >= self.ttl]
        for k in expired:
            del self._entries[k]

        # Evict oldest if still over capacity
        if len(self._entries) >= self.max_size:
            oldest_key = min(self._entries, key=lambda k: self._entries[k].created_at)
            del self._entries[oldest_key]

        qhash = _query_hash(query)
        self._entries[qhash] = CacheEntry(
            query=query,
            tokens=_tokenize(query),
            query_hash=qhash,
            result=result,
            created_at=now,
        )

    def clear(self) -> None:
        """Clear all cached entries."""
        self._entries.clear()

    @property
    def stats(self) -> dict[str, int]:
        """Return cache hit/miss statistics."""
        return {**self._stats, "size": len(self._entries)}


# Module-level cache instance
_query_cache = QueryCache()


def get_query_cache() -> QueryCache:
    """Return the module-level QueryCache instance."""
    return _query_cache
