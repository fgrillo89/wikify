"""Tests for wikify extract canonicalize against tests/fixtures/tiny/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wikify.cli import app
from wikify.ingest.pipeline import ingest_corpus
from wikify.paths import BundlePaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"
runner = CliRunner()


@pytest.fixture(scope="module")
def tiny_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    corpus_root = tmp_path_factory.mktemp("corpus-extract")
    ingest_corpus(FIXTURE, corpus_root)
    return corpus_root


def _init(tmp_path: Path, corpus: Path) -> Path:
    bundle = tmp_path / "bundle"
    init = runner.invoke(
        app,
        [
            "session",
            "init",
            "--bundle",
            str(bundle),
            "--corpus",
            str(corpus),
            "--strategy",
            "baseline",
        ],
    )
    assert init.exit_code == 0, init.output
    return Path(json.loads(init.output)["session_path"])


def _seed_extract_response(
    session_path: Path,
    chunk_id: str,
    concepts: list[dict],
) -> Path:
    bundle_root = Path(json.loads(session_path.read_text(encoding="utf-8"))["bundle_root"])
    scratch = BundlePaths(bundle_root).scratch_dir
    scratch.mkdir(parents=True, exist_ok=True)
    out = scratch / f"extract-{chunk_id}.json"
    out.write_text(
        json.dumps(
            {
                "chunk_id": chunk_id,
                "concepts": concepts,
                "tokens_in": 100,
                "tokens_out": 50,
            }
        ),
        encoding="utf-8",
    )
    return out


def test_canonicalize_creates_planned_page_entries(tmp_path: Path, tiny_corpus: Path) -> None:
    session_path = _init(tmp_path, tiny_corpus)
    seeds = runner.invoke(
        app,
        ["kg", "seeds", "--session", str(session_path), "--max-seeds", "1"],
    )
    seed_chunk_id = json.loads(seeds.output)["seed_chunk_ids"][0]

    response = _seed_extract_response(
        session_path,
        seed_chunk_id,
        concepts=[
            {
                "title": "Atomic Layer Deposition",
                "aliases": ["ALD"],
                "kind": "article",
                "quote": "Atomic layer deposition",
                "category": "method",
                "confidence": "extracted",
                "score": 1.0,
                "definition": (
                    "Atomic layer deposition is a self-limiting vapor-phase technique "
                    "that grows films one atomic layer at a time through alternating "
                    "precursor pulses, enabling sub-nanometer thickness control."
                ),
                "summary": (
                    "This chunk introduces ALD as a method whose self-limiting surface "
                    "chemistry produces conformal thin films with atomic-level accuracy."
                ),
            },
            {
                "title": "Stuart Parkin",
                "aliases": [],
                "kind": "person",
                "quote": "Atomic layer deposition",
                "category": None,
                "confidence": "extracted",
                "score": 1.0,
            },
        ],
    )

    result = runner.invoke(
        app,
        [
            "extract",
            "canonicalize",
            "--session",
            str(session_path),
            "--responses",
            json.dumps([str(response)]),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["n_canonical_pages"] == 2
    assert payload["kinds"] == {"article": 1, "person": 1}

    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    page_ids = {p["page_id"] for p in session_doc["pages"]}
    assert any("ald" in pid.lower() or "atomic" in pid.lower() for pid in page_ids)
    person_entries = [p for p in session_doc["pages"] if p["kind"] == "person"]
    assert len(person_entries) == 1
    assert person_entries[0]["status"] == "planned"


def test_canonicalize_is_idempotent(tmp_path: Path, tiny_corpus: Path) -> None:
    session_path = _init(tmp_path, tiny_corpus)
    seeds = runner.invoke(
        app,
        ["kg", "seeds", "--session", str(session_path), "--max-seeds", "1"],
    )
    seed_chunk_id = json.loads(seeds.output)["seed_chunk_ids"][0]
    response = _seed_extract_response(
        session_path,
        seed_chunk_id,
        concepts=[
            {
                "title": "Atomic Layer Deposition",
                "aliases": ["ALD"],
                "kind": "article",
                "quote": "Atomic layer deposition",
                "category": "method",
                "confidence": "extracted",
                "score": 1.0,
                "definition": (
                    "Atomic layer deposition is a self-limiting vapor-phase technique "
                    "that grows films one atomic layer at a time."
                ),
                "summary": (
                    "This chunk introduces ALD as a method whose self-limiting surface "
                    "chemistry produces conformal films."
                ),
            },
        ],
    )
    args = [
        "extract",
        "canonicalize",
        "--session",
        str(session_path),
        "--responses",
        json.dumps([str(response)]),
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == 0
    assert second.exit_code == 0

    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    page_ids = [p["page_id"] for p in session_doc["pages"]]
    assert len(page_ids) == len(set(page_ids)), "canonicalize must not duplicate page_ids"


def test_canonicalize_rejects_unknown_chunk(tmp_path: Path, tiny_corpus: Path) -> None:
    session_path = _init(tmp_path, tiny_corpus)
    response = _seed_extract_response(
        session_path,
        "no-such-chunk",
        concepts=[
            {
                "title": "X",
                "aliases": [],
                "kind": "article",
                "quote": "x",
                "category": None,
                "confidence": "extracted",
                "score": 1.0,
                "definition": "x" * 100,
                "summary": "x" * 100,
            },
        ],
    )
    result = runner.invoke(
        app,
        [
            "extract",
            "canonicalize",
            "--session",
            str(session_path),
            "--responses",
            json.dumps([str(response)]),
        ],
    )
    assert result.exit_code != 0
