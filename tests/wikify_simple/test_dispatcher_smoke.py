"""End-to-end smoke test for ``bindings/file_dispatch.py``.

Spawns a daemon thread that impersonates the Claude Code subagent
dispatcher: it watches the request directory for any ``*.request.json``
file, reads it, and writes a deterministic valid response next to it.
The three bindings (extractor, writer, querier) are then driven against
this fake dispatcher and the results are asserted against the pydantic
schemas. This is the first automated coverage of the binding's request
and response file protocol.
"""

import json
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from wikify_simple.bindings.file_dispatch import (
    FileDispatchExtractor,
    FileDispatchQuerier,
    FileDispatchWriter,
)
from wikify_simple.contracts.schema import (
    ExtractRequest,
    ExtractResponse,
    QueryEvidence,
    QueryRequest,
    QueryResponse,
    WriteEvidenceRef,
    WriteRequest,
    WriteResponse,
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
        # Quote MUST be a verbatim substring of the request's
        # chunk_text or the binding's _assert_quotes_in_chunk check
        # raises QuoteNotInChunkError.
        chunk_text = payload["chunk_text"]
        # Pick a quote guaranteed to be a substring: the first 5..80
        # characters of chunk_text (schema requires len in [5, 400]).
        quote = chunk_text[: max(5, min(80, len(chunk_text)))]
        if len(quote) < 5:
            quote = (chunk_text + "xxxxx")[:5]
        return {
            "chunk_id": payload["chunk_id"],
            "concepts": [
                {
                    "title": "Atomic Layer Deposition",
                    "aliases": ["ALD"],
                    "kind": "article",
                    "quote": quote,
                    "category": "method",
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
                "# ALD\n\n"
                "## Definition\n\n"
                "Atomic layer deposition is a vapor-phase thin-film growth "
                "technique used to build conformal coatings on complex "
                "surfaces.\n\n"
                "## Background\n\n"
                "Atomic layer deposition emerged from work on molecular "
                "layer epitaxy in the 1970s and was refined for industrial "
                "use over subsequent decades[^e1]. Early reports framed it "
                "as a route to conformal coatings on complex geometries[^e1]. "
                "The technique gained traction across semiconductor "
                "manufacturing and catalysis research over the following "
                "years[^e1].\n\n"
                "## Mechanism / Process\n\n"
                "ALD proceeds through self-limiting half-reactions[^e1].\n\n"
                "Each half-reaction saturates the surface before the next "
                "pulse[^e1].\n\n"
                "The cycle repeats to grow films one atomic layer at a "
                "time[^e1].\n\n"
                "Growth depends on cycle count rather than exposure "
                "time[^e1].\n\n"
                "## Applications\n\n"
                "ALD coats high-aspect-ratio structures in memory and "
                "logic devices[^e1]. It enables conformal catalyst layers "
                "in heterogeneous catalysis[^e1]. Recent corpus sources "
                "discuss its role in neuromorphic memristor "
                "fabrication[^e1]. Multiple sources span this area.\n\n"
                "## Open Questions\n\n"
                "The corpus does not address how ALD scales to wafer-level "
                "memristor manufacturing.\n\n"
                "## References\n\n"
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
    extractor = FileDispatchExtractor(cache, meter, dispatch_dir=dispatcher)

    req = ExtractRequest(
        chunk_id="chunk-1",
        chunk_text="Atomic layer deposition is a self-limiting process.",
        canonical_titles=[],
        prompt_template="wikify_simple/extract",
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
    writer = FileDispatchWriter(meter, dispatch_dir=dispatcher)

    req = WriteRequest(
        page_id="p1",
        page_kind="article",
        title="Atomic Layer Deposition",
        aliases=["ALD"],
        skeleton="# ALD\n",
        evidence=[
            WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="self-limiting", locator=""),
        ],
        prompt_template="wikify_simple/write",
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
    querier = FileDispatchQuerier(meter, dispatch_dir=dispatcher)

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
        prompt_template="wikify_simple/query",
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
    extractor = FileDispatchExtractor(cache, meter, dispatch_dir=dispatcher)

    req = ExtractRequest(
        chunk_id="chunk-cached",
        chunk_text="same text",
        canonical_titles=[],
        prompt_template="wikify_simple/extract",
        model_id="claude-haiku",
        tier="S",
    )
    r1 = extractor.extract(req)
    r2 = extractor.extract(req)
    assert r1.concepts[0].title == r2.concepts[0].title
    _no_residual_files(dispatcher)
