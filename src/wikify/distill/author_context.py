"""Author context builder for person pages.

Produces structured AuthorContext dicts keyed by normalised author name.
The context is attached to WriteRequest.author_context so the model can
write a grounded biographical article without inventing facts.

No prose generation happens here. No bullet lists. No wikilinks. Only
plain structured data that the writer uses as grounding for its output.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from wikify.ingest.metadata import _is_valid_author
from wikify.models import Document

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_author_name(name: str) -> str:
    """Normalize whitespace and trailing punctuation; preserve initials."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(",.;")
    name = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", name)
    return name


def _author_key(name: str) -> str:
    n = _normalize_author_name(name)
    return _NORM_RE.sub(" ", n.lower()).strip()


@dataclass
class Publication:
    """A document authored by the person."""

    doc_id: str
    title: str
    year: int | None = None


@dataclass
class CitedWork:
    """A work cited by a corpus document, attributed to the person."""

    title: str
    year: int | None = None
    citing_doc_id: str = ""


@dataclass
class AuthorContext:
    """Structured grounding context attached to a person-page WriteRequest.

    This is context only --- it is never emitted to disk as its own file.
    The writer uses it as grounded facts when composing biographical prose.
    All fields are plain data; no prose, no bullet rendering, no wikilinks.
    """

    primary_publications: list[Publication] = field(default_factory=list)
    cited_works: list[CitedWork] = field(default_factory=list)
    collaborators: list[str] = field(default_factory=list)
    year_range: tuple[int, int] | None = None
    affiliations: list[str] = field(default_factory=list)


def build_author_context(docs: list[Document]) -> dict[str, AuthorContext]:
    """Return one AuthorContext per unique valid corpus author across docs.

    Keyed by _author_key(name). Citation-only authors who appear in fewer
    than 2 distinct citations are still included --- the context is for
    grounding, not for filtering (the writer decides relevance).
    """
    bucket: dict[str, dict] = {}

    for doc in docs:
        meta = doc.metadata or {}
        year = meta.get("year")
        if isinstance(year, str):
            try:
                year = int(year)
            except ValueError:
                year = None

        primary_authors = meta.get("authors") or []
        if isinstance(primary_authors, str):
            primary_authors = [primary_authors]

        normed_primary = [
            _normalize_author_name(str(a))
            for a in primary_authors
            if _is_valid_author(_normalize_author_name(str(a)))
        ]

        for name in normed_primary:
            key = _author_key(name)
            if not key:
                continue
            entry = bucket.setdefault(
                key,
                {
                    "display": name,
                    "primary": [],
                    "cited": [],
                    "collaborators": set(),
                },
            )
            entry["primary"].append(
                Publication(doc_id=doc.id, title=doc.title or doc.id, year=year)
            )
            for other in normed_primary:
                if _author_key(other) != key:
                    entry["collaborators"].add(other)

        for cit in doc.citations or []:
            cit_year = cit.get("year")
            if isinstance(cit_year, str):
                try:
                    cit_year = int(cit_year)
                except ValueError:
                    cit_year = None
            cit_title = (cit.get("title") or cit.get("raw_text", ""))[:120]
            for raw in cit.get("authors") or []:
                name = _normalize_author_name(str(raw))
                if not _is_valid_author(name):
                    continue
                key = _author_key(name)
                if not key:
                    continue
                entry = bucket.setdefault(
                    key,
                    {
                        "display": name,
                        "primary": [],
                        "cited": [],
                        "collaborators": set(),
                    },
                )
                entry["cited"].append(
                    CitedWork(
                        title=cit_title,
                        year=cit_year,
                        citing_doc_id=doc.id,
                    )
                )

    result: dict[str, AuthorContext] = {}
    for key, info in bucket.items():
        primary: list[Publication] = info["primary"]
        cited: list[CitedWork] = info["cited"]
        collaborators = sorted(info["collaborators"])

        all_years = [p.year for p in primary if isinstance(p.year, int)]
        year_range: tuple[int, int] | None = None
        if all_years:
            year_range = (min(all_years), max(all_years))

        result[key] = AuthorContext(
            primary_publications=primary,
            cited_works=cited,
            collaborators=collaborators,
            year_range=year_range,
            affiliations=[],
        )

    return result
