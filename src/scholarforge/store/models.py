"""Core data models for ScholarForge."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Field, SQLModel

# ── SQLite models (via SQLModel) ──────────────────────────────────────────────


class DocType(str, Enum):
    PAPER = "paper"
    REPORT = "report"
    PROPOSAL = "proposal"
    NOTE = "note"
    PRESENTATION = "presentation"
    OTHER = "other"


class Paper(SQLModel, table=True):
    """A research paper or document in the knowledge base."""

    id: str = Field(primary_key=True)  # SHA256 of file content
    title: str
    authors: str = ""  # JSON list
    summary: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    doc_type: str = DocType.PAPER  # paper, report, proposal, note, presentation
    zotero_key: Optional[str] = None
    source_path: str = ""
    file_hash: str = ""  # For change detection on re-ingest
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    section_tree: str = "{}"  # JSON: nested TOC structure

    @property
    def parsed_authors(self) -> list[str]:
        """Parse authors JSON safely."""
        if not self.authors:
            return []
        try:
            return json.loads(self.authors)
        except (json.JSONDecodeError, TypeError):
            return []

    def display_name(self) -> str:
        """Create display name like 'Kim 2021 - 4K-memristor...' for wikilinks."""
        authors = self.parsed_authors
        first_author = authors[0].split()[-1] if authors else "Unknown"
        year = self.year or "YYYY"
        title = self.title or "Untitled"
        raw = f"{first_author} {year} - {title}"
        # Sanitize for filenames
        raw = re.sub(r'[<>:"/\\|?*]', "", raw)
        raw = raw.strip(". ")
        return raw[:200]


class Chunk(SQLModel, table=True):
    """A text chunk from a paper, section-aware."""

    id: str = Field(primary_key=True)  # UUID
    paper_id: str = Field(foreign_key="paper.id")
    section_path: str = ""  # e.g. "3.Methods.3.2.Data Collection"
    section_type: str = "body"  # Canonical type: introduction, methods, results, etc.
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


class FigureRef(SQLModel, table=True):
    """A figure reference extracted from paper text (caption-first, no binary)."""

    id: str = Field(primary_key=True)
    paper_id: str = Field(foreign_key="paper.id")
    figure_key: str = ""  # e.g. "Fig. 1", "Figure 2a"
    caption_text: str = ""
    section_path: Optional[str] = None
    page_number: Optional[int] = None


class PaperTopic(SQLModel, table=True):
    """A topic tag for a paper, extracted during ingestion."""

    paper_id: str = Field(foreign_key="paper.id", primary_key=True)
    topic: str = Field(primary_key=True)  # canonical display form
    is_declared: bool = False  # True = from paper's own keywords


class JournalTemplate(SQLModel, table=True):
    """A tracked journal/publisher DOCX or LaTeX template."""

    id: str = Field(primary_key=True)  # sanitized name, e.g. "wiley_afm"
    name: str  # display name, e.g. "Advanced Functional Materials"
    publisher: str = ""
    file_path: str  # absolute path to the .docx/.cls file
    file_type: str = "docx"  # "docx" or "latex"
    source_url: str = ""  # where to download from
    imported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str = ""


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
    SIMILAR_TO = "similar_to"
    BIBLIOGRAPHIC_COUPLING = "bibliographic_coupling"


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

    def flat_sections(self) -> list[SectionPlan]:
        """Return all sections and subsections in a flat list (depth-first)."""

        def _flatten(sections: list[SectionPlan]) -> list[SectionPlan]:
            result: list[SectionPlan] = []
            for s in sections:
                result.append(s)
                if s.subsections:
                    result.extend(_flatten(s.subsections))
            return result

        return _flatten(self.sections)
