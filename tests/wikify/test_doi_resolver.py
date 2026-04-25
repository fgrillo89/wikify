"""Tests for the shared DOI resolver.

Covers cache hit / miss paths, CrossRef-first + doi.org-fallback strategy,
source-tag correctness on mixed outcomes, negative-result caching, and
the TTL-based retry for stale negatives.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from wikify.citations.db import DOICache


def _fake_resolve_many(
    tmp_path: Path,
    xref_results: dict[str, dict[str, object]],
    doiorg_results: dict[str, dict[str, object]],
):
    """Patch the async HTTP layers and call the real resolve_many."""
    async def fake_xref(dois, *, concurrency, qps, timeout):
        return {d.lower(): xref_results.get(d.lower(), {}) for d in dois}

    async def fake_doiorg(dois, *, concurrency, qps, timeout):
        return {d.lower(): doiorg_results.get(d.lower(), {}) for d in dois}

    from wikify.util import doi_resolver

    with patch.object(doi_resolver, "_crossref_batch", fake_xref), \
         patch.object(doi_resolver, "_doiorg_fallback", fake_doiorg):
        cache_path = tmp_path / ".citestore.db"
        return doi_resolver.resolve_many(
            list(xref_results.keys() | doiorg_results.keys()),
            cache_path=cache_path,
        )


def _row_source(path: Path, doi: str) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT source FROM works WHERE doi = ?", (doi.lower(),),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def test_crossref_complete_hits_are_tagged_crossref(tmp_path):
    full = {"title": "Ref Paper", "authors": ["A. Author"]}
    result = _fake_resolve_many(
        tmp_path,
        xref_results={"10.1000/a": full},
        doiorg_results={},
    )
    assert result["10.1000/a"]["title"] == "Ref Paper"
    assert _row_source(tmp_path / ".citestore.db", "10.1000/a") == "crossref"


def test_crossref_incomplete_then_doiorg_complete_is_tagged_doiorg(tmp_path):
    # CrossRef returns title-only (incomplete: no authors). doi.org
    # completes it. The stored row must be tagged doi.org, not crossref.
    result = _fake_resolve_many(
        tmp_path,
        xref_results={"10.1000/b": {"title": "Thin Title"}},  # no authors
        doiorg_results={
            "10.1000/b": {
                "title": "Thin Title",
                "authors": ["B. Recovered"],
            },
        },
    )
    assert result["10.1000/b"]["authors"] == ["B. Recovered"]
    assert _row_source(tmp_path / ".citestore.db", "10.1000/b") == "doi.org"


def _is_empty_row(meta: dict) -> bool:
    """A cached negative row has all-empty string/list fields."""
    return not meta.get("title") and not meta.get("authors")


def test_both_sources_fail_caches_negative(tmp_path):
    result = _fake_resolve_many(
        tmp_path,
        xref_results={"10.1000/c": {}},
        doiorg_results={"10.1000/c": {}},
    )
    # Empty metadata returned to caller.
    assert _is_empty_row(result["10.1000/c"])
    # Row exists with source="not-found".
    assert _row_source(tmp_path / ".citestore.db", "10.1000/c") == "not-found"


def test_cache_hit_skips_network(tmp_path):
    # Pre-populate cache.
    cache_path = tmp_path / ".citestore.db"
    with DOICache(cache_path) as cache:
        cache.put(
            "10.1000/d",
            {"title": "Cached", "authors": ["C. Cached"]},
            source="crossref",
        )
    # Now call resolve_many with mocks that would fail if hit.
    async def boom(*args, **kwargs):
        raise RuntimeError("network should not be called")

    from wikify.util import doi_resolver

    with patch.object(doi_resolver, "_crossref_batch", boom), \
         patch.object(doi_resolver, "_doiorg_fallback", boom):
        result = doi_resolver.resolve_many(
            ["10.1000/d"], cache_path=cache_path,
        )
    assert result["10.1000/d"]["title"] == "Cached"


def test_negative_cache_is_honoured_for_fresh_rows(tmp_path):
    cache_path = tmp_path / ".citestore.db"
    with DOICache(cache_path) as cache:
        cache.put("10.1000/e", {}, source="not-found")
    # A fresh negative row blocks network retries.
    async def should_not_fire(*args, **kwargs):
        raise RuntimeError("negative should have short-circuited")

    from wikify.util import doi_resolver

    with patch.object(doi_resolver, "_crossref_batch", should_not_fire), \
         patch.object(doi_resolver, "_doiorg_fallback", should_not_fire):
        result = doi_resolver.resolve_many(
            ["10.1000/e"], cache_path=cache_path,
        )
    assert _is_empty_row(result["10.1000/e"])


def test_expired_negative_is_retried(tmp_path):
    # Write a row with source=not-found and resolved_at backdated past the TTL.
    cache_path = tmp_path / ".citestore.db"
    with DOICache(cache_path) as cache:
        cache.put("10.1000/f", {}, source="not-found")
    # Backdate resolved_at to force expiry.
    expired_days = DOICache.NEGATIVE_TTL_DAYS + 1
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(
            "UPDATE works SET resolved_at = datetime('now', ?) "
            "WHERE doi = ?",
            (f"-{expired_days} days", "10.1000/f"),
        )
        conn.commit()
    finally:
        conn.close()

    # A subsequent resolve should retry, and this time the network succeeds.
    result = _fake_resolve_many(
        tmp_path,
        xref_results={"10.1000/f": {"title": "Late Reg", "authors": ["F. F."]}},
        doiorg_results={},
    )
    assert result["10.1000/f"]["title"] == "Late Reg"
    assert _row_source(cache_path, "10.1000/f") == "crossref"


def test_is_complete_requires_title_and_authors():
    from wikify.util.doi_resolver import _is_complete

    assert _is_complete({"title": "T", "authors": ["A"]})
    assert not _is_complete({})
    assert not _is_complete(None)
    assert not _is_complete({"title": "T", "authors": []})
    assert not _is_complete({"title": "", "authors": ["A"]})


def test_skip_content_neg_suppresses_doiorg_fallback(tmp_path):
    # CrossRef returns an incomplete record; ordinarily doi.org completes it.
    # With skip_content_neg=True the fallback must NOT be called and the
    # result must be negative-cached.
    xref_results = {"10.1000/h": {"title": "Thin Title"}}

    async def fake_xref(dois, *, concurrency, qps, timeout):
        return {d.lower(): xref_results.get(d.lower(), {}) for d in dois}

    async def fail_doiorg(*args, **kwargs):
        raise AssertionError("doi.org fallback must not be called")

    from wikify.util import doi_resolver

    with patch.object(doi_resolver, "_crossref_batch", fake_xref), \
         patch.object(doi_resolver, "_doiorg_fallback", fail_doiorg):
        result = doi_resolver.resolve_many(
            ["10.1000/h"],
            cache_path=tmp_path / ".citestore.db",
            skip_content_neg=True,
        )
    # CrossRef returned title-only → treated as incomplete → negative-cached.
    assert _is_empty_row(result["10.1000/h"])
    assert _row_source(tmp_path / ".citestore.db", "10.1000/h") == "not-found"


def test_enrich_citations_skip_content_neg_propagates():
    # enrich_citations must forward skip_content_neg to resolve_many.
    # Use a real CitationEntry so to_dict() / heuristic-parse behaviour
    # match production; patch the actual HTTP layers to catch the flag.
    from wikify.citations.models import CitationEntry
    from wikify.ingest import cite_parse
    from wikify.models import Document
    from wikify.util import doi_resolver as _dr

    captured: dict[str, object] = {}

    def fake_resolve_many(
        dois, *, cache_path, skip_content_neg=False, **_kwargs,
    ):
        captured["skip"] = skip_content_neg
        return {d.lower(): {} for d in dois}

    cit = CitationEntry(
        ord=0,
        raw_text="Author A. (2020). Example paper. Journal. 10.1234/s41586-020-1234-5",
        doi="10.1234/s41586-020-1234-5",
        title="Example Paper With Long Enough Title",
        authors=["Author A."],
        year=2020,
    )
    doc = Document(
        id="d1",
        source_path="x.pdf",
        kind="pdf",
        title="T",
        metadata={},
        markdown_path="x.md",
        image_dir="x/",
    )
    doc.citations = [cit]

    with patch.object(_dr, "resolve_many", fake_resolve_many):
        cite_parse.enrich_citations(
            [doc],
            cache_path=Path("/tmp/unused.db"),
            use_doi=True,
            skip_content_neg=True,
        )
    assert captured.get("skip") is True


def test_put_overwrites_negative_with_positive(tmp_path):
    # Regression for the INSERT OR REPLACE semantic: a negative row
    # must not permanently block a later successful resolution.
    cache_path = tmp_path / ".citestore.db"
    with DOICache(cache_path) as cache:
        cache.put("10.1000/g", {}, source="not-found")
        cache.put("10.1000/g", {"title": "Now Found", "authors": ["G."]},
                  source="crossref")
    # Only one row exists, and it carries the positive data.
    conn = sqlite3.connect(cache_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM works WHERE doi = ?", ("10.1000/g",),
        ).fetchone()[0]
        source = conn.execute(
            "SELECT source FROM works WHERE doi = ?", ("10.1000/g",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1
    assert source == "crossref"
