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

Dispatch directory resolution:
  1. explicit ``dispatch_dir`` passed to the binding constructor
  2. the ``WIKIFY_SIMPLE_DISPATCH_DIR`` environment variable
  3. ``data/dispatch`` under CWD
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..agents.protocols import Extractor, Orchestrator, Querier, Writer
from ..agents.schema import (
    ExtractRequest,
    ExtractResponse,
    OrchAction,
    OrchState,
    QueryRequest,
    QueryResponse,
    QuoteNotInChunkError,
    WriteRequest,
    WriteResponse,
)
from ..agents.text_normalize import normalize_for_substring
from ..infra.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from ..infra.cost_meter import CostMeter
from ..infra.role import Role, response_reserve, total_context

_DISPATCH_TIMEOUT = 600.0
_POLL_INTERVAL = 0.25
_REQ_DIR_ENV = "WIKIFY_SIMPLE_DISPATCH_DIR"


def resolve_dispatch_dir(explicit: Path | str | None = None) -> Path:
    """Return the dispatch root directory.

    Order: explicit arg > ``WIKIFY_SIMPLE_DISPATCH_DIR`` env var > ``data/dispatch``.
    """
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get(_REQ_DIR_ENV, "data/dispatch"))


def _write_request(dispatch_dir: Path, role: str, payload: dict) -> tuple[Path, Path]:
    base = dispatch_dir / role
    base.mkdir(parents=True, exist_ok=True)
    rid = uuid.uuid4().hex[:12]
    req = base / f"{rid}.request.json"
    res = base / f"{rid}.response.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    return req, res


def _await_response(res: Path, *, timeout: float = _DISPATCH_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
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


def _cleanup(*paths: Path) -> None:
    for p in paths:
        with contextlib.suppress(FileNotFoundError, OSError):
            p.unlink()


def _write_error_artifact(req_path: Path, model_cls: type, raw, exc: Exception) -> Path:
    """Persist a debuggable rejection record next to the request file.

    On validation (or post-validation binding-check) failure we want the
    operator to inspect what the dispatcher produced, so we write a
    sibling ``<rid>.error.json`` with the error message, the raw dict,
    and the schema name. The request file is intentionally kept; only
    the response file is cleaned up by the caller's ``finally`` block.
    """
    err_path = req_path.with_name(req_path.name.replace(".request.", ".error."))
    try:
        payload = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "schema": model_cls.__name__,
            "raw": raw,
        }
        err_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return err_path


def _assert_quotes_in_chunk(
    response: ExtractResponse,
    chunk_text: str,
    req_path: Path,
    raw,
) -> None:
    """Structural barrier against hallucinated paraphrased quotes.

    Schemas don't see ``chunk_text``, so the substring rule lives in the
    binding. If any extracted quote is not a verbatim (whitespace-
    tolerant) substring of the source chunk we raise
    ``QuoteNotInChunkError`` and drop a ``.error.json`` artifact so the
    operator can see which concept the dispatcher hallucinated.
    """
    normalized_chunk = normalize_for_substring(chunk_text)
    for concept in response.concepts:
        q = concept.quote.strip()
        normalized_q = normalize_for_substring(q)
        if not normalized_q or normalized_q not in normalized_chunk:
            exc = QuoteNotInChunkError(
                title=concept.title,
                quote_prefix=q[:60],
            )
            _write_error_artifact(req_path, ExtractResponse, raw, exc)
            raise exc


def _validate_or_record(
    model_cls: type[BaseModel],
    raw,
    req_path: Path,
):
    """Validate ``raw`` against ``model_cls``; on failure write .error.json and re-raise.

    There is no retry: re-validating the same dict against the same
    schema cannot succeed. The dead ``_retry_validate`` helper that used
    to live here was removed intentionally — if the model returns bad
    JSON, the right response is to fail loudly with an artifact on disk,
    not to burn another validation pass.
    """
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        _write_error_artifact(req_path, model_cls, raw, exc)
        raise


# --- extractor -----------------------------------------------------------


class ClaudeCodeExtractor(Extractor):
    BINDING_NAME = "claude_code"

    def __init__(
        self,
        cache: ExtractCache,
        meter: CostMeter,
        *,
        dispatch_dir: Path | str | None = None,
    ) -> None:
        self._cache = cache
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        key = ExtractCacheKey(
            binding_name=self.BINDING_NAME,
            model_id=request.model_id,
            prompt_hash=prompt_hash(request.prompt_template),
            chunk_id=request.chunk_id,
        )

        def compute() -> CachedExtract:
            req_path, res_path = _write_request(
                self._dispatch_dir,
                "extract",
                {
                    "chunk_id": request.chunk_id,
                    "chunk_text": request.chunk_text,
                    "canonical_titles": request.canonical_titles,
                    "prompt_template": request.prompt_template,
                    "tier": request.tier,
                    "model_id": request.model_id,
                    "images_for_doc": [
                        {
                            "id": im.id,
                            "label": im.label,
                            "caption": im.caption,
                            "page": im.page,
                            "path": im.path,
                        }
                        for im in request.images_for_doc
                    ],
                },
            )
            try:
                raw = _await_response(res_path)
                response = _validate_or_record(ExtractResponse, raw, req_path)
                _assert_quotes_in_chunk(response, request.chunk_text, req_path, raw)
            finally:
                _cleanup(res_path)
                # On success there is no error artifact and we also
                # drop the request file. On validation or quote-check
                # failure _write_error_artifact has already run and
                # we intentionally keep the request file for the
                # operator to inspect.
                if not req_path.with_name(req_path.name.replace(".request.", ".error.")).exists():
                    _cleanup(req_path)
            return CachedExtract(
                payload={
                    "chunk_id": response.chunk_id,
                    "concepts": [
                        {
                            "title": c.title,
                            "aliases": list(c.aliases),
                            "kind": c.kind,
                            "quote": c.quote,
                            "category": c.category,
                            "confidence": c.confidence,
                            "score": c.score,
                        }
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

        def _concept_kwargs(c: dict) -> dict:
            # category was added in slice 6; older cached payloads may
            # omit it. Default to None rather than failing the lookup.
            return {
                "title": c["title"],
                "aliases": c["aliases"],
                "kind": c["kind"],
                "quote": c["quote"],
                "category": c.get("category"),
                "confidence": c.get("confidence", "extracted"),
                "score": c.get("score", 1.0),
            }

        return ExtractResponse(
            chunk_id=payload["chunk_id"],
            concepts=[ExtractedConcept(**_concept_kwargs(c)) for c in payload["concepts"]],
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
        )


# --- writer --------------------------------------------------------------


class ClaudeCodeWriter(Writer):
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def write(self, request: WriteRequest) -> WriteResponse:
        req_path, res_path = _write_request(
            self._dispatch_dir,
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
                "figures": [
                    {
                        "id": f.id,
                        "label": f.label,
                        "caption": f.caption,
                        "page": f.page,
                        "path": f.path,
                    }
                    for f in request.figures
                ],
            },
        )
        t0 = time.monotonic()
        try:
            raw = _await_response(res_path)
            response = _validate_or_record(WriteResponse, raw, req_path)
        finally:
            _cleanup(res_path)
            if not req_path.with_name(req_path.name.replace(".request.", ".error.")).exists():
                _cleanup(req_path)
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
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def step(self, state: OrchState) -> OrchAction:
        req_path, res_path = _write_request(
            self._dispatch_dir,
            "orchestrate",
            {
                "run_id": state.run_id,
                "n_pages": state.n_pages,
                "n_candidates": state.n_candidates,
                "last_actions": state.last_actions,
            },
        )
        t0 = time.monotonic()
        try:
            raw = _await_response(res_path)
            action = _validate_or_record(OrchAction, raw, req_path)
        finally:
            _cleanup(res_path)
            if not req_path.with_name(req_path.name.replace(".request.", ".error.")).exists():
                _cleanup(req_path)
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
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def answer(self, request: QueryRequest) -> QueryResponse:
        req_path, res_path = _write_request(
            self._dispatch_dir,
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
        try:
            raw = _await_response(res_path)
            response = _validate_or_record(QueryResponse, raw, req_path)
            tokens_in = int(raw.get("tokens_in", 0))
            tokens_out = int(raw.get("tokens_out", 0))
        finally:
            _cleanup(res_path)
            if not req_path.with_name(req_path.name.replace(".request.", ".error.")).exists():
                _cleanup(req_path)
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return response
