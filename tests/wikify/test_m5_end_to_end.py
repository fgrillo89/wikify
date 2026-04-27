"""End-to-end M5 producer test.

Proves the M5 hit-rate path:
- ``corpus show chunk:<id>`` emits a ``chunk_read`` event when run
  from inside a bundle.
- ``wiki commit`` records the same chunk as evidence on a page.
- ``eval`` aggregates both into ``M5_hit_rate.value > 0`` with
  ``n_chunks_read >= 1`` and a non-zero overlap.

The corpus, response payload, and validation gate are reused from
``test_mvp_smoke``; the focus here is the M5 plumbing specifically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.wikify.test_corpus_queries import _make_corpus
from tests.wikify.test_mvp_smoke import _good_response_payload
from wikify.api import Bundle
from wikify.bundle.draft.artifact import response_path
from wikify.cli import app

runner = CliRunner()

PAGE_TITLE = "Atomic Layer Deposition"
PAGE_SLUG = "atomic-layer-deposition"
CHUNK_ID = "paper_0__c0000"
DOC_ID = "paper_0"


def _invoke(*argv: str) -> None:
    result = runner.invoke(app, list(argv))
    assert result.exit_code == 0, (
        f"CLI {' '.join(argv)} failed with exit_code={result.exit_code}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {getattr(result, 'stderr', '')}"
    )


def test_m5_chunk_read_event_yields_nonzero_hit_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root = tmp_path / "bundle"
    corpus = _make_corpus(tmp_path / "corpus")

    _invoke(
        "run", "init",
        "--bundle", str(bundle_root),
        "--corpus", str(corpus.root),
        "--strategy", "baseline",
    )
    bundle = Bundle.open(bundle_root)

    # cd into the bundle so `corpus show` resolves the bundle context
    # from cwd and emits a `chunk_read` event into run/events.jsonl.
    monkeypatch.chdir(bundle_root)
    _invoke(
        "corpus", "show", f"chunk:{CHUNK_ID}",
        "--corpus", str(corpus.root),
        "--full",
    )
    events = [
        json.loads(line)
        for line in bundle.events_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    chunk_read_events = [e for e in events if e["type"] == "chunk_read"]
    assert chunk_read_events, "corpus show chunk:<id> must emit a chunk_read event"
    assert chunk_read_events[0]["chunk_id"] == CHUNK_ID

    # Build the page that cites the same chunk.
    _invoke(
        "work", "add", "concept", PAGE_TITLE,
        "--run", str(bundle_root),
        "--aliases", '["ALD"]',
    )
    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        json.dumps({"chunk_id": CHUNK_ID, "doc_id": DOC_ID}) + "\n",
        encoding="utf-8",
    )
    _invoke(
        "work", "add", "evidence", PAGE_SLUG,
        "--records", str(records_path),
        "--run", str(bundle_root),
    )
    _invoke(
        "draft", "build", PAGE_SLUG,
        "--task", "create",
        "--corpus", str(corpus.root),
        "--model-id", "claude-sonnet-4-6",
        "--tier", "M",
        "--run", str(bundle_root),
    )
    draft_json_path = bundle.work_concept_dir(PAGE_SLUG) / "draft.json"
    draft_payload = json.loads(draft_json_path.read_text(encoding="utf-8"))
    chunk_text = draft_payload["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response_p = response_path(bundle, PAGE_SLUG)
    response_p.write_text(
        json.dumps(_good_response_payload(quote)), encoding="utf-8"
    )
    _invoke("draft", "check", PAGE_SLUG, "--run", str(bundle_root))
    _invoke("wiki", "commit", PAGE_SLUG, "--run", str(bundle_root))

    # Eval consumes events.jsonl + page evidence; M5 must be non-null.
    _invoke("eval", "--bundle", str(bundle_root))
    payload = json.loads((bundle.derived_dir / "eval.json").read_text(encoding="utf-8"))
    m5 = payload["M5_hit_rate"]
    assert m5["value"] is not None, "M5_hit_rate.value must not be null"
    assert m5["n_chunks_read"] >= 1, "expected at least one chunk_read event"
    assert m5["n_chunks_used"] >= 1, "expected at least one evidence chunk on the page"
    assert m5["n_chunks_read_and_used"] >= 1, "the read and used sets must overlap"
    assert m5["value"] > 0.0
