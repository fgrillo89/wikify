"""Typed request/response shapes for Extractor / Writer / Orchestrator / Querier.

These are the only structures the bindings ever see. They are Pydantic v2
``BaseModel``s with ``frozen=True`` and ``extra="forbid"``, so a missing
or extra field aborts the call after one retry.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(frozen=True, extra="forbid")

# --- extractor -----------------------------------------------------------


class ImageRef(BaseModel):
    """Lightweight image reference passed to extractors and writers.

    A flat projection of ``store.images_index.ImageRecord`` so the agents
    layer doesn't take a store dependency. The fully-qualified ``id`` is
    ``"<doc_id>/<stem>"`` and is the canonical handle for citation.
    """

    model_config = _STRICT

    id: str
    label: str | None = None
    caption: str = ""
    page: int | None = None
    path: str = ""


class ExtractRequest(BaseModel):
    model_config = _STRICT

    chunk_id: str
    chunk_text: str
    canonical_titles: list[str]  # known wiki page titles to dedup against
    prompt_template: str  # used by the cache key
    model_id: str
    tier: str  # "S" | "M" | "L"
    images_for_doc: list[ImageRef] = Field(default_factory=list)


class ExtractedConcept(BaseModel):
    model_config = _STRICT

    title: str
    aliases: list[str]
    kind: Literal["concept", "person"]
    quote: str
    evidence_figures: list[str] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    model_config = _STRICT

    chunk_id: str
    concepts: list[ExtractedConcept]
    tokens_in: int
    tokens_out: int


# --- writer --------------------------------------------------------------


class WriteEvidenceRef(BaseModel):
    model_config = _STRICT

    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""


class WriteRequest(BaseModel):
    model_config = _STRICT

    page_id: str
    page_kind: str  # "concept" | "person"
    title: str
    aliases: list[str]
    skeleton: str
    evidence: list[WriteEvidenceRef]
    neighbor_titles: list[str]
    prompt_template: str
    model_id: str
    tier: str
    figures: list[ImageRef] = Field(default_factory=list)


class WriteResponse(BaseModel):
    model_config = _STRICT

    page_id: str
    body_markdown: str
    used_markers: list[str]
    tokens_in: int
    tokens_out: int


# --- orchestrator --------------------------------------------------------


class OrchState(BaseModel):
    """Snapshot of run state for one orchestrator step."""

    model_config = _STRICT

    run_id: str
    n_pages: int
    n_candidates: int
    n_concepts: int = 0
    n_people: int = 0
    docs_covered: int = 0
    docs_total: int = 0
    index_path: str = ""
    last_actions: list[str] = Field(default_factory=list)


class OrchAction(BaseModel):
    model_config = _STRICT

    name: str  # walk_local | jump_uniform | ... | done
    args: dict = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0


# --- querier -------------------------------------------------------------


class QueryEvidence(BaseModel):
    model_config = _STRICT

    page_id: str
    page_title: str
    body_excerpt: str
    citations: list[str]

    @field_validator("page_id")
    @classmethod
    def _page_id_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("QueryEvidence.page_id must be non-empty str")
        return v


class QueryRequest(BaseModel):
    model_config = _STRICT

    question: str
    evidence: list[QueryEvidence]
    prompt_template: str
    model_id: str
    tier: str

    @field_validator("question")
    @classmethod
    def _question_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("QueryRequest.question must be non-empty str")
        return v


class QueryAnswer(BaseModel):
    model_config = _STRICT

    text: str
    citations: list[str]
    chunks: list[str]
    follow_ups: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    model_config = _STRICT

    answer: QueryAnswer
    tokens_in: int = 0
    tokens_out: int = 0
