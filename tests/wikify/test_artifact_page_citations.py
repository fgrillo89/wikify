"""Tests for data-artifact footnote label cleaning in render_artifact_markdown.

Verifies that raw canonical chunk ids and doc-hash fragments are stripped
from rendered reference LABELS so only human-readable source labels appear.
When the raw doc_id contains a hash (needed for cross-page linking) it is
preserved in parentheses after the clean label, matching the alternate
evidence format ``<label> (<raw_doc_id>) > "quote"``.
"""

from __future__ import annotations

import re

from wikify.data.artifact_page import _clean_source_label, render_artifact_markdown
from wikify.data.consolidate import ConsolidatedTable

# Regex that matches the LABEL portion of a footnote line (before ' > "').
# Captures text between ']:' and '> "' to check the visible label only.
_LABEL_RE = re.compile(r"^\[\^[^\]]+\]:\s*(.*?)\s*>\s*\"", re.MULTILINE)
_RE_CHUNK_SUFFIX = re.compile(r"__c\d+_[0-9a-f]+")

# Realistic doc_id / chunk_id fixtures that reproduce the F33/F32 leak.
_DOC_ID_SAHU = (
    "[2023 Sahu] Linear and symmetric synaptic weight update in oxide_4dbfd151d2dc"
)
_CHUNK_ID_SAHU = (
    "[2023 Sahu] Linear and symmetric synaptic weight update in oxide"
    "_4dbfd151d2dc__c0006_f39949f4"
)


def _make_table(
    evidence: list[dict], rows: list[dict] | None = None
) -> ConsolidatedTable:
    """Build a minimal ConsolidatedTable for render testing."""
    return ConsolidatedTable(
        artifact_id="test-artifact",
        title="Test Artifact Table",
        description="",
        columns=["Value"],
        property_keys=["value"],
        rows=rows or [],
        evidence=evidence,
        claim_ids=[ev.get("claim_id", ev["marker"]) for ev in evidence],
        n_conflicts=0,
    )


def _extract_labels(md: str) -> list[str]:
    """Return the label part (before ' > "') of every footnote line."""
    return [m.group(1).strip() for m in _LABEL_RE.finditer(md)]


# ---------------------------------------------------------------------------
# _clean_source_label unit tests
# ---------------------------------------------------------------------------


def test_clean_strips_chunk_suffix_and_doc_hash() -> None:
    result = _clean_source_label(_CHUNK_ID_SAHU)
    assert "__c" not in result
    assert "_4dbfd151d2dc" not in result
    assert "weight update in oxide" in result


def test_clean_strips_doc_hash_only() -> None:
    result = _clean_source_label(_DOC_ID_SAHU)
    assert "_4dbfd151d2dc" not in result
    assert "weight update in oxide" in result


def test_clean_author_year_only() -> None:
    """Title-less id like [2022 Ismail]_65456d1402fa -> [2022 Ismail]."""
    raw = "[2022 Ismail]_65456d1402fa"
    result = _clean_source_label(raw)
    assert result == "[2022 Ismail]"


def test_clean_no_fragments_unchanged() -> None:
    """A plain doc_id without hash or chunk suffix is returned unchanged."""
    assert _clean_source_label("doc1") == "doc1"


def test_clean_empty_string() -> None:
    assert _clean_source_label("") == ""


# ---------------------------------------------------------------------------
# render_artifact_markdown: no chunk suffix leakage anywhere
# ---------------------------------------------------------------------------


def test_render_no_chunk_suffix_anywhere() -> None:
    """__cNNNN_<hex> must never appear anywhere in the rendered markdown."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": _DOC_ID_SAHU,
            "chunk_id": _CHUNK_ID_SAHU,
            "locator": "Table 1",
            "quote": "The weight update was linear and symmetric.",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    assert not _RE_CHUNK_SUFFIX.search(md), f"chunk suffix leaked into output:\n{md}"


def test_render_label_has_no_doc_hash() -> None:
    """The visible label (before the '> "' separator) must not contain hashes.

    The raw doc_id may appear inside parentheses after the label to preserve
    cross-page link matching -- that part is NOT the visible label.
    """
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": _DOC_ID_SAHU,
            "chunk_id": _CHUNK_ID_SAHU,
            "locator": "Table 1",
            "quote": "The weight update was linear and symmetric.",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    labels = _extract_labels(md)
    assert labels, "no footnote lines found in output"
    # The visible label (before the trailing raw_doc_id in parens, if any)
    # must not start with or directly expose the hash.
    label = labels[0]
    # Strip any trailing " (raw_doc_id)" parenthetical.
    clean_part = re.sub(r"\s*\([^)]+\)\s*$", "", label)
    assert "_4dbfd151d2dc" not in clean_part, (
        f"doc hash in visible label: {clean_part!r}"
    )
    assert "weight update in oxide" in clean_part


def test_render_no_chunk_suffix_in_label() -> None:
    """The visible label must not contain the chunk suffix."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": "paper_2020_abc123def456",
            "chunk_id": "paper_2020_abc123def456__c0000_deadbeef1234",
            "locator": "",
            "quote": "GPC was 1.1 A/cycle.",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    assert not _RE_CHUNK_SUFFIX.search(md), f"chunk suffix leaked:\n{md}"
    labels = _extract_labels(md)
    clean_part = re.sub(r"\s*\([^)]+\)\s*$", "", labels[0])
    assert "__c" not in clean_part


def test_render_clean_title_present_in_label() -> None:
    """The human-readable title (without hash) must appear in the label."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": _DOC_ID_SAHU,
            "chunk_id": _CHUNK_ID_SAHU,
            "locator": "Table 1",
            "quote": "The weight update was linear and symmetric.",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    labels = _extract_labels(md)
    clean_part = re.sub(r"\s*\([^)]+\)\s*$", "", labels[0])
    assert "weight update in oxide" in clean_part
    assert "Table 1" in clean_part


def test_render_quote_preserved_verbatim() -> None:
    """The exact quote text must appear unchanged in the references block."""
    quote_text = "The weight update was linear and symmetric across 1e6 cycles."
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": "[2023 Sahu] Some title_4dbfd151d2dc",
            "chunk_id": "[2023 Sahu] Some title_4dbfd151d2dc__c0003_abcdef123456",
            "locator": "",
            "quote": quote_text,
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    assert quote_text in md


def test_render_multiple_evidence_entries() -> None:
    """Multiple entries each get a clean label and verbatim quote."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": "[2023 Sahu] Oxide device_4dbfd151d2dc",
            "chunk_id": "[2023 Sahu] Oxide device_4dbfd151d2dc__c0006_f39949f4",
            "locator": "Table 1",
            "quote": "First quote here.",
        },
        {
            "marker": "d2",
            "claim_id": "cid2",
            "doc_id": "[2022 Ismail]_65456d1402fa",
            "chunk_id": "[2022 Ismail]_65456d1402fa__c0001_aabbccdd1234",
            "locator": "",
            "quote": "Second quote here.",
        },
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    # Chunk suffix must never appear anywhere
    assert not _RE_CHUNK_SUFFIX.search(md), f"chunk suffix leaked:\n{md}"
    # Both quotes present verbatim
    assert "First quote here." in md
    assert "Second quote here." in md
    # Labels are clean (no chunk suffix in visible part)
    for label in _extract_labels(md):
        clean = re.sub(r"\s*\([^)]+\)\s*$", "", label)
        assert not _RE_CHUNK_SUFFIX.search(clean), f"chunk suffix in label: {clean!r}"
    # Author+year-only case: [2022 Ismail] survives stripping
    assert "[2022 Ismail]" in md


def test_render_fallback_to_chunk_id_when_doc_id_empty() -> None:
    """When doc_id is empty, chunk_id (stripped) is used as the label."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": "",
            "chunk_id": "fallback_title_abc123def456__c0000_deadbeef1234",
            "locator": "",
            "quote": "Fallback quote.",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    assert not _RE_CHUNK_SUFFIX.search(md), f"chunk suffix leaked:\n{md}"
    assert "fallback_title" in md
    assert "Fallback quote." in md


def test_render_locator_present_in_label() -> None:
    """Locator appears in the visible label part alongside the clean title."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": "[2023 Sahu] Paper title_4dbfd151d2dc",
            "chunk_id": "[2023 Sahu] Paper title_4dbfd151d2dc__c0000_aabbccddeeef",
            "locator": "Table 2",
            "quote": "Some measurement value.",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    assert not _RE_CHUNK_SUFFIX.search(md), f"chunk suffix leaked:\n{md}"
    labels = _extract_labels(md)
    clean_part = re.sub(r"\s*\([^)]+\)\s*$", "", labels[0])
    assert "Paper title" in clean_part
    assert "Table 2" in clean_part


def test_render_plain_doc_id_no_parens() -> None:
    """When doc_id has no hash to strip, no extra parens should appear."""
    evidence = [
        {
            "marker": "d1",
            "claim_id": "cid1",
            "doc_id": "doc1",
            "chunk_id": "c1",
            "locator": "",
            "quote": "GPC was 1.1",
        }
    ]
    table = _make_table(evidence)
    md = render_artifact_markdown(table)
    # No redundant parenthetical because doc_id == clean_label
    assert "(doc1)" not in md
    assert 'doc1 > "GPC was 1.1"' in md
