"""Tests for wikify.api: Bundle, LegacyBundle, Corpus, layout detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import (
    Bundle,
    Corpus,
    LayoutMismatchError,
    LegacyBundle,
    _detect_layout,
)

# --- Layout detection ----------------------------------------------------


def test_detect_layout_v2_state_json(tmp_path: Path) -> None:
    """run/state.json is the canonical v2 marker."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    assert _detect_layout(tmp_path) == "v2"


def test_detect_layout_v2_run_dir_only(tmp_path: Path) -> None:
    """A bare run/ directory is treated as v2 (mid-init)."""
    (tmp_path / "run").mkdir()
    assert _detect_layout(tmp_path) == "v2"


def test_detect_layout_v1_session_dir(tmp_path: Path) -> None:
    (tmp_path / "_session").mkdir()
    assert _detect_layout(tmp_path) == "v1"


def test_detect_layout_v1_calls_jsonl(tmp_path: Path) -> None:
    (tmp_path / "_calls.jsonl").write_text("")
    assert _detect_layout(tmp_path) == "v1"


def test_detect_layout_v1_run_json(tmp_path: Path) -> None:
    (tmp_path / "_run.json").write_text("{}")
    assert _detect_layout(tmp_path) == "v1"


def test_detect_layout_unknown_for_empty_dir(tmp_path: Path) -> None:
    assert _detect_layout(tmp_path) == "unknown"


def test_detect_layout_v2_wins_over_v1(tmp_path: Path) -> None:
    """A bundle mid-migration with both markers is reported as v2."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    (tmp_path / "_session").mkdir()
    assert _detect_layout(tmp_path) == "v2"


# --- Bundle.open (v2) ----------------------------------------------------


def test_bundle_open_succeeds_on_v2(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    b = Bundle.open(tmp_path)
    assert b.root == tmp_path
    assert b.state_path == tmp_path / "run" / "state.json"
    assert b.events_path == tmp_path / "run" / "events.jsonl"
    assert b.lock_path == tmp_path / "run" / "lock"
    assert b.work_dir == tmp_path / "work"
    assert b.work_concept_dir("foo") == tmp_path / "work" / "concepts" / "foo"
    assert b.wiki_articles_dir == tmp_path / "wiki" / "articles"
    assert b.derived_index_path == tmp_path / "derived" / "index.json"


def test_bundle_open_rejects_v1(tmp_path: Path) -> None:
    (tmp_path / "_session").mkdir()
    with pytest.raises(LayoutMismatchError) as exc:
        Bundle.open(tmp_path)
    assert exc.value.expected == "v2"
    assert exc.value.found == "v1"


def test_bundle_open_rejects_unknown(tmp_path: Path) -> None:
    with pytest.raises(LayoutMismatchError) as exc:
        Bundle.open(tmp_path)
    assert exc.value.expected == "v2"
    assert exc.value.found == "unknown"


def test_bundle_open_rejects_nonexistent(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Bundle.open(tmp_path / "does-not-exist")


def test_bundle_ensure_creates_v2_layout(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()  # marker so .open() succeeds
    b = Bundle.open(tmp_path)
    b.ensure()
    for sub in ("run", "run/io", "work", "work/inbox", "work/concepts",
                "wiki", "wiki/articles", "wiki/people", "derived"):
        assert (tmp_path / sub).is_dir(), f"missing: {sub}"


# --- LegacyBundle.open (v1) ---------------------------------------------


def test_legacy_bundle_open_succeeds_on_v1(tmp_path: Path) -> None:
    (tmp_path / "_session").mkdir()
    lb = LegacyBundle.open(tmp_path)
    assert lb.root == tmp_path
    assert lb.session_path == tmp_path / "_session" / "session.json"
    assert lb.calls_path == tmp_path / "_calls.jsonl"
    assert lb.scratch_dir == tmp_path / "_scratch"
    assert lb.articles_dir == tmp_path / "articles"


def test_legacy_bundle_open_succeeds_on_unknown(tmp_path: Path) -> None:
    """LegacyBundle accepts an empty dir — init_session creates one fresh."""
    lb = LegacyBundle.open(tmp_path)
    assert lb.root == tmp_path


def test_legacy_bundle_open_rejects_v2(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    with pytest.raises(LayoutMismatchError) as exc:
        LegacyBundle.open(tmp_path)
    assert exc.value.expected == "v1"
    assert exc.value.found == "v2"


# --- Corpus.open --------------------------------------------------------


def test_corpus_open_succeeds(tmp_path: Path) -> None:
    c = Corpus.open(tmp_path)
    assert c.root == tmp_path
    assert c.vectors_path == tmp_path / "vectors.npz"
    assert c.knowledge_graph_path == tmp_path / "knowledge_graph.json"
    assert c.manifest_path == tmp_path / "manifest.json"


def test_corpus_open_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Corpus.open(tmp_path / "does-not-exist")
