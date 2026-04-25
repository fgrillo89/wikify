"""Tests for wikify.bundle.work.card — work.md parser/serialiser."""

from __future__ import annotations

from pathlib import Path

from wikify.api import Bundle
from wikify.bundle.work.card import (
    WorkCard,
    create_concept,
    list_concept_slugs,
    load_card,
    save_card,
    slugify,
)


def _v2(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle.open(tmp_path)


def test_slugify_basic() -> None:
    assert slugify("Atomic Layer Deposition") == "atomic-layer-deposition"
    assert slugify("ALD: Self-limiting!") == "ald-self-limiting"
    assert slugify("") == "untitled"


def test_workcard_parse_full() -> None:
    text = """---
page_id: Atomic Layer Deposition
kind: article
status: active
aliases:
  - ALD
needs_refine: false
---

# Atomic Layer Deposition

## Working Summary
Self-limiting vapor-phase film growth.
"""
    card = WorkCard.parse(text)
    assert card.page_id == "Atomic Layer Deposition"
    assert card.kind == "article"
    assert card.status == "active"
    assert card.aliases == ["ALD"]
    assert card.needs_refine is False
    assert "Working Summary" in card.body


def test_workcard_parse_no_frontmatter() -> None:
    text = "Just body text, no frontmatter.\n"
    card = WorkCard.parse(text)
    assert card.front == {}
    assert card.body == text


def test_workcard_serialise_roundtrip() -> None:
    card = WorkCard(
        front={"page_id": "ALD", "kind": "article", "aliases": ["a"]},
        body="# ALD\n\nbody.\n",
    )
    text = card.serialise()
    parsed = WorkCard.parse(text)
    assert parsed.front == card.front
    assert parsed.body.strip() == card.body.strip()


def test_save_and_load(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    card = WorkCard(front={"page_id": "ALD", "kind": "article"}, body="body\n")
    save_card(bundle, "ald", card)
    loaded = load_card(bundle, "ald")
    assert loaded.page_id == "ALD"
    assert "body" in loaded.body


def test_load_card_missing_returns_empty(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    assert load_card(bundle, "no-such").front == {}


def test_create_concept_creates_directory(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    slug, card = create_concept(
        bundle, page_id="Atomic Layer Deposition", aliases=["ALD"]
    )
    assert slug == "atomic-layer-deposition"
    assert card.kind == "article"
    assert card.aliases == ["ALD"]
    assert (bundle.work_concepts_dir / slug / "work.md").is_file()


def test_create_concept_idempotent(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    slug1, _ = create_concept(bundle, page_id="ALD")
    slug2, _ = create_concept(bundle, page_id="ALD")
    assert slug1 == slug2


def test_list_concept_slugs_sorted(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    create_concept(bundle, page_id="Beta")
    create_concept(bundle, page_id="Alpha")
    create_concept(bundle, page_id="Gamma")
    assert list_concept_slugs(bundle) == ["alpha", "beta", "gamma"]


def test_list_concept_slugs_empty(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    assert list_concept_slugs(bundle) == []
