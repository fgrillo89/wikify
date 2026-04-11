"""File-dispatch binding: writes request JSON, polls for response JSON.

Strategies never import this module. The CLI wires it into a run when
``--binding file_dispatch`` is passed. The binding writes a request file at
a well-known path, blocks for a matching response file, validates the
JSON against ``contracts/schema.py``, deducts from the cost meter, and
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

import contextlib
import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..contracts.normalize import normalize_for_substring
from ..contracts.protocols import Compactor, Editor, Extractor, Orchestrator, Querier, Writer
from ..contracts.roles import Role, response_reserve, total_context
from ..contracts.schema import (
    EditorBrief,
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
from ..infra.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from ..infra.config import DISPATCH_TIMEOUT, POLL_INTERVAL
from ..infra.cost_meter import CostMeter

_REQ_DIR_ENV = "WIKIFY_SIMPLE_DISPATCH_DIR"
ModelT = TypeVar("ModelT", bound=BaseModel)


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


def _await_response(res: Path, *, timeout: float = DISPATCH_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if res.exists():
            data = res.read_text(encoding="utf-8")
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                # incomplete write; brief retry
                time.sleep(POLL_INTERVAL)
                continue
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"no response at {res}")


def _cleanup(*paths: Path) -> None:
    for p in paths:
        with contextlib.suppress(FileNotFoundError, OSError):
            p.unlink()


def _error_path(req_path: Path) -> Path:
    return req_path.with_name(req_path.name.replace(".request.", ".error."))


def _write_error_artifact(req_path: Path, model_cls: type, raw, exc: Exception) -> Path:
    """Persist a debuggable rejection record next to the request file.

    On validation (or post-validation binding-check) failure we want the
    operator to inspect what the dispatcher produced, so we write a
    sibling ``<rid>.error.json`` with the error message, the raw dict,
    and the schema name. The request file is intentionally kept; only
    the response file is cleaned up by the caller's ``finally`` block.
    """
    err_path = _error_path(req_path)
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
    model_cls: type[ModelT],
    raw,
    req_path: Path,
) -> ModelT:
    """Validate ``raw`` against ``model_cls``; on failure try to salvage
    (for ExtractResponse) or write .error.json and re-raise.

    For ``ExtractResponse`` specifically, if the failure is in one or
    more ``concepts[i]`` entries, drop the bad concepts and revalidate
    with the clean subset. This means a single bad title no longer
    kills all five concepts in a batch.
    """
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        salvaged = _try_salvage_extract_response(model_cls, raw, exc)
        if salvaged is not None:
            return salvaged  # type: ignore[return-value]
        _write_error_artifact(req_path, model_cls, raw, exc)
        raise


def _try_salvage_extract_response(
    model_cls: type[ModelT],
    raw,
    exc: ValidationError,
) -> ModelT | None:
    """Drop bad ``concepts[i]`` entries from an ExtractResponse and retry.

    Returns the revalidated response on success, or None if the raw is
    not an ExtractResponse, has no concepts list, the errors are not
    concept-local, or the cleaned response still fails.
    """
    if model_cls.__name__ != "ExtractResponse":
        return None
    if not isinstance(raw, dict):
        return None
    concepts = raw.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        return None
    bad_indices: set[int] = set()
    for err in exc.errors():
        loc = err.get("loc", ())
        if len(loc) >= 2 and loc[0] == "concepts" and isinstance(loc[1], int):
            bad_indices.add(loc[1])
    if not bad_indices:
        # Failure wasn't concept-local (e.g. chunk_id missing); no salvage.
        return None
    salvaged_concepts = [c for i, c in enumerate(concepts) if i not in bad_indices]
    if not salvaged_concepts:
        return None  # nothing left worth returning
    salvaged_raw = dict(raw)
    salvaged_raw["concepts"] = salvaged_concepts
    try:
        return model_cls.model_validate(salvaged_raw)
    except ValidationError:
        return None


def _dispatch_raw(dispatch_dir: Path, role: str, payload: dict):
    req_path, res_path = _write_request(dispatch_dir, role, payload)
    try:
        return _await_response(res_path)
    finally:
        _cleanup(res_path)
        if not _error_path(req_path).exists():
            _cleanup(req_path)


def _dispatch_model(
    dispatch_dir: Path,
    role: str,
    payload: dict,
    model_cls: type[ModelT],
    *,
    validate: Callable[[ModelT, dict, Path], None] | None = None,
) -> ModelT:
    response, _ = _dispatch_model_with_raw(
        dispatch_dir,
        role,
        payload,
        model_cls,
        validate=validate,
    )
    return response


def _dispatch_model_with_raw(
    dispatch_dir: Path,
    role: str,
    payload: dict,
    model_cls: type[ModelT],
    *,
    validate: Callable[[ModelT, dict, Path], None] | None = None,
) -> tuple[ModelT, dict]:
    req_path, res_path = _write_request(dispatch_dir, role, payload)
    try:
        raw = _await_response(res_path)
        response = _validate_or_record(model_cls, raw, req_path)
        if validate is not None:
            validate(response, raw, req_path)
        return response, raw
    finally:
        _cleanup(res_path)
        if not _error_path(req_path).exists():
            _cleanup(req_path)


def _record_call(
    meter: CostMeter,
    *,
    role: Role,
    tier: str,
    input_tokens: int,
    output_tokens: int,
    wall_seconds: float,
    cache_hit: bool = False,
    prompt_hash_value: str,
) -> None:
    meter.record(
        role=role,
        tier=tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        context_cap=total_context() - response_reserve(),
        wall_seconds=wall_seconds,
        cache_hit=cache_hit,
        prompt_hash=prompt_hash_value,
    )


# --- extractor -----------------------------------------------------------


class FileDispatchExtractor(Extractor):
    BINDING_NAME = "file_dispatch"

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
            response = _dispatch_model(
                self._dispatch_dir,
                "extract",
                request.model_dump(mode="json"),
                ExtractResponse,
                validate=lambda response, raw, req_path: _assert_quotes_in_chunk(
                    response,
                    request.chunk_text,
                    req_path,
                    raw,
                ),
            )
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
                            "definition": c.definition,
                            "summary": c.summary,
                            "parameters": [p.model_dump() for p in c.parameters],
                            "mechanisms": list(c.mechanisms),
                            "relationships": [r.model_dump() for r in c.relationships],
                            "equations": [eq.model_dump() for eq in c.equations],
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
        _record_call(
            self._meter,
            role=Role.EXTRACTOR,
            tier=request.tier,
            input_tokens=entry.tokens_in,
            output_tokens=entry.tokens_out,
            wall_seconds=wall,
            cache_hit=was_hit,
            prompt_hash_value=key.prompt_hash,
        )
        payload = entry.payload
        from ..contracts.schema import Equation, ExtractedConcept, Parameter, Relationship

        def _concept_kwargs(c: dict) -> dict:
            # Backwards-compatible: older cached payloads may omit v2 fields.
            kwargs = {
                "title": c["title"],
                "aliases": c["aliases"],
                "kind": c["kind"],
                "quote": c["quote"],
                "category": c.get("category"),
                "confidence": c.get("confidence", "extracted"),
                "score": c.get("score", 1.0),
                "definition": c.get("definition", ""),
                "summary": c.get("summary", ""),
            }
            if c.get("parameters"):
                kwargs["parameters"] = [Parameter(**p) for p in c["parameters"]]
            if c.get("mechanisms"):
                kwargs["mechanisms"] = c["mechanisms"]
            if c.get("relationships"):
                kwargs["relationships"] = [Relationship(**r) for r in c["relationships"]]
            if c.get("equations"):
                kwargs["equations"] = [Equation(**eq) for eq in c["equations"]]
            return kwargs

        return ExtractResponse(
            chunk_id=payload["chunk_id"],
            concepts=[ExtractedConcept(**_concept_kwargs(c)) for c in payload["concepts"]],
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
        )


# --- writer --------------------------------------------------------------


class FileDispatchWriter(Writer):
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def write(self, request: WriteRequest) -> WriteResponse:
        t0 = time.monotonic()
        response, raw = _dispatch_model_with_raw(
            self._dispatch_dir,
            "write",
            request.model_dump(mode="json"),
            WriteResponse,
        )
        # Re-validate with page_kind from the originating request so the
        # article-structure check (_check_wikipedia_structure) has the kind.
        if not response.page_kind:
            response = WriteResponse.model_validate(
                {**raw, "page_kind": request.page_kind}
            )
        _record_call(
            self._meter,
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=response.tokens_in,
            output_tokens=response.tokens_out,
            wall_seconds=time.monotonic() - t0,
            prompt_hash_value=prompt_hash(request.prompt_template),
        )
        return response


# --- compactor -----------------------------------------------------------


class FileDispatchCompactor(Compactor):
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def compact(self, page_id: str, title: str, entries: list[dict]) -> dict:
        t0 = time.monotonic()
        raw = _dispatch_raw(
            self._dispatch_dir,
            "compact",
            {"page_id": page_id, "title": title, "entries": entries},
        )
        _record_call(
            self._meter,
            role=Role.COMPACTOR,
            tier="S",
            input_tokens=int(raw.get("tokens_in", 500)),
            output_tokens=int(raw.get("tokens_out", 200)),
            wall_seconds=time.monotonic() - t0,
            prompt_hash_value="compact_v1",
        )
        return raw


# --- editor --------------------------------------------------------------


class FileDispatchEditor(Editor):
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def edit(
        self, page_id: str, title: str, dossier: list[dict], neighbors: list[dict]
    ) -> EditorBrief:
        t0 = time.monotonic()
        brief, raw = _dispatch_model_with_raw(
            self._dispatch_dir,
            "edit",
            {
                "page_id": page_id,
                "title": title,
                "dossier": dossier,
                "neighbors": neighbors,
            },
            EditorBrief,
        )
        _record_call(
            self._meter,
            role=Role.EDITOR,
            tier="M",
            input_tokens=int(raw.get("tokens_in", 500)),
            output_tokens=int(raw.get("tokens_out", 300)),
            wall_seconds=time.monotonic() - t0,
            prompt_hash_value="edit_v1",
        )
        return brief


# --- orchestrator --------------------------------------------------------


class FileDispatchOrchestrator(Orchestrator):
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def step(self, state: OrchState) -> OrchAction:
        t0 = time.monotonic()
        action = _dispatch_model(
            self._dispatch_dir,
            "orchestrate",
            {
                "run_id": state.run_id,
                "n_pages": state.n_pages,
                "n_candidates": state.n_candidates,
                "last_actions": state.last_actions,
            },
            OrchAction,
        )
        _record_call(
            self._meter,
            role=Role.ORCHESTRATOR,
            tier="L",
            input_tokens=action.tokens_in,
            output_tokens=action.tokens_out,
            wall_seconds=time.monotonic() - t0,
            prompt_hash_value="orchestrator",
        )
        return action


# --- persona generator ---------------------------------------------------


def make_persona_complete(
    *,
    dispatch_dir: Path | str | None = None,
) -> Callable[[str], str]:
    """Return a ``complete(prompt) -> str`` callable backed by the dispatcher.

    The callable writes one ``persona/{rid}.request.json`` payload, blocks
    on the matching response file, and returns the ``text`` field. This
    is the only persona-specific dispatch path; ``distill.persona`` stays
    binding-agnostic.
    """
    root = resolve_dispatch_dir(dispatch_dir)

    def _complete(prompt: str) -> str:
        raw = _dispatch_raw(root, "persona", {"prompt": prompt})
        if not isinstance(raw, dict):
            raise ValueError(f"persona dispatch returned non-dict: {raw!r}")
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise ValueError("persona dispatch response missing 'text' field")
        return text

    return _complete


# --- querier -------------------------------------------------------------

QUERY_PROMPT = "wikify_simple/query"


class FileDispatchQuerier(Querier):
    def __init__(self, meter: CostMeter, *, dispatch_dir: Path | str | None = None) -> None:
        self._meter = meter
        self._dispatch_dir = resolve_dispatch_dir(dispatch_dir)

    def answer(self, request: QueryRequest) -> QueryResponse:
        t0 = time.monotonic()
        response = _dispatch_model(
            self._dispatch_dir,
            "query",
            request.model_dump(mode="json"),
            QueryResponse,
        )
        _record_call(
            self._meter,
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=response.tokens_in,
            output_tokens=response.tokens_out,
            wall_seconds=time.monotonic() - t0,
            prompt_hash_value=prompt_hash(request.prompt_template),
        )
        return response
