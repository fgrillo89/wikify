"""File-dispatch binding: writes request JSON, polls for response JSON.

Strategies never import this module. The CLI wires a ``Dispatch`` instance
into the pipeline at run time. The binding writes a request file at a
well-known path, blocks for a matching response file, validates the
JSON against ``schema.py``, deducts from the cost meter, and consults the
extraction cache for extract calls so cache hits are zero-token and never
spawn a subagent.

Architecturally this file is the *only* place vendor-specific dispatch
lives.

Dispatch directory resolution:
  1. explicit ``dispatch_dir`` passed to the binding constructor
  2. the ``WIKIFY_DISPATCH_DIR`` environment variable
  3. ``data/dispatch`` under CWD
"""

import contextlib
import json
import os
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from .config import DISPATCH_TIMEOUT, POLL_INTERVAL
from .context import response_reserve, total_context
from .meter import CostMeter
from .schema import (
    EditorBrief,
    Equation,
    ExtractedConcept,
    ExtractRequest,
    ExtractResponse,
    OrchAction,
    OrchState,
    Parameter,
    QueryRequest,
    QueryResponse,
    QuoteNotInChunkError,
    Relationship,
    WriteRequest,
    WriteResponse,
)
from .types import (
    ModelTier,
    Role,
)

# ---------------------------------------------------------------------------
# normalize_for_substring — tolerant text normalization for quote matching
# ---------------------------------------------------------------------------

# All dash variants observed in pymupdf output.
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212-"
_DASH_RE = re.compile(f"[{re.escape(_DASHES)}]")

# Curly / typographic quotes -> straight.
_CURLY_SINGLE = "\u2018\u2019\u201a\u201b"
_CURLY_DOUBLE = "\u201c\u201d\u201e\u201f"

# [12] or [12-15] inline citation markers.
_CITE_RE = re.compile(r"\[\d+(?:-\d+)?\]")

# [word] bracket-wrapping artifact: lowercase ASCII token of length >= 2
# OR a run of digits. We deliberately do NOT match single letters like
# ``[a]`` / ``[b]`` because those are legitimate subfigure refs.
_BRACKET_WRAP_RE = re.compile(r"\[([a-z0-9]{2,})\]")

_WS_RE = re.compile(r"\s+")

# Markdown emphasis markers (`**bold**`, `*italic*`, `_italic_`,
# `__bold__`). The model strips these when emitting a clean quote;
# the raw chunk keeps them. Strip on both sides for comparison.
_MD_EMPHASIS_RE = re.compile(r"[*_]+")

# After dash normalization, collapse whitespace around '-' so
# ``chua - a`` and ``chua-a`` compare equal.
_DASH_WS_RE = re.compile(r"\s*-\s*")


def normalize_for_substring(s: str) -> str:
    """Normalize text for tolerant substring matching against noisy
    PDF-extracted chunks. Preserves the *information* in the string
    while erasing artifacts the model legitimately cleans up.
    """
    # 1. NFKC unicode normalization
    s = unicodedata.normalize("NFKC", s)
    # 2. Dash variants -> ASCII '-'
    s = _DASH_RE.sub("-", s)
    # 3. Curly quotes -> straight
    for ch in _CURLY_SINGLE:
        s = s.replace(ch, "'")
    for ch in _CURLY_DOUBLE:
        s = s.replace(ch, '"')
    # 4. Strip [NN] / [NN-NN] citation markers
    s = _CITE_RE.sub("", s)
    # 5. Unwrap [token] bracket-wrap artifacts (lowercase word/digits, >=2)
    s = _BRACKET_WRAP_RE.sub(r"\1", s)
    # 6. Strip markdown emphasis markers (**bold**, _italic_, etc.)
    s = _MD_EMPHASIS_RE.sub("", s)
    # 7. Collapse whitespace
    s = _WS_RE.sub(" ", s).strip()
    # 8. Collapse whitespace around hyphens
    s = _DASH_WS_RE.sub("-", s)
    # 9. Lowercase
    return s.lower()


# ---------------------------------------------------------------------------
# Dispatch helpers (module-level, stateless)
# ---------------------------------------------------------------------------

_REQ_DIR_ENV = "WIKIFY_DISPATCH_DIR"
ModelT = TypeVar("ModelT", bound=BaseModel)

BINDING_NAME = "file_dispatch"

QUERY_PROMPT = "wikify/query"


def resolve_dispatch_dir(explicit: Path | str | None = None) -> Path:
    """Return the dispatch root directory.

    Order: explicit arg > ``WIKIFY_DISPATCH_DIR`` env var > ``data/dispatch``.
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
    """Persist a debuggable rejection record next to the request file."""
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
    """Structural barrier against hallucinated paraphrased quotes."""
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
    """Drop bad ``concepts[i]`` entries from an ExtractResponse and retry."""
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
        return None
    salvaged_concepts = [c for i, c in enumerate(concepts) if i not in bad_indices]
    if not salvaged_concepts:
        return None
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
    tier: ModelTier | str,
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


# ---------------------------------------------------------------------------
# Persona generator factory (used by cli.py persona-generate command)
# ---------------------------------------------------------------------------


def make_persona_complete(
    *,
    dispatch_dir: Path | str | None = None,
) -> Callable[[str], str]:
    """Return a ``complete(prompt) -> str`` callable backed by the dispatcher."""
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


# ---------------------------------------------------------------------------
# extract_many helpers (cache serialization)
# ---------------------------------------------------------------------------


def _concept_kwargs(c: dict) -> dict:
    """Build kwargs for ExtractedConcept from a cached payload dict."""
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


def _entry_to_response(entry: CachedExtract) -> ExtractResponse:
    payload = entry.payload
    return ExtractResponse(
        chunk_id=payload["chunk_id"],
        concepts=[ExtractedConcept(**_concept_kwargs(c)) for c in payload["concepts"]],
        tokens_in=entry.tokens_in,
        tokens_out=entry.tokens_out,
    )


def _response_to_entry(response: ExtractResponse) -> CachedExtract:
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


# ---------------------------------------------------------------------------
# Dispatch — consolidated file-dispatch binding
# ---------------------------------------------------------------------------


class Dispatch:
    """File-based request/response dispatch. The only place model calls live.

    Satisfies the Extractor, Writer, Compactor, Editor, Orchestrator, and
    Querier protocols via duck typing. Pipeline receives one ``Dispatch``
    object instead of separate bindings.
    """

    def __init__(
        self,
        meter: CostMeter,
        cache: ExtractCache,
        *,
        dispatch_dir: Path | str | None = None,
    ) -> None:
        self._meter = meter
        self._cache = cache
        self._dir = resolve_dispatch_dir(dispatch_dir)
        # Guided-mode context (populated via attach_guided_context)
        self._kg: object | None = None
        self._guided_pages: list = []
        self._budget_target: float = 0.0
        self._tool_schemas: dict | None = None
        self._max_tool_turns: int = 8
        self._snapshot: dict = {}

    def attach_guided_context(
        self,
        *,
        kg: object,
        pages: list,
        budget_target: float,
        tool_schemas: dict | None = None,
        max_tool_turns: int = 8,
    ) -> None:
        """Attach KG and runtime context for guided-mode tool execution."""
        self._kg = kg
        self._guided_pages = pages
        self._budget_target = budget_target
        self._tool_schemas = tool_schemas
        self._max_tool_turns = max_tool_turns

    def update_guided_state(
        self, *, snapshot: dict | None = None, pages: list | None = None
    ) -> None:
        """Update mutable guided-mode state before each orchestrator step."""
        if snapshot is not None:
            self._snapshot = snapshot
        if pages is not None:
            self._guided_pages = pages

    # --- extract -----------------------------------------------------------

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        return self.extract_many([request])[0]

    def extract_many(self, requests: list[ExtractRequest]) -> list[ExtractResponse]:
        """Fire a whole batch of extract requests and collect responses.

        Cache hits short-circuit immediately. Uncached requests are written
        up front, then all their response files are polled in a single loop
        so the dispatcher can handle them concurrently. Cost-meter accounting
        runs serially after all responses are collected.
        """
        keys = [
            ExtractCacheKey(
                binding_name=BINDING_NAME,
                model_id=req.model_id,
                prompt_hash=prompt_hash(req.prompt_template),
                chunk_id=req.chunk_id,
            )
            for req in requests
        ]

        # Probe cache on disk to identify which requests need dispatch.
        cache_root = self._cache._root  # noqa: SLF001
        uncached_set: set[int] = {
            i for i, key in enumerate(keys) if not (cache_root / key.relpath()).exists()
        }

        # Write all uncached request files up front so the dispatcher can
        # begin handling them concurrently before we start polling.
        uncached_order = sorted(uncached_set)
        dispatch_info: list[tuple[Path, Path, ExtractRequest]] = []
        for i in uncached_order:
            req = requests[i]
            req_path, res_path = _write_request(
                self._dir, "extract", req.model_dump(mode="json")
            )
            dispatch_info.append((req_path, res_path, req))

        # Poll all pending response files in a single shared loop.
        deadline = time.monotonic() + DISPATCH_TIMEOUT
        pending: set[int] = set(range(len(dispatch_info)))
        raw_results: list[dict | None] = [None] * len(dispatch_info)
        while pending and time.monotonic() < deadline:
            still_pending: set[int] = set()
            for j in list(pending):
                _, res_path, _ = dispatch_info[j]
                if res_path.exists():
                    data = res_path.read_text(encoding="utf-8")
                    try:
                        raw_results[j] = json.loads(data)
                    except json.JSONDecodeError:
                        still_pending.add(j)  # incomplete write; retry next tick
                else:
                    still_pending.add(j)
            pending = still_pending
            if pending:
                time.sleep(POLL_INTERVAL)

        if pending:
            _, res_path, _ = dispatch_info[next(iter(pending))]
            raise TimeoutError(f"no response at {res_path}")

        # Validate dispatched responses and clean up files.
        validated: dict[int, ExtractResponse] = {}
        errors: dict[int, Exception] = {}
        for j, (req_path, res_path, req) in enumerate(dispatch_info):
            raw = raw_results[j]
            try:
                response = _validate_or_record(ExtractResponse, raw, req_path)
                _assert_quotes_in_chunk(response, req.chunk_text, req_path, raw)
                validated[j] = response
            except (ValidationError, QuoteNotInChunkError) as exc:
                errors[j] = exc
            finally:
                _cleanup(res_path)
                if not _error_path(req_path).exists():
                    _cleanup(req_path)

        # Collect (entry, was_hit) per request, SKIPPING any that errored
        # in dispatch. A single bad quote in a batch must not destroy the
        # other successes — the .error.json files preserve diagnostics
        # for the failed ones, the meter records cost for the successes.
        t0 = time.monotonic()
        entries: list[tuple[CachedExtract, bool] | None] = []
        dispatch_cursor = 0
        for i, key in enumerate(keys):
            if i not in uncached_set:
                def _unreachable() -> CachedExtract:  # noqa: E306
                    raise AssertionError(
                        "cache probe found a miss that disk check said was a hit"
                    )

                entry, was_hit = self._cache.get_or_extract(key, _unreachable)
                entries.append((entry, was_hit))
            else:
                j = dispatch_cursor
                dispatch_cursor += 1
                if j in errors:
                    # Dispatch produced an .error.json; drop this slot
                    # but keep going so the rest of the batch survives.
                    entries.append(None)
                    continue
                resp = validated[j]
                entry, was_hit = self._cache.get_or_extract(
                    key, lambda r=resp: _response_to_entry(r)
                )
                entries.append((entry, was_hit))

        # Record meter cost + emit responses for successful slots only.
        # Partial-batch return: caller gets a list shorter than requests
        # when some entries errored; dispatch tracking lives in the
        # .error.json files alongside the request payloads.
        wall = time.monotonic() - t0
        results: list[ExtractResponse] = []
        for req, key, slot in zip(requests, keys, entries):
            if slot is None:
                continue
            entry, was_hit = slot
            _record_call(
                self._meter,
                role=Role.EXTRACTOR,
                tier=req.tier,
                input_tokens=entry.tokens_in,
                output_tokens=entry.tokens_out,
                wall_seconds=wall,
                cache_hit=was_hit,
                prompt_hash_value=key.prompt_hash,
            )
            results.append(_entry_to_response(entry))
        return results

    # --- write -------------------------------------------------------------

    def write(self, request: WriteRequest) -> WriteResponse:
        t0 = time.monotonic()
        response, raw = _dispatch_model_with_raw(
            self._dir,
            "write",
            request.model_dump(mode="json"),
            WriteResponse,
        )
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

    # --- compact -----------------------------------------------------------

    def compact(self, page_id: str, title: str, entries: list[dict]) -> dict:
        t0 = time.monotonic()
        raw = _dispatch_raw(
            self._dir,
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

    # --- edit --------------------------------------------------------------

    def edit(
        self, page_id: str, title: str, dossier: list[dict], neighbors: list[dict]
    ) -> EditorBrief:
        t0 = time.monotonic()
        brief, raw = _dispatch_model_with_raw(
            self._dir,
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

    # --- orchestrate -------------------------------------------------------

    def orchestrate(self, state: OrchState) -> OrchAction:
        if self._tool_schemas and self._kg is not None:
            return self._orchestrate_with_tools(state)
        return self._orchestrate_single_turn(state)

    def _orchestrate_single_turn(self, state: OrchState) -> OrchAction:
        t0 = time.monotonic()
        action = _dispatch_model(
            self._dir,
            "orchestrate",
            {
                "run_id": state.run_id,
                "n_pages": state.n_pages,
                "n_candidates": state.n_candidates,
                "last_actions": state.last_actions,
                "budget_spent": state.budget_spent,
                "budget_remaining": state.budget_remaining,
                "novelty_rate": state.novelty_rate,
                "page_summaries": state.page_summaries,
                "sampler_snapshot": state.sampler_snapshot,
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

    def _orchestrate_with_tools(self, state: OrchState) -> OrchAction:
        """Multi-turn orchestrator dispatch with local KG tool execution.

        The orchestrator can call free KG tools (search_chunks, get_citations,
        etc.) before committing to a terminal action. Each tool call is
        executed locally; only the orchestrator LLM calls cost tokens.
        """
        from .distill.kg_tools import KG_TOOL_NAMES

        t0 = time.monotonic()
        total_in = 0
        total_out = 0

        payload: dict = {
            "run_id": state.run_id,
            "n_pages": state.n_pages,
            "n_candidates": state.n_candidates,
            "last_actions": state.last_actions,
            "budget_spent": state.budget_spent,
            "budget_remaining": state.budget_remaining,
            "novelty_rate": state.novelty_rate,
            "page_summaries": state.page_summaries,
            "sampler_snapshot": state.sampler_snapshot,
            "tool_definitions": self._tool_schemas,
        }

        for turn in range(self._max_tool_turns):
            req_path, res_path = _write_request(self._dir, "orchestrate", payload)
            raw = _await_response(res_path)
            # Clean up request/response files
            with contextlib.suppress(OSError):
                req_path.unlink()
            with contextlib.suppress(OSError):
                res_path.unlink()

            total_in += raw.get("tokens_in", 0)
            total_out += raw.get("tokens_out", 0)
            action_name = raw.get("name", "")

            # Terminal action: anything that's not a KG tool
            if action_name not in KG_TOOL_NAMES:
                action = OrchAction(
                    name=action_name,
                    args=raw.get("args", {}),
                    tokens_in=total_in,
                    tokens_out=total_out,
                )
                _record_call(
                    self._meter,
                    role=Role.ORCHESTRATOR,
                    tier="L",
                    input_tokens=total_in,
                    output_tokens=total_out,
                    wall_seconds=time.monotonic() - t0,
                    prompt_hash_value="orchestrator_tools",
                )
                return action

            # KG tool: execute locally and feed result back
            tool_result = self._execute_kg_tool(
                action_name, raw.get("args", {})
            )
            payload = {
                "tool_call": {
                    "name": action_name,
                    "args": raw.get("args", {}),
                },
                "tool_result": tool_result,
                "turn": turn + 1,
            }

        # Max turns exceeded: fall back to explorer
        _record_call(
            self._meter,
            role=Role.ORCHESTRATOR,
            tier="L",
            input_tokens=total_in,
            output_tokens=total_out,
            wall_seconds=time.monotonic() - t0,
            prompt_hash_value="orchestrator_tools",
        )
        return OrchAction(
            name="walk_local",
            args={},
            tokens_in=total_in,
            tokens_out=total_out,
        )

    def _execute_kg_tool(self, name: str, args: dict) -> object:
        """Execute a KG tool locally and return JSON-serializable result."""
        from .distill import kg_tools

        match name:
            case "search_chunks":
                return kg_tools.search_chunks(self._kg, **args)
            case "get_source_info":
                return kg_tools.get_source_info(self._kg, **args)
            case "list_sources":
                return kg_tools.list_sources(self._kg, **args)
            case "get_citations":
                return kg_tools.get_citations(self._kg, **args)
            case "get_coverage":
                return kg_tools.get_coverage(self._snapshot)
            case "get_pages":
                return kg_tools.get_pages(self._guided_pages)
            case "get_budget":
                return kg_tools.get_budget(
                    self._meter, self._budget_target
                )
            case _:
                return {"error": f"unknown tool: {name}"}

    # --- query -------------------------------------------------------------

    def answer(self, request: QueryRequest) -> QueryResponse:
        t0 = time.monotonic()
        response = _dispatch_model(
            self._dir,
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
