"""Tests for wikify.api: Bundle and Corpus open/properties."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import Bundle, Corpus

# --- Bundle.open ---------------------------------------------------------


def test_bundle_open_with_state_json(tmp_path: Path) -> None:
    """run/state.json is the canonical bundle marker."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    bundle = Bundle.open(tmp_path)
    assert bundle.root == tmp_path


def test_bundle_open_rejects_bare_run_dir(tmp_path: Path) -> None:
    """A run/ directory without state.json is not a complete bundle."""
    (tmp_path / "run").mkdir()
    with pytest.raises(FileNotFoundError):
        Bundle.open(tmp_path)


def test_bundle_open_rejects_directory_without_run(tmp_path: Path) -> None:
    """An empty directory is not a bundle."""
    with pytest.raises(FileNotFoundError):
        Bundle.open(tmp_path)


def test_bundle_open_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Bundle.open(tmp_path / "does-not-exist")


def test_bundle_paths_are_under_root(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    bundle = Bundle.open(tmp_path)
    assert bundle.run_dir == tmp_path / "run"
    assert bundle.state_path == tmp_path / "run" / "state.json"
    assert bundle.events_path == tmp_path / "run" / "events.jsonl"
    assert bundle.wiki_dir == tmp_path / "wiki"
    assert bundle.derived_dir == tmp_path / "derived"


def test_bundle_ensure_creates_subdirs(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("{}")
    bundle = Bundle.open(tmp_path)
    bundle.ensure()
    for sub in ("run", "run/io", "work", "work/inbox", "work/concepts",
                "wiki", "wiki/articles", "wiki/people", "derived"):
        assert (tmp_path / sub).is_dir(), f"missing {sub}"


def test_bundle_dataclass_construction_skips_check(tmp_path: Path) -> None:
    """`run init` constructs Bundle(root=...) directly before state.json exists.

    The strict check is on `Bundle.open`; the dataclass itself does not
    enforce the marker so the bootstrap path can wire up paths before
    materialising state.
    """
    bundle = Bundle(root=tmp_path)
    assert bundle.run_dir == tmp_path / "run"


# --- Corpus.open ---------------------------------------------------------


def test_corpus_open_returns_dataclass(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    c = Corpus.open(corpus_dir)
    assert c.root == corpus_dir


def test_corpus_open_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Corpus.open(tmp_path / "missing")


def test_corpus_paths_are_under_root(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "c"
    corpus_dir.mkdir()
    c = Corpus.open(corpus_dir)
    assert c.markdown_dir == corpus_dir / "markdown"
    assert c.sqlite_path == corpus_dir / "wikify.db"
    assert c.manifest_path == corpus_dir / "manifest.json"
