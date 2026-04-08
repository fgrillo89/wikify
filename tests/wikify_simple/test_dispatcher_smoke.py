"""End-to-end smoke test for ``bindings/claude_code.py``.

Spawns a daemon thread that impersonates the Claude Code subagent
dispatcher: it watches the request directory for any ``*.request.json``
file, reads it, and writes a deterministic valid response next to it.
The three bindings (extractor, writer, querier) are then driven against
this fake dispatcher and the results are asserted against the pydantic
schemas. This is the first automated coverage of the binding's request
and response file protocol.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from wikify_simple.agents.schema import (
    ExtractRequest,
    ExtractResponse,
    QueryEvidence,
    QueryRequest,
    QueryResponse,
    WriteEvidenceRef,
    WriteRequest,
    WriteResponse,
)
from wikify_simple.bindings.claude_code import (
    ClaudeCodeExtractor,
    ClaudeCodeQuerier,
    ClaudeCodeWriter,
)
from wikify_simple.infra.cache import ExtractCache
from wikify_simple.infra.cost_meter import CostMeter

# --- fake dispatcher thread ----------------------------------------------


class _FakeDispatcher:
    """Watches ``<root>/<role>/*.request.json`` and writes responses.

    Runs in a daemon thread so pytest can tear down at any time.
    """

    def __init__(self, root: Path, responder: Callable[[str, dict], dict]) -> None:
        self.root = root
        self._responder = responder
        self._stop = threading.Event()
        self._seen: set[Path] = set()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.root.exists():
                for role_dir in self.root.iterdir():
                    if not role_dir.is_dir():
                        continue
                    for req in role_dir.glob("*.request.json"):
                        if req in self._seen:
                            continue
                        try:
                            payload = json.loads(req.read_text(encoding="utf-8"))
                        except (FileNotFoundError, json.JSONDecodeError):
                            continue
                        self._seen.add(req)
                        res = req.with_name(req.name.replace(".request.", ".response."))
                        response = self._responder(role_dir.name, payload)
                        res.write_text(json.dumps(response), encoding="utf-8")
            time.sleep(0.02)


def _responder(role: str, payload: dict) -> dict:
    """Deterministic valid responses for each role."""
    if role == "extract":
        return {
            "chunk_id": payload["chunk_id"],
            "concepts": [
                {
                    "title": "Atomic Layer Deposition",
                    "aliases": ["ALD"],
                    "kind": "concept",
                    "quote": "Atomic layer deposition is a thin-film technique.",
                    "evidence_figures": [],
                }
            ],
            "tokens_in": 123,
            "tokens_out": 45,
        }
    if role == "write":
        return {
            "page_id": payload["page_id"],
            "body_markdown": (
                "ALD is a self-limiting surface reaction process[^e1].\n\n"
                "It produces conformal films one half-cycle at a time[^e1].\n\n"
                "## Evidence\n\n"
                "[^e1]: self-limiting (d1)\n"
            ),
            "used_markers": ["e1"],
            "tokens_in": 400,
            "tokens_out": 250,
        }
    if role == "query":
        return {
            "answer": {
                "text": "ALD enables conformal thin films.",
                "citations": ["p1"],
                "chunks": ["c1"],
                "follow_ups": [],
            },
            "tokens_in": 80,
            "tokens_out": 40,
        }
    raise AssertionError(f"unknown role {role!r}")


@pytest.fixture
def dispatcher(tmp_path):
    root = tmp_path / "dispatch"
    root.mkdir()
    fake = _FakeDispatcher(root, _responder)
    fake.start()
    try:
        yield root
    finally:
        fake.stop()


def _meter(tmp_path: Path) -> CostMeter:
    return CostMeter(
        budget_haiku_eq=1_000_000.0,
        run_id="smoke",
        events_path=tmp_path / "calls.jsonl",
    )


# --- tests ---------------------------------------------------------------


def _no_residual_files(root: Path) -> None:
    """No ``*.request.json`` or ``*.response.json`` should survive a call."""
    leftover = list(root.rglob("*.json"))
    assert leftover == [], f"dispatch files not cleaned up: {leftover}"


def test_extractor_dispatcher_roundtrip(tmp_path, dispatcher):
    meter = _meter(tmp_path)
    cache = ExtractCache(root=tmp_path / "cache")
    extractor = ClaudeCodeExtractor(cache, meter, dispatch_dir=dispatcher)

    req = ExtractRequest(
        chunk_id="chunk-1",
        chunk_text="Atomic layer deposition is a self-limiting process.",
        canonical_titles=[],
        prompt_template="wikify_simple/extract/v1",
        model_id="claude-haiku",
        tier="S",
    )
    before = meter.spent_haiku_eq
    response = extractor.extract(req)
    assert isinstance(response, ExtractResponse)
    assert response.chunk_id == "chunk-1"
    assert response.concepts[0].title == "Atomic Layer Deposition"
    assert response.tokens_in == 123
    assert response.tokens_out == 45
    assert meter.spent_haiku_eq > before
    _no_residual_files(dispatcher)


def test_writer_dispatcher_roundtrip(tmp_path, dispatcher):
    meter = _meter(tmp_path)
    writer = ClaudeCodeWriter(meter, dispatch_dir=dispatcher)

    req = WriteRequest(
        page_id="p1",
        page_kind="concept",
        title="Atomic Layer Deposition",
        aliases=["ALD"],
        skeleton="# ALD\n",
        evidence=[
            WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="self-limiting", locator=""),
        ],
        neighbor_titles=[],
        prompt_template="wikify_simple/write/v1",
        model_id="claude-haiku",
        tier="M",
    )
    before = meter.spent_haiku_eq
    response = writer.write(req)
    assert isinstance(response, WriteResponse)
    assert response.page_id == "p1"
    assert "e1" in response.used_markers
    assert response.tokens_in == 400
    assert response.tokens_out == 250
    assert meter.spent_haiku_eq > before
    _no_residual_files(dispatcher)


def test_querier_dispatcher_roundtrip(tmp_path, dispatcher):
    meter = _meter(tmp_path)
    querier = ClaudeCodeQuerier(meter, dispatch_dir=dispatcher)

    req = QueryRequest(
        question="What is ALD?",
        evidence=[
            QueryEvidence(
                page_id="p1",
                page_title="ALD",
                body_excerpt="ALD is a thin-film method.",
                citations=["ev1"],
            )
        ],
        prompt_template="wikify_simple/query/v1",
        model_id="claude-haiku",
        tier="S",
    )
    before = meter.spent_haiku_eq
    response = querier.answer(req)
    assert isinstance(response, QueryResponse)
    assert response.answer.text == "ALD enables conformal thin films."
    assert response.answer.citations == ["p1"]
    assert meter.spent_haiku_eq > before
    _no_residual_files(dispatcher)


def test_extractor_cache_hit_skips_dispatcher(tmp_path, dispatcher):
    """Second call with same key must be a cache hit and not touch the
    dispatcher (so a torn-down dispatcher would still work)."""
    meter = _meter(tmp_path)
    cache = ExtractCache(root=tmp_path / "cache")
    extractor = ClaudeCodeExtractor(cache, meter, dispatch_dir=dispatcher)

    req = ExtractRequest(
        chunk_id="chunk-cached",
        chunk_text="same text",
        canonical_titles=[],
        prompt_template="wikify_simple/extract/v1",
        model_id="claude-haiku",
        tier="S",
    )
    r1 = extractor.extract(req)
    r2 = extractor.extract(req)
    assert r1.concepts[0].title == r2.concepts[0].title
    _no_residual_files(dispatcher)
