"""Data models for citestore."""

from __future__ import annotations

from dataclasses import dataclass, field


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
