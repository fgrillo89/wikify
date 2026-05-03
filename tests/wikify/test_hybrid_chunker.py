"""Smoke tests for the universal HybridChunker entry point.

The chunker depends on Docling + transformers being available; these
tests exercise the end-to-end shape on a small synthetic markdown
fixture so they stay quick.
"""

from __future__ import annotations

from wikify.ingest.hybrid_chunker import chunk_with_hybrid

_SAMPLE_MD = """# A Memristor Paper

## Abstract

Atomic layer deposition of HfO2 is used to fabricate memristive
crossbars. The fabricated devices show stable resistive switching
across more than 1000 cycles with low cycle-to-cycle variation.

## 1. Introduction

Resistive random-access memory (RRAM) based on transition metal oxides
has emerged as a leading candidate for non-volatile memory. The HfO2
system is among the most studied because of its CMOS compatibility and
the wide tuning range available through doping and stack engineering.

## 2. Methods

Films were grown by ALD using TDMAH and water at 200C, 100 cycles. The
bottom electrode is TiN; the top electrode is Pt. Pulse and purge
times were optimised for self-limiting growth.

## 3. Results

Resistive switching was observed with set voltages near +1.2 V and
reset near -1.0 V. Endurance testing demonstrated stable operation up
to 10^6 cycles on representative devices.
"""


def test_chunk_with_hybrid_returns_chunks() -> None:
    chunks = chunk_with_hybrid("paper_test", _SAMPLE_MD)
    assert chunks, "expected non-empty chunk list"
    assert all(c.doc_id == "paper_test" for c in chunks)
    assert all(c.text for c in chunks)


def test_chunk_with_hybrid_preserves_section_path() -> None:
    chunks = chunk_with_hybrid("paper_test", _SAMPLE_MD)
    # At least one chunk should land inside the abstract section.
    assert any(
        any("abstract" in h.lower() for h in c.section_path)
        for c in chunks
    ), [c.section_path for c in chunks]


def test_chunk_with_hybrid_classifies_section_types() -> None:
    chunks = chunk_with_hybrid("paper_test", _SAMPLE_MD)
    types = {c.section_type for c in chunks}
    # Each section we wrote should map to a non-default type after
    # classification (abstract / introduction / methods / results).
    # Don't assert on each one strictly — Docling may collapse a
    # short section into an adjacent one — but at least 'body' alone
    # is not the only outcome.
    assert types - {"body"}, types


def test_chunk_with_hybrid_assigns_unique_ids() -> None:
    chunks = chunk_with_hybrid("paper_test", _SAMPLE_MD)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunk_with_hybrid_handles_empty_markdown() -> None:
    assert chunk_with_hybrid("empty", "") == []
    assert chunk_with_hybrid("empty", "   \n\n   ") == []


def test_chunk_with_hybrid_drops_obvious_boilerplate() -> None:
    """A chunk that is overwhelmingly publisher license text gets dropped
    by the same hard filter the legacy chunker uses."""
    md = """# Some Paper

## Body

This is a real paragraph about atomic layer deposition of hafnium oxide.

## Boilerplate

Wiley Online Library licensed downloaded from onlinelibrary.wiley.com.
This article is licensed under a Creative Commons license. All rights
reserved. Redistribution prohibited. Copyright owner: Wiley. Licensed
materials downloaded from sciencedirect. Terms and conditions apply.
"""
    chunks = chunk_with_hybrid("bp_test", md)
    # At least one substantive chunk survived.
    assert any("hafnium oxide" in c.text.lower() for c in chunks)
    # No chunk consisted of dominant license language.
    for c in chunks:
        # heuristic: if a chunk has "wiley" AND "licensed" AND "copyright"
        # the legacy hard filter should have dropped it.
        lower = c.text.lower()
        assert not (
            "wiley" in lower and "licensed" in lower and "copyright" in lower
        ), f"chunk passed boilerplate gate: {c.text[:200]!r}"
