"""Tests for retrieve/bm25.py -- BM25 text search."""

from __future__ import annotations

from wikify.retrieve.bm25 import BM25Index, _tokenize, bm25_is_confident

# ── Tokenization ────────────────────────────────────────────────────────────


def test_tokenize():
    """Splits into lowercase alphanumeric tokens."""
    assert _tokenize("HfO2 ALD at 250C") == ["hfo2", "ald", "at", "250c"]


def test_tokenize_empty():
    """Empty string returns empty list."""
    assert _tokenize("") == []


# ── BM25Index ───────────────────────────────────────────────────────────────


def test_bm25_basic_ranking():
    """Documents with query terms rank higher."""
    index = BM25Index()
    index.build(
        doc_ids=["d1", "d2", "d3"],
        doc_texts=[
            "atomic layer deposition of HfO2 thin films",
            "chemical vapor deposition of silicon nitride",
            "memristor device fabrication using ALD HfO2",
        ],
    )

    results = index.query("HfO2 ALD")
    assert len(results) > 0

    # d1 and d3 mention HfO2, d3 also mentions ALD
    result_ids = [rid for rid, _ in results]
    assert "d2" not in result_ids or results[-1][0] == "d2"
    # d1 or d3 should be top (both have HfO2)
    assert result_ids[0] in ("d1", "d3")


def test_bm25_no_match():
    """Query with no matching terms returns empty."""
    index = BM25Index()
    index.build(
        doc_ids=["d1"],
        doc_texts=["atomic layer deposition"],
    )

    results = index.query("quantum computing")
    assert results == []


def test_bm25_empty_index():
    """Empty index returns empty results."""
    index = BM25Index()
    results = index.query("anything")
    assert results == []


def test_bm25_idf_weighting():
    """Rare terms get higher weight than common terms."""
    index = BM25Index()
    index.build(
        doc_ids=["d1", "d2", "d3"],
        doc_texts=[
            "the deposition process uses ALD",
            "the deposition process uses CVD",
            "the deposition process uses sputtering",
        ],
    )

    # "ALD" appears in 1/3 docs (high IDF)
    # "deposition" appears in 3/3 docs (low IDF)
    results = index.query("ALD deposition")
    assert results[0][0] == "d1"  # d1 has the rare term "ALD"


def test_bm25_result_limit():
    """n_results caps the output."""
    index = BM25Index()
    index.build(
        doc_ids=[f"d{i}" for i in range(10)],
        doc_texts=[f"document about topic {i}" for i in range(10)],
    )

    results = index.query("document topic", n_results=3)
    assert len(results) == 3


# ── Confidence ──────────────────────────────────────────────────────────────


def test_bm25_confident_strong_top():
    """Confident when top score is high and gap to #2 is large."""
    results = [("d1", 5.0), ("d2", 2.0)]
    assert bm25_is_confident(results) is True


def test_bm25_not_confident_close_scores():
    """Not confident when top scores are close."""
    results = [("d1", 3.0), ("d2", 2.5)]
    assert bm25_is_confident(results) is False


def test_bm25_not_confident_low_score():
    """Not confident when top score is below threshold."""
    results = [("d1", 1.0)]
    assert bm25_is_confident(results) is False


def test_bm25_confident_single_result():
    """Confident with single high-scoring result."""
    results = [("d1", 5.0)]
    assert bm25_is_confident(results) is True


def test_bm25_empty_not_confident():
    """Empty results are not confident."""
    assert bm25_is_confident([]) is False
