"""Deterministic fakes for tests + dry runs.

The fake extractor returns one canned concept per chunk built from the
chunk's leading words; the fake writer echoes the skeleton (or a stub
prose body) plus the supplied evidence; the fake orchestrator picks
``done`` after a small fixed number of steps.

All three respect the ExtractCache + CostMeter contract: every call goes
through ``meter.record`` so the run accounting is honest, and extract
calls go through ``cache.get_or_extract`` so cache semantics are
exercised.
"""

from __future__ import annotations

import re
import time

from ..agents.protocols import Extractor, Orchestrator, Querier, Writer
from ..agents.schema import (
    ExtractedConcept,
    ExtractRequest,
    ExtractResponse,
    OrchAction,
    OrchState,
    QueryAnswer,
    QueryRequest,
    QueryResponse,
    WriteRequest,
    WriteResponse,
)
from ..infra.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from ..infra.cost_meter import CostMeter
from ..infra.role import Role, response_reserve, total_context

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")


class FakeExtractor(Extractor):
    def __init__(self, cache: ExtractCache, meter: CostMeter) -> None:
        self._cache = cache
        self._meter = meter

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        key = ExtractCacheKey(
            model_id=request.model_id,
            prompt_hash=prompt_hash(request.prompt_template),
            chunk_id=request.chunk_id,
        )
        t0 = time.monotonic()

        def compute() -> CachedExtract:
            payload = _fake_extract_payload(request)
            return CachedExtract(payload=payload, tokens_in=200, tokens_out=80)

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
        concepts = [
            ExtractedConcept(
                title=c["title"],
                aliases=c["aliases"],
                kind=c["kind"],
                quote=c["quote"],
            )
            for c in payload["concepts"]
        ]
        return ExtractResponse(
            chunk_id=payload["chunk_id"],
            concepts=concepts,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
        )


def _fake_extract_payload(request: ExtractRequest) -> dict:
    """Pull two longest tokens from the chunk; promote them as concept candidates.

    The first token is a concept; if a capitalised bigram looks like a name in
    the original text we also emit a person.
    """
    text = request.chunk_text
    tokens = [t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 5]
    seen: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
        if len(seen) >= 2:
            break
    concepts: list[dict] = []
    for tok in seen:
        # quote: first sentence containing the token, else first 80 chars
        quote = _first_sentence_with(text, tok) or text[:80]
        concepts.append(
            {
                "title": tok,
                "aliases": [],
                "kind": "concept",
                "quote": quote,
            }
        )
    person = _detect_person(text)
    if person:
        concepts.append(
            {
                "title": person,
                "aliases": [],
                "kind": "person",
                "quote": _first_sentence_with(text, person.split()[-1]) or text[:80],
            }
        )
    return {
        "chunk_id": request.chunk_id,
        "concepts": concepts,
        "tokens_in": 200,
        "tokens_out": 80,
    }


def _first_sentence_with(text: str, needle: str) -> str:
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if needle.lower() in sent.lower():
            return sent.strip()[:200]
    return ""


_NAME_RE = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")


def _detect_person(text: str) -> str | None:
    m = _NAME_RE.search(text)
    return f"{m.group(1)} {m.group(2)}" if m else None


# --- writer --------------------------------------------------------------


class FakeWriter(Writer):
    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def write(self, request: WriteRequest) -> WriteResponse:
        t0 = time.monotonic()
        used: list[str] = []
        sentences: list[str] = []
        for i, ev in enumerate(request.evidence, start=1):
            marker = f"e{i}"
            used.append(marker)
            sentences.append(f"{request.title} is described in the source corpus[^{marker}].")
        body = (
            " ".join(sentences) if sentences else (f"{request.title} is documented in this corpus.")
        )
        wall = time.monotonic() - t0
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=300,
            output_tokens=120,
            context_cap=total_context() - response_reserve(),
            wall_seconds=wall,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return WriteResponse(
            page_id=request.page_id,
            body_markdown=body,
            used_markers=used,
            tokens_in=300,
            tokens_out=120,
        )


# --- orchestrator --------------------------------------------------------


class FakeOrchestrator(Orchestrator):
    def __init__(self, meter: CostMeter, max_steps: int = 4) -> None:
        self._meter = meter
        self._max_steps = max_steps
        self._steps = 0

    def step(self, state: OrchState) -> OrchAction:
        self._steps += 1
        t0 = time.monotonic()
        name = "done" if self._steps >= self._max_steps else "jump_uniform"
        self._meter.record(
            role=Role.ORCHESTRATOR,
            tier="L",
            input_tokens=400,
            output_tokens=40,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash="fake",
        )
        return OrchAction(name=name, args={"n_docs": 2}, tokens_in=400, tokens_out=40)


# --- querier -------------------------------------------------------------


class FakeQuerier(Querier):
    """Deterministic stub. No randomness, no I/O, no model calls."""

    def answer(self, request: QueryRequest) -> QueryResponse:
        n = len(request.evidence)
        text = f"[fake] question='{request.question}' supported by {n} pages"
        citations = [ev.page_id for ev in request.evidence]
        chunks: list[str] = []
        for ev in request.evidence:
            for c in ev.citations:
                if c not in chunks:
                    chunks.append(c)
        return QueryResponse(
            answer=QueryAnswer(
                text=text,
                citations=citations,
                chunks=chunks,
                follow_ups=[],
            )
        )
