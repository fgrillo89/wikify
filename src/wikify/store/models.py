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
    # Web / Knowledge
    WEB_ARTICLE = "web_article"
    MARKDOWN = "markdown"
    WIKI_ARTICLE = "wiki_article"
    # Rich media / Code
    IMAGE = "image"
    REPO_README = "repo_readme"


class PaperOrigin(str, Enum):
    """Distinguishes ingested corpus papers from generated output."""

    CORPUS = "corpus"  # Ingested from PDF/DOCX — part of the knowledge base
    GENERATED = "generated"  # Produced by the writing pipeline — NOT part of corpus


class Paper(SQLModel, table=True):
    """A research paper or document in the knowledge base."""

    id: str = Field(primary_key=True)  # SHA256 of file content
    title: str
    authors: str = ""  # JSON list
    summary: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    doc_type: str = DocType.PAPER  # paper, report, proposal, note, presentation
    origin: str = PaperOrigin.CORPUS  # "corpus" or "generated"
    zotero_key: Optional[str] = None
    source_path: str = ""
    file_hash: str = ""  # For change detection on re-ingest
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    section_tree: str = "{}"  # JSON: nested TOC structure
    section_summaries: str = "{}"  # JSON: {"section_path": "1-2 sentence summary"}

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
        raw = raw.strip(". []")
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


class WikiArticle(SQLModel, table=True):
    """A curated wiki article authored or updated by the LLM."""

    id: str = Field(primary_key=True)  # slug, e.g. "HfO2_ALD_memristors"
    title: str
    status: str = "stub"  # stub | draft | full
    file_path: str  # relative to data/wiki/, e.g. "concepts/HfO2.md"
    source_ids: str = Field(default="[]")  # JSON list of Paper.id values
    topic_keys: str = Field(default="[]")  # JSON list of topic vocab keys
    domain: str = ""  # e.g. "material_science", "machine_learning"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model: str = ""
    needs_update: bool = False


class DomainPersona(SQLModel, table=True):
    """Expert persona generated from a domain's corpus sample, applied to all wiki writing."""

    domain: str = Field(primary_key=True)  # e.g. "material_science"
    persona_text: str  # 150-200 word expert persona
    source_sample: str = Field(default="[]")  # JSON list of source titles used
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model: str = ""


class SourceCoverage(SQLModel, table=True):
    """Records which wiki article each source contributed to, and what was extracted."""

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)      # Paper.id
    article_slug: str = Field(index=True)   # WikiArticle.id
    domain: str = ""
    extraction: str = ""  # haiku-extracted sentence(s) that were used
    covered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConceptRecord(SQLModel, table=True):
    """A named concept discovered from the corpus by the epoch pipeline."""

    id: str = Field(primary_key=True)  # slugified name, e.g. "atomic_layer_deposition"
    name: str  # canonical display name, e.g. "Atomic Layer Deposition"
    aliases: str = Field(default="[]")  # JSON list, e.g. '["ALD", "atomic layer dep."]'
    definition: str = ""  # one-line definition from discovery
    concept_type: str = ""  # technique | material | phenomenon | method | theory | dataset
    domain: str = ""  # deprecated: use domains; kept for backward compat
    domains: str = Field(default="[]")  # JSON list of DomainCluster.id values
    importance: float = 0.0  # 0-1, computed from concept graph (updated in Pass 2)
    epoch_discovered: int = 0
    epoch_last_updated: int = 0
    article_status: str = "none"  # none | stub | draft | full
    article_path: str = ""  # relative path to .md file, or ""

    @property
    def parsed_aliases(self) -> list[str]:
        """Parse aliases JSON safely."""
        if not self.aliases:
            return []
        try:
            return json.loads(self.aliases)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def parsed_domains(self) -> list[str]:
        """Parse domains JSON safely."""
        if not self.domains:
            return [self.domain] if self.domain else []
        try:
            result = json.loads(self.domains)
            return result if result else ([self.domain] if self.domain else [])
        except (json.JSONDecodeError, TypeError):
            return [self.domain] if self.domain else []


class ConceptRelation(SQLModel, table=True):
    """A directed relationship between two concepts in the concept graph."""

    id: Optional[int] = Field(default=None, primary_key=True)
    source_concept: str = Field(index=True)  # FK -> ConceptRecord.id
    target_concept: str = Field(index=True)  # FK -> ConceptRecord.id
    relation_type: str = ""  # IS-A | PART-OF | USED-IN | ENABLES | CONTRASTS-WITH
    weight: float = 0.0  # co-occurrence strength
    epoch: int = 0


class EpochLog(SQLModel, table=True):
    """Log entry for one completed epoch of the Wikipedia pipeline."""

    id: Optional[int] = Field(default=None, primary_key=True)
    epoch: int = Field(index=True)
    triggered_by: str = ""  # "user" | "ingest" | "schedule"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    concepts_discovered: int = 0
    stubs_upgraded: int = 0
    articles_written: int = 0
    contradictions_flagged: int = 0
    cross_refs_added: int = 0
    converged: bool = False
    loss_score: float = 0.0  # L computed after Pass 5
    loss_delta: float = 0.0  # |L(epoch_n) - L(epoch_n-1)|


class ChunkMiningLog(SQLModel, table=True):
    """Tracks which chunks have been mined for concepts and in which epoch.

    Progressive mining ensures every chunk is eventually processed:
    - Tier 0 (abstract, introduction, conclusion): mined in early epochs
    - Tier 1 (methods, results): mined in mid epochs
    - Tier 2 (body, discussion, other): mined in later epochs
    - Exploration: random 5% of unmined chunks mined each epoch regardless of tier
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    chunk_id: str = Field(index=True)  # FK -> Chunk.id
    paper_id: str = Field(index=True)  # FK -> Paper.id
    epoch_mined: int = 0  # which epoch this chunk was processed in
    tier: int = 0  # 0=high priority, 1=medium, 2=low
    source: str = ""  # "scheduled" | "exploration" | "deepening"


class DomainCluster(SQLModel, table=True):
    """A discovered domain community from the concept co-occurrence graph."""

    id: str = Field(primary_key=True)  # e.g. "cluster_0" or slug of label
    label: str  # LLM-generated, e.g. "ALD Process Engineering"
    scope: str = ""  # one-sentence scope statement
    epoch_created: int = 0
    epoch_last_updated: int = 0
    concept_count: int = 0
    core_concept_ids: str = Field(default="[]")  # JSON list of ConceptRecord.id
    bridge_concept_ids: str = Field(default="[]")  # JSON list
    centroid_embedding: str = Field(default="[]")  # JSON list[float]
    modularity_contribution: float = 0.0
    persona_text: str = ""  # community-specific persona
    merged_from: str = Field(default="[]")  # JSON list of previous cluster ids

    @property
    def parsed_core_concepts(self) -> list[str]:
        try:
            return json.loads(self.core_concept_ids)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def parsed_bridge_concepts(self) -> list[str]:
        try:
            return json.loads(self.bridge_concept_ids)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def parsed_centroid(self) -> list[float]:
        try:
            return json.loads(self.centroid_embedding)
        except (json.JSONDecodeError, TypeError):
            return []


class TopologySnapshot(SQLModel, table=True):
    """Corpus topology metrics captured once per epoch after domain discovery."""

    id: Optional[int] = Field(default=None, primary_key=True)
    epoch: int = Field(index=True)
    modularity_q: float = 0.0
    inter_community_edge_ratio: float = 0.0
    bridge_density: float = 0.0
    community_gini: float = 0.0
    spectral_gap: float = 0.0
    community_count: int = 0
    total_concepts: int = 0
    total_edges: int = 0


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


# ── Project & Output models ──────────────────────────────────────────────────


class Project(SQLModel, table=True):
    """A research project — groups corpus papers and generated outputs.

    Each project has its own corpus scope. Papers can belong to multiple
    projects (many-to-many via ProjectPaper). Outputs belong to exactly
    one project.
    """

    id: str = Field(primary_key=True)  # slug, e.g. "ald-memristors"
    name: str  # human-readable, e.g. "ALD Memristors for Neuromorphic"
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProjectPaper(SQLModel, table=True):
    """Many-to-many: which papers belong to which project's corpus."""

    project_id: str = Field(foreign_key="project.id", primary_key=True)
    paper_id: str = Field(foreign_key="paper.id", primary_key=True)


class GeneratedOutput(SQLModel, table=True):
    """A generated document (review, paper, presentation, etc.).

    Tracks the output separately from corpus papers. Stores the generation
    context (strategy, reading log, coverage score) for reproducibility.
    """

    id: str = Field(primary_key=True)  # UUID
    project_id: Optional[str] = Field(default=None, foreign_key="project.id")
    title: str = ""
    artifact_type: str = "lit_review"  # lit_review, research, abstract, etc.
    strategy: str = ""  # snowball, greedy_submodular, etc.
    journal: str = ""  # target journal for formatting
    markdown_path: str = ""  # path to .md output
    docx_path: str = ""
    pdf_path: str = ""
    reading_log_path: str = ""
    coverage_score: Optional[float] = None
    word_count: int = 0
    citation_count: int = 0
    token_cost: int = 0  # total tokens consumed during generation
    duration_seconds: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata_json: str = "{}"  # flexible JSON for strategy-specific data


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
