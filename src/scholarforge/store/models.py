"""Core data models for ScholarForge."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


# ── SQLite models (via SQLModel) ──────────────────────────────────────────────


class Paper(SQLModel, table=True):
    """A research paper or document in the knowledge base."""

    id: str = Field(primary_key=True)  # SHA256 of file content
    title: str
    authors: str = ""  # JSON list
    abstract: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    zotero_key: Optional[str] = None
    source_path: str = ""
    file_hash: str = ""  # For change detection on re-ingest
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    section_tree: str = "{}"  # JSON: nested TOC structure


class Chunk(SQLModel, table=True):
    """A text chunk from a paper, section-aware."""

    id: str = Field(primary_key=True)  # UUID
    paper_id: str = Field(foreign_key="paper.id")
    section_path: str = ""  # e.g. "3.Methods.3.2.Data Collection"
    content: str = ""
    token_count: int = 0
    chunk_index: int = 0  # Order within section
    has_citations: bool = False
    has_equations: bool = False


class Figure(SQLModel, table=True):
    """An extracted or generated figure."""

    id: str = Field(primary_key=True)  # Content-hash of image bytes
    paper_id: Optional[str] = Field(default=None, foreign_key="paper.id")
    caption: Optional[str] = None
    figure_number: Optional[str] = None  # e.g. "Fig. 3"
    section_path: Optional[str] = None
    image_path: str = ""  # Path in figures/ store
    width_px: int = 0
    height_px: int = 0
    format: str = "png"
    tags: str = "[]"  # JSON list
    extracted_data: Optional[str] = None  # JSON, if chart data extracted
    reuse_count: int = 0


class Citation(SQLModel, table=True):
    """A citation reference within a paper."""

    id: str = Field(primary_key=True)
    paper_id: str = Field(foreign_key="paper.id")
    cited_paper_id: Optional[str] = Field(default=None, foreign_key="paper.id")
    raw_text: str = ""  # e.g. "[Smith et al., 2023]"
    bibtex: Optional[str] = None
    csl_json: Optional[str] = None  # JSON
    context_chunk_id: Optional[str] = Field(default=None, foreign_key="chunk.id")


# ── Knowledge Graph types ─────────────────────────────────────────────────────


class GraphNodeType(str, Enum):
    PAPER = "paper"
    SECTION = "section"
    CHUNK = "chunk"
    FIGURE = "figure"
    CONCEPT = "concept"
    AUTHOR = "author"
    METHOD = "method"
    DATASET = "dataset"
    FINDING = "finding"


class GraphEdgeType(str, Enum):
    CONTAINS = "contains"
    CITES = "cites"
    DESCRIBES = "describes"
    USES_METHOD = "uses_method"
    USES_DATASET = "uses_dataset"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    RELATED_TO = "related_to"
    AUTHORED_BY = "authored_by"


# ── Generation planning models (Pydantic, not persisted) ─────────────────────


class FigurePlan(BaseModel):
    """Plan for a figure in a generated document."""

    type: str  # "reuse", "composite", "generate"
    source_figure_ids: list[str] = []
    generation_spec: Optional[dict] = None
    caption_draft: str = ""


class SectionPlan(BaseModel):
    """Plan for a section in a generated document."""

    heading: str
    level: int = 1
    description: str = ""
    target_tokens: int = 0
    source_papers: list[str] = []
    figures: list[FigurePlan] = []
    subsections: list[SectionPlan] = []


class PaperPlan(BaseModel):
    """Top-level plan for a generated document."""

    title: str
    paper_type: str  # "lit_review", "research", "grant_proposal", "abstract"
    target_length: int = 0  # approximate word count
    sections: list[SectionPlan] = []
