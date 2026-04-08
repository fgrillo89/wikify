"""The only file allowed to talk to the Claude Code subagent dispatcher.

Strategies never import this module. The CLI wires it into a run when
``--binding claude_code`` is passed. The binding writes a request file at
a well-known path, blocks for a matching response file, validates the
JSON against ``agents/schema.py``, deducts from the cost meter, and
consults the extraction cache for extract calls so cache hits are
zero-token and never spawn a subagent.

Architecturally this file is the *only* place vendor-specific dispatch
lives. ``scripts/check_no_vendor_imports.py`` enforces that no other file
references the dispatcher or imports the anthropic SDK.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from ..agents.protocols import Extractor, Orchestrator, Querier, Writer
from ..agents.schema import (
    ExtractRequest,
    ExtractResponse,
    OrchAction,
    OrchState,
    QueryRequest,
    QueryResponse,
    WriteRequest,
    WriteResponse,
    validate_extract_response,
    validate_orch_action,
    validate_query_response,
    validate_write_response,
)
from ..infra.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from ..infra.cost_meter import CostMeter
from ..infra.role import Role, response_reserve, total_context

_DISPATCH_TIMEOUT = 600.0
_POLL_INTERVAL = 0.25
_REQ_DIR_ENV = "WIKIFY_SIMPLE_DISPATCH_DIR"


def _dispatch_dir() -> Path:
    return Path(os.environ.get(_REQ_DIR_ENV, "data/dispatch"))


def _write_request(role: str, payload: dict) -> tuple[Path, Path]:
    base = _dispatch_dir() / role
    base.mkdir(parents=True, exist_ok=True)
    rid = uuid.uuid4().hex[:12]
    req = base / f"{rid}.request.json"
    res = base / f"{rid}.response.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    return req, res


def _await_response(res: Path) -> dict:
    deadline = time.monotonic() + _DISPATCH_TIMEOUT
    while time.monotonic() < deadline:
        if res.exists():
            data = res.read_text(encoding="utf-8")
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                # incomplete write; brief retry
                time.sleep(_POLL_INTERVAL)
                continue
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"no response at {res}")


def _retry_validate(parser, raw: dict):
    try:
        return parser(raw)
    except Exception:
        return parser(raw)  # one retry, then propagate


# --- extractor -----------------------------------------------------------


class ClaudeCodeExtractor(Extractor):
    def __init__(self, cache: ExtractCache, meter: CostMeter) -> None:
        self._cache = cache
        self._meter = meter

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        key = ExtractCacheKey(
            model_id=request.model_id,
            prompt_hash=prompt_hash(request.prompt_template),
            chunk_id=request.chunk_id,
        )

        def compute() -> CachedExtract:
            req_path, res_path = _write_request(
                "extract",
                {
                    "chunk_id": request.chunk_id,
                    "chunk_text": request.chunk_text,
                    "canonical_titles": request.canonical_titles,
                    "prompt_template": request.prompt_template,
                    "tier": request.tier,
                    "model_id": request.model_id,
                },
            )
            raw = _await_response(res_path)
            response = _retry_validate(validate_extract_response, raw)
            return CachedExtract(
                payload={
                    "chunk_id": response.chunk_id,
                    "concepts": [
                        {"title": c.title, "aliases": c.aliases, "kind": c.kind, "quote": c.quote}
                        for c in response.concepts
                    ],
                },
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
            )

        t0 = time.monotonic()
        entry, was_hit = self._cache.get_or_extract(key, compute)
        wall = time.monotonic() - t0
        self._meter.record(
            role=Role.EXTRACTOR,
            tier=request.tier,
            input_tokens=entry.tokens_in,
            output_tokens=entry.tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=wall,
            cache_hit=was_hit,
            prompt_hash=key.prompt_hash,
        )
        payload = entry.payload
        from ..agents.schema import ExtractedConcept

        return ExtractResponse(
            chunk_id=payload["chunk_id"],
            concepts=[ExtractedConcept(**c) for c in payload["concepts"]],
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
        )


# --- writer --------------------------------------------------------------


class ClaudeCodeWriter(Writer):
    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def write(self, request: WriteRequest) -> WriteResponse:
        req_path, res_path = _write_request(
            "write",
            {
                "page_id": request.page_id,
                "page_kind": request.page_kind,
                "title": request.title,
                "aliases": request.aliases,
                "skeleton": request.skeleton,
                "evidence": [
                    {
                        "chunk_id": e.chunk_id,
                        "doc_id": e.doc_id,
                        "quote": e.quote,
                        "locator": e.locator,
                    }
                    for e in request.evidence
                ],
                "neighbor_titles": request.neighbor_titles,
                "prompt_template": request.prompt_template,
                "tier": request.tier,
                "model_id": request.model_id,
            },
        )
        t0 = time.monotonic()
        raw = _await_response(res_path)
        response = _retry_validate(validate_write_response, raw)
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=response.tokens_in,
            output_tokens=response.tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return response


# --- orchestrator --------------------------------------------------------


class ClaudeCodeOrchestrator(Orchestrator):
    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def step(self, state: OrchState) -> OrchAction:
        req_path, res_path = _write_request(
            "orchestrate",
            {
                "run_id": state.run_id,
                "n_pages": state.n_pages,
                "n_candidates": state.n_candidates,
                "last_actions": state.last_actions,
            },
        )
        t0 = time.monotonic()
        raw = _await_response(res_path)
        action = _retry_validate(validate_orch_action, raw)
        self._meter.record(
            role=Role.ORCHESTRATOR,
            tier="L",
            input_tokens=action.tokens_in,
            output_tokens=action.tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash="orchestrator",
        )
        return action


# --- querier -------------------------------------------------------------

QUERY_PROMPT = "wikify_simple/query/v1"


class ClaudeCodeQuerier(Querier):
    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def answer(self, request: QueryRequest) -> QueryResponse:
        req_path, res_path = _write_request(
            "query",
            {
                "question": request.question,
                "evidence": [
                    {
                        "page_id": ev.page_id,
                        "page_title": ev.page_title,
                        "body_excerpt": ev.body_excerpt,
                        "citations": ev.citations,
                    }
                    for ev in request.evidence
                ],
                "prompt_template": request.prompt_template,
                "model_id": request.model_id,
                "tier": request.tier,
            },
        )
        t0 = time.monotonic()
        raw = _await_response(res_path)
        response = _retry_validate(validate_query_response, raw)
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=int(raw.get("tokens_in", 0)),
            output_tokens=int(raw.get("tokens_out", 0)),
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return response
