"""Tests for short-handle resolution in corpus.handles."""

from __future__ import annotations

import pytest

from wikify.corpus.handles import (
    AmbiguousHandleError,
    HandleNotFoundError,
    format_chunk_handles,
    format_handle,
    resolve,
    short_id,
)


def test_short_id_strips_hex_suffix() -> None:
    full = "[2011 Yang] Dopant Control..._5f92b0389ccd"
    assert short_id(full) == "5f92b0389ccd"


def test_short_id_passthrough_no_suffix() -> None:
    assert short_id("paper_0") == "paper_0"


def test_short_id_only_long_hex_counts() -> None:
    # Trailing 7 hex chars is below the 8-char heuristic — pass through.
    assert short_id("foo_abcdef0") == "foo_abcdef0"
    assert short_id("foo_abcdef01") == "abcdef01"


def test_resolve_exact_wins() -> None:
    cands = ["paper_0", "paper_1", "paper_0_extra"]
    assert resolve("paper_0", cands) == "paper_0"


def test_resolve_short_hash() -> None:
    cands = [
        "[2011 Yang] Dopant..._5f92b0389ccd",
        "[2024 Gou] Optimization..._329efcf68938",
    ]
    assert resolve("5f92b0389ccd", cands) == cands[0]
    assert resolve("329efcf68938", cands) == cands[1]


def test_resolve_underscore_suffix() -> None:
    cands = ["paper_alpha", "paper_beta"]
    assert resolve("alpha", cands) == "paper_alpha"


def test_resolve_loose_suffix() -> None:
    cands = ["paper_0__c0001__abc", "paper_0__c0002__def"]
    assert resolve("abc", cands) == cands[0]


def test_resolve_ambiguous_raises() -> None:
    cands = ["foo_alpha", "bar_alpha"]
    with pytest.raises(AmbiguousHandleError) as exc:
        resolve("alpha", cands)
    assert set(exc.value.matches) == {"foo_alpha", "bar_alpha"}


def test_resolve_not_found_raises() -> None:
    with pytest.raises(HandleNotFoundError):
        resolve("nope", ["paper_0", "paper_1"])


def test_format_handle_short_default() -> None:
    full = "[2011 Yang]..._5f92b0389ccd"
    assert format_handle("doc", full) == "doc:5f92b0389ccd"
    assert format_handle("doc", full, long=True) == f"doc:{full}"


def test_format_handle_no_suffix() -> None:
    assert format_handle("doc", "paper_0") == "doc:paper_0"


def test_short_id_compound_id_shortens_doc_part() -> None:
    """Figure ids of the form ``<doc-id>/<stem>`` shorten the doc portion only."""
    assert (
        short_id("[2011 Yang] Dopant Control..._5f92b0389ccd/Figure_01")
        == "5f92b0389ccd/Figure_01"
    )


def test_short_id_compound_id_no_doc_hash() -> None:
    assert short_id("paper_0/fig_001") == "paper_0/fig_001"


def test_resolve_compound_short_via_loose_suffix() -> None:
    """Short figure handles match full ids via loose-suffix tier."""
    cands = [
        "[2011 Yang]_5f92b0389ccd/Figure_01",
        "[2024 Gou]_329efcf68938/Figure_01",
    ]
    assert resolve("5f92b0389ccd/Figure_01", cands) == cands[0]


def test_format_handle_compound_short() -> None:
    full = "[1971 Chua]_514791d621fa/fig_002"
    assert format_handle("figure", full) == "figure:514791d621fa/fig_002"


def test_format_chunk_handles_unique_uses_bare_short() -> None:
    """When chunk shorts don't collide, emit the bare ``chunk:<short>``."""
    rows = [
        ("doc_a_111111111111__c0000__aaaaaaaa", "doc_a_111111111111"),
        ("doc_b_222222222222__c0000__bbbbbbbb", "doc_b_222222222222"),
    ]
    out = format_chunk_handles(rows)
    assert out["doc_a_111111111111__c0000__aaaaaaaa"] == "chunk:aaaaaaaa"
    assert out["doc_b_222222222222__c0000__bbbbbbbb"] == "chunk:bbbbbbbb"


def test_format_chunk_handles_collision_namespaces_by_doc() -> None:
    """Two chunks sharing the same short suffix escalate to compound form."""
    rows = [
        ("doc_a_111111111111__c0000__deadbeef", "doc_a_111111111111"),
        ("doc_b_222222222222__c0009__deadbeef", "doc_b_222222222222"),
    ]
    out = format_chunk_handles(rows)
    assert out["doc_a_111111111111__c0000__deadbeef"] == "chunk:111111111111/deadbeef"
    assert out["doc_b_222222222222__c0009__deadbeef"] == "chunk:222222222222/deadbeef"


def test_format_chunk_handles_no_doc_id_falls_back_to_full() -> None:
    """No doc_id available means we cannot namespace; emit the full id."""
    rows = [
        ("aaa__c0__deadbeef", ""),
        ("bbb__c0__deadbeef", ""),
    ]
    out = format_chunk_handles(rows)
    assert out["aaa__c0__deadbeef"] == "chunk:aaa__c0__deadbeef"
    assert out["bbb__c0__deadbeef"] == "chunk:bbb__c0__deadbeef"
