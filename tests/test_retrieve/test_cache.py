"""Tests for retrieve/cache.py -- Query cache with Jaccard similarity."""

from __future__ import annotations

from wikify.core.retrieve.cache import QueryCache, _jaccard, _tokenize

# ── Tokenization ────────────────────────────────────────────────────────────


def test_tokenize_basic():
    """Splits into lowercase words, drops 1-char tokens."""
    assert _tokenize("ALD Growth Rate") == {"ald", "growth", "rate"}


def test_tokenize_empty():
    """Empty string returns empty set."""
    assert _tokenize("") == set()


# ── Jaccard ─────────────────────────────────────────────────────────────────


def test_jaccard_identical():
    """Identical sets have similarity 1.0."""
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint():
    """Disjoint sets have similarity 0.0."""
    assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial():
    """Partial overlap gives correct ratio."""
    # {a,b,c} & {b,c,d} = {b,c}, union = {a,b,c,d} -> 2/4 = 0.5
    assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 0.5


def test_jaccard_empty():
    """Empty set returns 0.0."""
    assert _jaccard(set(), {"a"}) == 0.0


# ── QueryCache ──────────────────────────────────────────────────────────────


def test_cache_exact_hit():
    """Exact query match returns cached result."""
    cache = QueryCache()
    cache.put("ALD growth rate", {"result": 1})

    result, tier = cache.get("ALD growth rate")
    assert result == {"result": 1}
    assert tier == "exact"


def test_cache_miss():
    """Unknown query returns None, 'miss'."""
    cache = QueryCache()
    result, tier = cache.get("unknown query")
    assert result is None
    assert tier == "miss"


def test_cache_fuzzy_hit():
    """Near-duplicate query (Jaccard >= 0.6) returns cached result."""
    cache = QueryCache(jaccard_threshold=0.6)
    cache.put("ALD growth rate on silicon", {"result": 1})

    # 4/5 token overlap = 0.8 Jaccard
    result, tier = cache.get("ALD growth rate on germanium")
    assert result == {"result": 1}
    assert tier == "fuzzy"


def test_cache_fuzzy_miss():
    """Low-overlap query doesn't trigger fuzzy hit."""
    cache = QueryCache(jaccard_threshold=0.6)
    cache.put("ALD growth rate", {"result": 1})

    # Completely different query
    result, tier = cache.get("memristor endurance cycling")
    assert result is None
    assert tier == "miss"


def test_cache_expiry():
    """Expired entries are not returned."""
    cache = QueryCache(ttl=0.0)  # immediate expiry
    cache.put("query", {"result": 1})

    result, tier = cache.get("query")
    assert result is None
    assert tier == "miss"


def test_cache_eviction():
    """Oldest entry is evicted when cache is full."""
    cache = QueryCache(max_size=2)
    cache.put("first query here", {"result": 1})
    cache.put("second query here", {"result": 2})
    cache.put("third query here", {"result": 3})

    # First should be evicted
    assert cache.stats["size"] == 2


def test_cache_clear():
    """clear() removes all entries."""
    cache = QueryCache()
    cache.put("query", {"result": 1})
    cache.clear()

    result, tier = cache.get("query")
    assert result is None


def test_cache_stats():
    """Stats track hits and misses."""
    cache = QueryCache()
    cache.put("query one", {"result": 1})
    cache.get("query one")  # exact hit
    cache.get("unknown query")  # miss

    stats = cache.stats
    assert stats["hits_exact"] == 1
    assert stats["misses"] == 1
