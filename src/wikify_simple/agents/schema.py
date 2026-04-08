"""Typed request/response shapes for Extractor / Writer / Orchestrator.

These are the only structures the bindings ever see. They are validated
strictly: a missing or extra field aborts the call after one retry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- extractor -----------------------------------------------------------


@dataclass(frozen=True)
class ExtractRequest:
    chunk_id: str
    chunk_text: str
    canonical_titles: list[str]  # known wiki page titles to dedup against
    prompt_template: str  # used by the cache key
    model_id: str
    tier: str  # "S" | "M" | "L"


@dataclass(frozen=True)
class ExtractedConcept:
    title: str
    aliases: list[str]
    kind: str  # "concept" | "person"
    quote: str  # the supporting span from chunk_text


@dataclass(frozen=True)
class ExtractResponse:
    chunk_id: str
    concepts: list[ExtractedConcept]
    tokens_in: int
    tokens_out: int


def validate_extract_response(payload: Any) -> ExtractResponse:
    if not isinstance(payload, dict):
        raise ValueError("extract response must be a dict")
    for k in ("chunk_id", "concepts", "tokens_in", "tokens_out"):
        if k not in payload:
            raise ValueError(f"missing field in extract response: {k}")
    concepts: list[ExtractedConcept] = []
    for c in payload["concepts"]:
        for k in ("title", "aliases", "kind", "quote"):
            if k not in c:
                raise ValueError(f"missing field in concept: {k}")
        if c["kind"] not in ("concept", "person"):
            raise ValueError(f"bad concept.kind: {c['kind']}")
        concepts.append(
            ExtractedConcept(
                title=str(c["title"]),
                aliases=[str(a) for a in c["aliases"]],
                kind=str(c["kind"]),
                quote=str(c["quote"]),
            )
        )
    return ExtractResponse(
        chunk_id=str(payload["chunk_id"]),
        concepts=concepts,
        tokens_in=int(payload["tokens_in"]),
        tokens_out=int(payload["tokens_out"]),
    )


# --- writer --------------------------------------------------------------


@dataclass(frozen=True)
class WriteEvidenceRef:
    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""


@dataclass(frozen=True)
class WriteRequest:
    page_id: str
    page_kind: str  # "concept" | "person"
    title: str
    aliases: list[str]
    skeleton: str  # any pre-existing body to refine
    evidence: list[WriteEvidenceRef]
    neighbor_titles: list[str]
    prompt_template: str
    model_id: str
    tier: str


@dataclass(frozen=True)
class WriteResponse:
    page_id: str
    body_markdown: str  # body with [^eN] markers
    used_markers: list[str]  # markers actually referenced
    tokens_in: int
    tokens_out: int


def validate_write_response(payload: Any) -> WriteResponse:
    if not isinstance(payload, dict):
        raise ValueError("write response must be a dict")
    for k in ("page_id", "body_markdown", "used_markers", "tokens_in", "tokens_out"):
        if k not in payload:
            raise ValueError(f"missing field in write response: {k}")
    return WriteResponse(
        page_id=str(payload["page_id"]),
        body_markdown=str(payload["body_markdown"]),
        used_markers=[str(x) for x in payload["used_markers"]],
        tokens_in=int(payload["tokens_in"]),
        tokens_out=int(payload["tokens_out"]),
    )


# --- orchestrator --------------------------------------------------------


@dataclass(frozen=True)
class OrchState:
    """Snapshot of run state for one orchestrator step.

    The orchestrator's primary read surface is the wiki index, not the
    page bodies. ``index_path`` points at the bundle's ``_index.json``
    so the agent skill can mmap/read it directly without re-walking
    the directory or parsing markdown. ``index_summary`` carries the
    cheap aggregates the orchestrator needs to plan the next action
    without loading the index file at all.
    """

    run_id: str
    n_pages: int
    n_candidates: int
    n_concepts: int = 0
    n_people: int = 0
    docs_covered: int = 0
    docs_total: int = 0
    index_path: str = ""
    last_actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrchAction:
    name: str  # walk_local | jump_uniform | ... | done
    args: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0


# --- querier -------------------------------------------------------------


@dataclass(frozen=True)
class QueryEvidence:
    page_id: str
    page_title: str
    body_excerpt: str
    citations: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.page_id, str) or not self.page_id:
            raise ValueError("QueryEvidence.page_id must be non-empty str")
        if not isinstance(self.citations, list):
            raise ValueError("QueryEvidence.citations must be list")


@dataclass(frozen=True)
class QueryRequest:
    question: str
    evidence: list[QueryEvidence]
    prompt_template: str
    model_id: str
    tier: str

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("QueryRequest.question must be non-empty str")
        if not isinstance(self.evidence, list):
            raise ValueError("QueryRequest.evidence must be list")


@dataclass(frozen=True)
class QueryAnswer:
    text: str
    citations: list[str]
    chunks: list[str]
    follow_ups: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("QueryAnswer.text must be str")
        for f in ("citations", "chunks", "follow_ups"):
            if not isinstance(getattr(self, f), list):
                raise ValueError(f"QueryAnswer.{f} must be list")


@dataclass(frozen=True)
class QueryResponse:
    answer: QueryAnswer


def validate_query_response(payload: Any) -> QueryResponse:
    if not isinstance(payload, dict) or "answer" not in payload:
        raise ValueError("query response must include 'answer'")
    a = payload["answer"]
    for k in ("text", "citations", "chunks"):
        if k not in a:
            raise ValueError(f"missing field in query answer: {k}")
    return QueryResponse(
        answer=QueryAnswer(
            text=str(a["text"]),
            citations=[str(x) for x in a["citations"]],
            chunks=[str(x) for x in a["chunks"]],
            follow_ups=[str(x) for x in a.get("follow_ups", [])],
        )
    )


def validate_orch_action(payload: Any) -> OrchAction:
    if not isinstance(payload, dict) or "name" not in payload:
        raise ValueError("orchestrator action must include 'name'")
    return OrchAction(
        name=str(payload["name"]),
        args=dict(payload.get("args", {})),
        tokens_in=int(payload.get("tokens_in", 0)),
        tokens_out=int(payload.get("tokens_out", 0)),
    )
