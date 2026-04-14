"""Data models for citestore."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CitationEntry:
    """A single citation from a document's reference list.

    Canonical model for citations throughout the pipeline.  Produced by
    ingest (citations.py + cite_parse.py), consumed by bibtex.py,
    store/bibliography.py, distill/write_prep.py, and ref_lookup.py.
    """

    ord: int = 0
    raw_text: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    author_last_names: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    venue: str = ""
    volume: str = ""
    pages: str = ""
    publisher: str = ""
    resolution: str = ""  # 'openalex', 'doi', 'heuristic', 'crossref', ''
    confidence: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for JSON storage (backward-compatible with dict format)."""
        d: dict = {
            "ord": self.ord,
            "raw_text": self.raw_text,
            "title": self.title,
            "authors": list(self.authors),
            "author_last_names": list(self.author_last_names),
            "year": self.year,
            "doi": self.doi,
            "venue": self.venue,
            "volume": self.volume,
            "pages": self.pages,
            "publisher": self.publisher,
        }
        if self.resolution:
            d["resolution"] = self.resolution
            # Backward compat: set the old flag names
            if self.resolution in ("openalex", "crossref"):
                d["crossref_resolved"] = True
            if self.resolution == "doi":
                d["doi_resolved"] = True
            if self.confidence:
                d["crossref_score"] = self.confidence
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CitationEntry:
        """Deserialize from JSON (backward-compatible with dict format)."""
        resolution = d.get("resolution", "")
        if not resolution:
            if d.get("crossref_resolved"):
                resolution = "crossref"
            elif d.get("doi_resolved"):
                resolution = "doi"
        return cls(
            ord=d.get("ord", 0),
            raw_text=d.get("raw_text", ""),
            title=d.get("title", ""),
            authors=d.get("authors") or [],
            author_last_names=d.get("author_last_names") or [],
            year=d.get("year"),
            doi=d.get("doi", ""),
            venue=d.get("venue", ""),
            volume=d.get("volume", ""),
            pages=d.get("pages", ""),
            publisher=d.get("publisher", ""),
            resolution=resolution,
            confidence=d.get("crossref_score", 0.0),
        )


@dataclass
class Work:
    """A resolved academic work with full metadata."""

    doi: str
    openalex_id: str
    title: str
    year: int | None
    journal: str
    authors: list[str]
    volume: str
    issue: str
    first_page: str
    last_page: str
    publisher: str
    cited_by_count: int | None
    work_type: str
    bibtex: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class ResolutionResult:
    """Outcome of resolving a single citation input."""

    work: Work | None
    level: str  # 'A', 'B', 'C', 'miss'
    source_doi: str = ""
    source_text: str = ""
