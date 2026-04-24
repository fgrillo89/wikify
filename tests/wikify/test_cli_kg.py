"""Tests for the wikify kg CLI family against tests/fixtures/tiny/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wikify.cli import app
from wikify.ingest.pipeline import ingest_corpus

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"
runner = CliRunner()


@pytest.fixture(scope="module")
def tiny_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    corpus_root = tmp_path_factory.mktemp("corpus")
    ingest_corpus(FIXTURE, corpus_root)
    return corpus_root


@pytest.fixture
def initialized_session(tmp_path: Path, tiny_corpus: Path) -> Path:
    bundle = tmp_path / "bundle"
    result = runner.invoke(
        app,
        [
            "session",
            "init",
            "--bundle",
            str(bundle),
            "--corpus",
            str(tiny_corpus),
            "--strategy",
            "baseline",
        ],
    )
    assert result.exit_code == 0, result.output
    return Path(json.loads(result.output)["session_path"])


def test_kg_seeds_returns_deterministic_ids(initialized_session: Path) -> None:
    result = runner.invoke(
        app, ["kg", "seeds", "--session", str(initialized_session), "--max-seeds", "3"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload.keys()) == {"seed_doc_ids", "seed_chunk_ids"}
    assert isinstance(payload["seed_doc_ids"], list)
    assert isinstance(payload["seed_chunk_ids"], list)
    assert len(payload["seed_doc_ids"]) <= 3
    # Every abstract chunk id must belong to a selected seed doc; there is
    # at most one abstract per doc, so the ratios hold.
    assert len(payload["seed_chunk_ids"]) <= len(payload["seed_doc_ids"])

    # Deterministic — a second invocation with the same max-seeds returns
    # the same set (order may not matter across runs, but should here
    # because greedy_seed_select is deterministic).
    result2 = runner.invoke(
        app, ["kg", "seeds", "--session", str(initialized_session), "--max-seeds", "3"]
    )
    assert result2.exit_code == 0
    assert json.loads(result2.output) == payload


def test_kg_abstracts_returns_metadata_for_known_docs(
    tiny_corpus: Path, initialized_session: Path
) -> None:
    seeds_result = runner.invoke(
        app, ["kg", "seeds", "--session", str(initialized_session), "--max-seeds", "3"]
    )
    seed_doc_ids = json.loads(seeds_result.output)["seed_doc_ids"]
    assert seed_doc_ids, "expected at least one seed doc from the tiny fixture"

    result = runner.invoke(
        app,
        [
            "kg",
            "abstracts",
            "--corpus",
            str(tiny_corpus),
            "--doc-ids",
            json.dumps(seed_doc_ids),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "abstracts" in payload
    for entry in payload["abstracts"]:
        assert set(entry.keys()) >= {"doc_id", "chunk_id", "text_len"}
        assert isinstance(entry["text_len"], int)


def test_kg_evidence_returns_chunk_ids_without_duplicates(
    initialized_session: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "kg",
            "evidence",
            "--session",
            str(initialized_session),
            "--page-id",
            "Atomic Layer Deposition",
            "--top-k",
            "5",
            "--max-per-source",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["page_id"] == "Atomic Layer Deposition"
    chunk_ids = payload["chunk_ids"]
    assert isinstance(chunk_ids, list)
    assert len(chunk_ids) == len(set(chunk_ids)), "evidence chunks must be deduped"
    assert len(chunk_ids) <= 5


def test_kg_abstracts_rejects_non_array(tiny_corpus: Path) -> None:
    result = runner.invoke(
        app,
        [
            "kg",
            "abstracts",
            "--corpus",
            str(tiny_corpus),
            "--doc-ids",
            json.dumps({"not": "an array"}),
        ],
    )
    assert result.exit_code != 0
