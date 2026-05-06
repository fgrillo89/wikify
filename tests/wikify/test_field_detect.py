"""Tests for distill.field_detect."""

from pathlib import Path

from wikify.api import Corpus
from wikify.corpus.field_detect import (
    _field_cache_path,
    detect_field,
    detect_field_scores,
)
from wikify.corpus.store import Store


def _make_corpus(tmp_path: Path, topics: list[str]) -> Corpus:
    root = tmp_path / "corpus"
    root.mkdir()
    corpus = Corpus(root=root)
    store = Store(corpus.sqlite_path)
    try:
        store.con.execute("DELETE FROM topics")
        store.con.executemany(
            "INSERT INTO topics(topic, declared) VALUES (?, 1)",
            [(t,) for t in topics],
        )
        store.con.commit()
    finally:
        store.close()
    return corpus


def test_materials_science_topics_detected(tmp_path: Path) -> None:
    corpus = _make_corpus(
        tmp_path,
        [
            "Atomic Layer Deposition",
            "HfO2 dielectric",
            "Resistive Switching",
            "TiN electrode",
            "XPS characterization",
            "XRD patterns",
            "Precursor chemistry",
            "Thin film growth",
            "Deposition temperature",
            "Crystal structure",
        ],
    )
    scores = detect_field_scores(corpus)
    assert scores, "expected some scores"
    # materials_science should appear near the top.
    top_names = [n for n, _ in scores[:3]]
    assert "materials_science" in top_names


def test_generic_fallback_on_unsignalled_corpus(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path, ["Foo", "Bar", "Baz"])
    assert detect_field(corpus) == "generic"


def test_field_cache_written_and_read(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path, ["Foo", "Bar"])
    first = detect_field(corpus)
    cache = _field_cache_path(corpus)
    assert cache.exists()
    assert cache.read_text(encoding="utf-8").strip() == first
    # Manually poison the cache; detect_field should honour the cached value.
    cache.write_text("physics", encoding="utf-8")
    assert detect_field(corpus) == "physics"


def test_missing_topics_returns_generic(tmp_path: Path) -> None:
    root = tmp_path / "empty_corpus"
    root.mkdir()
    corpus = Corpus(root=root)
    assert detect_field(corpus) == "generic"
