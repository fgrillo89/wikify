"""End-to-end tests for wikify draft + wikify bundle against tests/fixtures/tiny/.

These tests exercise the full skill-path chain short of the model-calling
subagent: session init -> kg seeds -> kg evidence -> draft write-request
-> (synthetic valid response) -> validate write -> bundle commit-page.
The subagent step is simulated by writing a canned valid WriteResponse to
scratch so the rest of the path can be exercised without a real model.
"""

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
    corpus_root = tmp_path_factory.mktemp("corpus")
    ingest_corpus(FIXTURE, corpus_root)
    return corpus_root


@pytest.fixture
def initialized_session(tmp_path: Path, tiny_corpus: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    init = runner.invoke(
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
    assert init.exit_code == 0, init.output
    session_path = Path(json.loads(init.output)["session_path"])

    # Seed a planned page entry in the session so the draft step finds it.
    patch = {"pages": [{"page_id": "ALD", "status": "planned"}]}
    upd = runner.invoke(
        app, ["session", "update", "--session", str(session_path), "--patch", json.dumps(patch)]
    )
    assert upd.exit_code == 0, upd.output
    return session_path, bundle


def test_draft_write_request_builds_scratch_artifact(
    initialized_session: tuple[Path, Path],
) -> None:
    session_path, bundle = initialized_session
    # Pick any chunk_id from the tiny corpus by running kg evidence.
    ev = runner.invoke(
        app,
        [
            "kg",
            "evidence",
            "--session",
            str(session_path),
            "--page-id",
            "ALD",
            "--top-k",
            "3",
        ],
    )
    assert ev.exit_code == 0, ev.output
    chunk_ids = json.loads(ev.output)["chunk_ids"]
    assert chunk_ids, "expected evidence chunks from the tiny corpus"

    result = runner.invoke(
        app,
        [
            "draft",
            "write-request",
            "--session",
            str(session_path),
            "--page-id",
            "ALD",
            "--chunk-ids",
            json.dumps(chunk_ids),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    draft_path = Path(payload["draft_path"])
    assert draft_path.exists()
    draft_data = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft_data["schema_version"] == 1
    assert draft_data["page_id"] == "ALD"
    assert len(draft_data["evidence_v2"]) == len(chunk_ids)
    for ev_entry in draft_data["evidence_v2"]:
        # Source grounding requires chunk_text to be present.
        assert ev_entry["chunk_text"], "evidence_v2 entries must carry chunk_text"

    # Session must have recorded draft_path on the page entry.
    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    ald = next(p for p in session_doc["pages"] if p["page_id"] == "ALD")
    assert ald["status"] == "drafted"
    assert ald["draft_path"] == str(draft_path)


def test_bundle_commit_page_writes_markdown_and_updates_session(
    initialized_session: tuple[Path, Path],
) -> None:
    session_path, bundle = initialized_session
    # Seed evidence + draft as in the previous test.
    ev = runner.invoke(
        app,
        ["kg", "evidence", "--session", str(session_path), "--page-id", "ALD", "--top-k", "2"],
    )
    chunk_ids = json.loads(ev.output)["chunk_ids"]
    runner.invoke(
        app,
        [
            "draft",
            "write-request",
            "--session",
            str(session_path),
            "--page-id",
            "ALD",
            "--chunk-ids",
            json.dumps(chunk_ids),
        ],
    )

    # Simulate a valid subagent response.
    scratch = BundlePaths(bundle).scratch_dir
    response_path = scratch / "response-ALD.json"
    filler = (
        "Atomic layer deposition (ALD) is a self-limiting vapor-phase technique. "
        "Films grow one atomic layer per cycle through alternating precursor pulses. "
    ) * 10
    response_body = (
        "**ALD** is a self-limiting vapor-phase technique.[^e1]\n\n"
        f"{filler}\n\n"
        "## Mechanism\n\n"
        f"{filler}\n\n"
        "## Applications\n\n"
        f"{filler}\n\n"
        "## References\n\n"
        '[^e1]: chunk_x (doc_x) > "self-limiting"\n'
    )
    response_path.write_text(
        json.dumps(
            {
                "page_id": "ALD",
                "page_kind": "article",
                "body_markdown": response_body,
                "used_markers": ["e1"],
                "tokens_in": 100,
                "tokens_out": 50,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "bundle",
            "commit-page",
            "--session",
            str(session_path),
            "--response",
            str(response_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    page_path = Path(payload["page_path"])
    assert page_path.exists()
    assert page_path.read_text(encoding="utf-8").startswith("---\n")

    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    ald = next(p for p in session_doc["pages"] if p["page_id"] == "ALD")
    assert ald["status"] == "committed"


def test_bundle_commit_page_rebuilds_index_and_graph(
    initialized_session: tuple[Path, Path],
) -> None:
    session_path, bundle = initialized_session
    ev = runner.invoke(
        app,
        ["kg", "evidence", "--session", str(session_path), "--page-id", "ALD", "--top-k", "2"],
    )
    chunk_ids = json.loads(ev.output)["chunk_ids"]
    runner.invoke(
        app,
        [
            "draft",
            "write-request",
            "--session",
            str(session_path),
            "--page-id",
            "ALD",
            "--chunk-ids",
            json.dumps(chunk_ids),
        ],
    )

    scratch = BundlePaths(bundle).scratch_dir
    response_path = scratch / "response-ALD.json"
    filler = "ALD is a self-limiting vapor-phase technique. " * 40
    response_path.write_text(
        json.dumps(
            {
                "page_id": "ALD",
                "page_kind": "article",
                "body_markdown": (
                    "**ALD** is self-limiting.[^e1]\n\n"
                    f"{filler}\n\n## Mechanism\n\n{filler}\n\n"
                    "## Applications\n\n"
                    f"{filler}\n\n## References\n\n"
                    '[^e1]: chunk_x (doc_x) > "self-limiting"\n'
                ),
                "used_markers": ["e1"],
                "tokens_in": 100,
                "tokens_out": 50,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "bundle",
            "commit-page",
            "--session",
            str(session_path),
            "--response",
            str(response_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Index + graph must now exist on disk.
    assert Path(payload["index_path"]).exists()
    assert Path(payload["graph_path"]).exists()


def test_bundle_commit_page_no_partial_write_on_lock_held(
    initialized_session: tuple[Path, Path],
) -> None:
    """Acquiring the lock BEFORE the page write means lock_held leaves no partial state."""
    session_path, bundle = initialized_session
    scratch = BundlePaths(bundle).scratch_dir
    scratch.mkdir(parents=True, exist_ok=True)
    response_path = scratch / "response-NEW.json"
    filler = "New page filler prose. " * 80
    response_path.write_text(
        json.dumps(
            {
                "page_id": "NEW",
                "page_kind": "article",
                "body_markdown": (
                    "**NEW** lead.[^e1]\n\n"
                    f"{filler}\n\n## Section\n\n{filler}\n\n## More\n\n{filler}\n\n"
                    "## References\n\n"
                    '[^e1]: chunk_x (doc_x) > "claim"\n'
                ),
                "used_markers": ["e1"],
                "tokens_in": 10,
                "tokens_out": 5,
            }
        ),
        encoding="utf-8",
    )

    # Someone else holds the lock.
    lock_result = runner.invoke(
        app,
        ["session", "lock", "--session", str(session_path), "--owner", "other"],
    )
    assert lock_result.exit_code == 0

    commit = runner.invoke(
        app,
        [
            "bundle",
            "commit-page",
            "--session",
            str(session_path),
            "--response",
            str(response_path),
        ],
    )
    assert commit.exit_code == 2

    # No NEW.md should have been written.
    new_article = BundlePaths(bundle).articles_dir / "NEW.md"
    assert not new_article.exists(), "partial page write leaked under lock_held"


def test_bundle_commit_page_rejects_failed_validation(
    initialized_session: tuple[Path, Path],
) -> None:
    session_path, bundle = initialized_session
    scratch = BundlePaths(bundle).scratch_dir
    scratch.mkdir(parents=True, exist_ok=True)
    response_path = scratch / "response-X.json"
    # Structurally-valid response so Pydantic doesn't reject the commit
    # before the verdict gate fires.
    filler = "Sentence with evidence.[^e1] Follow-up prose. " * 50
    response_path.write_text(
        json.dumps(
            {
                "page_id": "X",
                "page_kind": "article",
                "body_markdown": (
                    "**X** lead.[^e1]\n\n"
                    f"{filler}\n\n"
                    "## Details\n\n"
                    f"{filler}\n\n"
                    "## More\n\n"
                    f"{filler}\n\n"
                    "## References\n\n"
                    '[^e1]: chunk_x (doc_x) > "claim"\n'
                ),
                "used_markers": ["e1"],
                "tokens_in": 10,
                "tokens_out": 5,
            }
        ),
        encoding="utf-8",
    )
    verdict_path = scratch / "validation-X.json"
    verdict_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ok": False,
                "page_id": "X",
                "response_path": str(response_path),
                "errors": [{"path": "e", "code": "quote_not_in_source", "message": "fabricated"}],
                "structural_checks": {"pydantic": "pass", "quote_in_source": "fail"},
                "checked_at": "2026-04-24T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "bundle",
            "commit-page",
            "--session",
            str(session_path),
            "--response",
            str(response_path),
            "--validation",
            str(verdict_path),
        ],
    )
    assert result.exit_code != 0
