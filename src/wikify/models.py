"""The eight data structures that define wikify.

Everything in the package operates on these. If a piece of code needs a new
shape, add it here first and justify it in the README.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wikify.citations.models import CitationEntry

DocKind = Literal["pdf", "docx", "pptx", "html", "md"]
PageKind = Literal["article", "person"]


# --- Corpus side ---------------------------------------------------------


@dataclass
class DocImage:
    """One image extracted from a document.

    The image file lives at `path`; caption/alt text come from the parser
    when the source format provides them (figure captions in pdf, alt text
    in html, slide text near the image in pptx).
    """

    id: str  # e.g. "{doc_id}/fig_03"
    path: str  # corpus/images/{doc_id}/fig_03.png
    caption: str = ""
    alt_text: str = ""
    page: int | None = None  # or slide number
    near_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class DocSection:
    """A logical section of a document.

    Built from the markdown heading tree at ingest time. `summary` is
    optional and, when present, is a cheap small-model summary produced
    once during ingest so that distillation strategies can cheaply peek
    at sections without re-reading their chunks.
    """

    path: list[str]  # ["3. Results", "3.2 Photoactivity"]
    chunk_ids: list[str]
    summary: str = ""  # optional, pre-computed once


@dataclass
class Document:
    """A parsed source document.

    A Document is the file-level handle. Its canonical content lives on
    disk (`markdown_path`, `image_dir`); the fields here are the small
    structural index built once at ingest and cached in
    `corpus/docs/{id}.json`.

    Helpers on top of this (see ``corpus/chunks.py``,
    ``corpus/doc_markdown.py``) provide ``read_chunks(corpus, doc_id)``,
    ``read_markdown(corpus, doc_id)``, and section/figure lookups.
        get_intro(doc)     -> DocSection | None
        get_abstract(doc)  -> str | None
        get_images(doc)    -> list[DocImage]
    """

    id: str
    source_path: str
    kind: DocKind
    title: str
    metadata: dict  # authors, year, venue, doi, citations, ...
    markdown_path: str  # corpus/markdown/{id}.md
    image_dir: str  # corpus/images/{id}/
    sections: list[DocSection] = field(default_factory=list)
    images: list[DocImage] = field(default_factory=list)
    abstract: str = ""  # parsed or summarised, optional
    tldr: str = ""  # one-paragraph small-model summary, optional
    n_chunks: int = 0
    n_tokens: int = 0
    citations: list[CitationEntry] = field(default_factory=list)
    equations: list[dict] = field(default_factory=list)
    # Inline figure / table / scheme references parsed from body prose.
    # Each entry: ``{key, caption, section_path, char_offset}``. Used by
    # the extract handler to know which figures the current chunk is
    # discussing even when the figure binary couldn't be extracted.
    figure_refs: list[dict] = field(default_factory=list)
    # Doc-level edges computed post-embed for the Obsidian-friendly
    # per-doc markdown export (see store/doc_markdown.py).
    similar_to: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)
    cites_same: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    id: str
    doc_id: str
    ord: int
    text: str
    char_span: tuple[int, int]
    section_path: list[str]
    section_type: str = "body"  # canonical type from section_classifier
    # Equation ids whose source offset falls within this chunk's char span.
    # Populated by the chunker after equations are extracted from the
    # full document markdown — gives the extract handler equation context
    # in addition to the chunk text.
    equation_ids: list[str] = field(default_factory=list)
    # Soft flag set at ingest by ``ingest.boilerplate.is_boilerplate``:
    # True for chunks dominated by legal / journal-end-matter language
    # (thesis copyright preambles, "Reprints and permissions / Peer
    # review information" footers, etc.). The fluent ``KnowledgeGraph``
    # API filters these out by default; consumers that want to see them
    # pass ``include_boilerplate=True``.
    is_boilerplate: bool = False
    # embedding lives in the vector store, keyed by id


# --- Wiki side -----------------------------------------------------------


@dataclass
class Evidence:
    """Bridge from a claim in a wiki page to a specific corpus chunk.

    Serialised in the page file as a footnote:
        [^{marker}]: {chunk_id} ({doc_id}, {locator}) > "{quote}"
    Every factual sentence in WikiPage.body_markdown should reference a
    marker; every marker must resolve to one Evidence entry.
    """

    marker: str  # e.g. "e1", matches [^e1] in the body
    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""  # optional: "p.3", "slide 4", "sec 2.1"


@dataclass
class WikiPage:
    """In-memory view of a wiki markdown file.

    The canonical form is the file at wiki/{kind}s/{slug}.md. This dataclass
    is the parsed view used during distillation; it is never the source of
    truth on its own.
    """

    id: str
    kind: PageKind
    title: str
    aliases: list[str]
    body_markdown: str
    evidence: list[Evidence]
    links: list[str] = field(default_factory=list)  # other WikiPage ids
    equations: list[dict] = field(default_factory=list)  # {latex, label, kind, context}
    provenance: dict = field(default_factory=dict)  # run_id, model, sampled


# --- Run side ------------------------------------------------------------


@dataclass
class Stage:
    name: str
    t_start: datetime
    t_end: datetime | None = None
    counters: dict[str, int] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=dict)


@dataclass
class Run:
    id: str
    started_at: datetime
    finished_at: datetime | None
    config_hash: str
    stages: list[Stage] = field(default_factory=list)
    sampled_chunks: list[str] = field(default_factory=list)
    page_ids: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
