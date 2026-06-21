"""Tests for fallback-figure deduplication across pages in one render run.

Verifies that ``_inject_fallback_figure`` respects ``used_figure_ids``:
- Two pages sharing an evidence doc get DIFFERENT fallback figures.
- A page gets no figure when the only candidate is already used.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from wikify.render.html.render import (
    _body_has_figure,
    _figure_alt_text,
    _inject_fallback_figure,
)

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeImg:
    id: str
    caption: str
    path: str
    label: str | None = None
    width: int | None = 800
    height: int | None = 600


class _FakeImageIndex:
    """Minimal ImageIndex stand-in for tests."""

    def __init__(self, corpus_root: Path, docs: dict[str, list[_FakeImg]]) -> None:
        self.corpus_root = corpus_root
        self.by_doc: dict[str, list[_FakeImg]] = docs

    def for_doc(self, doc_id: str) -> list[_FakeImg]:
        return list(self.by_doc.get(doc_id, []))


def _make_page(doc_ids: list[str]) -> Any:
    """Return a mock Page whose evidence records reference the given doc_ids."""
    page = MagicMock()
    evidence = []
    for did in doc_ids:
        ev = MagicMock()
        ev.doc_id = did
        evidence.append(ev)
    page.evidence = evidence
    return page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def corpus_dir(tmp_path: Path) -> Path:
    """Create two real image files so the injector's src.is_file() check passes."""
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_000.png").write_bytes(b"\x89PNG\r\n")
    (figs / "fig_001.png").write_bytes(b"\x89PNG\r\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _call_inject(
    corpus_dir: Path,
    doc_id: str,
    img: _FakeImg,
    used_figure_ids: "set[str]",
) -> str:
    """Run _inject_fallback_figure for one page against a single-doc index."""
    idx = _FakeImageIndex(
        corpus_root=corpus_dir,
        docs={doc_id: [img]},
    )
    page = _make_page([doc_id])
    body = "First paragraph.\n\nRest of the body."
    return _inject_fallback_figure(
        body,
        page=page,
        out_dir=corpus_dir,
        image_index=idx,
        page_url_depth=1,
        used_figure_ids=used_figure_ids,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_two_pages_shared_doc_get_different_figures(corpus_dir: Path) -> None:
    """Two pages sharing a high-rank evidence doc should receive different
    fallback figures when the doc has at least two captioned figures."""
    img_a = _FakeImg(
        id="doc_abc/fig_000",
        caption="Schematic of the deposition chamber.",
        path="figures/fig_000.png",
    )
    img_b = _FakeImg(
        id="doc_abc/fig_001",
        caption="Cross-section TEM of the deposited film.",
        path="figures/fig_001.png",
    )
    idx = _FakeImageIndex(
        corpus_root=corpus_dir,
        docs={"doc_abc": [img_a, img_b]},
    )
    used: set[str] = set()
    body = "First paragraph.\n\nRest."
    page_a = _make_page(["doc_abc"])
    page_b = _make_page(["doc_abc"])

    html_a = _inject_fallback_figure(
        body,
        page=page_a,
        out_dir=corpus_dir,
        image_index=idx,
        page_url_depth=1,
        used_figure_ids=used,
    )
    html_b = _inject_fallback_figure(
        body,
        page=page_b,
        out_dir=corpus_dir,
        image_index=idx,
        page_url_depth=1,
        used_figure_ids=used,
    )

    # Both pages got a figure injected.
    assert "<figure" in html_a
    assert "<figure" in html_b

    # The two figures are different.
    assert "fig_000" in html_a or "fig_001" in html_a
    assert "fig_000" in html_b or "fig_001" in html_b
    # They must NOT share the same image file.
    assert html_a != html_b, "Both pages received the same fallback figure"


def test_page_gets_no_figure_when_only_candidate_is_used(corpus_dir: Path) -> None:
    """When the only candidate figure is already in used_figure_ids, the page
    should receive no fallback (body returned unchanged)."""
    img = _FakeImg(
        id="doc_abc/fig_000",
        caption="Schematic of the deposition chamber.",
        path="figures/fig_000.png",
    )
    # Mark the only candidate as already used.
    used: set[str] = {"doc_abc/fig_000"}

    result = _call_inject(corpus_dir, "doc_abc", img, used)

    # Body must be unchanged — no <figure> injected.
    assert "<figure" not in result


def test_used_figure_ids_updated_after_selection(corpus_dir: Path) -> None:
    """After a successful injection the chosen id is added to used_figure_ids."""
    img = _FakeImg(
        id="doc_abc/fig_000",
        caption="Schematic.",
        path="figures/fig_000.png",
    )
    used: set[str] = set()
    _call_inject(corpus_dir, "doc_abc", img, used)
    assert "doc_abc/fig_000" in used


def test_figure_alt_text_is_human_readable() -> None:
    """Alt text prefers a label, falls back to a bounded caption, then a
    generic string -- never the raw figure id (useless to screen readers)."""
    # Label wins.
    assert _figure_alt_text("Device schematic", "long caption here") == "Device schematic"
    # No label -> caption, whitespace collapsed.
    assert _figure_alt_text("", "Cross-section\n  TEM image") == "Cross-section TEM image"
    # Long caption is truncated.
    long = "x" * 300
    out = _figure_alt_text("", long)
    assert out.endswith("...") and len(out) <= 163
    # Nothing -> generic.
    assert _figure_alt_text("", "") == "Figure"


def test_body_has_figure_detects_html_and_markdown() -> None:
    """The fallback guard must recognize both a writer-embedded HTML
    ``<figure>`` (from the selected-figure placeholder pass) and a
    markdown image, so the fallback never doubles up on a page that
    already shows a figure."""
    html_fig = 'Prose.\n\n<figure class="wiki-figure"><img src="x.png"></figure>\n\nMore.'
    md_fig = "Prose.\n\n![A caption](x.png)\n\nMore."
    no_fig = "Prose with no figure at all.\n\nMore prose."

    assert _body_has_figure(html_fig) is True
    assert _body_has_figure(md_fig) is True
    assert _body_has_figure(no_fig) is False


def test_none_used_figure_ids_behaves_as_before(corpus_dir: Path) -> None:
    """Passing used_figure_ids=None preserves the original behavior (no dedup)."""
    img = _FakeImg(
        id="doc_abc/fig_000",
        caption="Schematic.",
        path="figures/fig_000.png",
    )
    idx = _FakeImageIndex(corpus_root=corpus_dir, docs={"doc_abc": [img]})
    page = _make_page(["doc_abc"])
    body = "First paragraph.\n\nRest."

    result = _inject_fallback_figure(
        body,
        page=page,
        out_dir=corpus_dir,
        image_index=idx,
        page_url_depth=1,
        used_figure_ids=None,
    )
    assert "<figure" in result
