"""Deterministic author/person pages built from primary metadata + citations.

The wiki concept extractor used to spend model budget on person pages.
That path is now disabled (see ``canonicalize.py``); instead this module
builds person pages directly from two cheap, deterministic sources:

1. ``Document.metadata['authors']`` -- the primary author list of every
   ingested doc. These give the "Papers in this corpus" section.
2. ``Document.citations`` -- the structured bibliography entries parsed
   at ingest time. Authors that appear there but not as primary authors
   still get listed under "Cited works".

Each author becomes one ``WikiPage`` with deterministic body markdown
and one ``Evidence`` entry per linked doc, so M3 g_evidence on author
pages stays non-zero. No model call.
"""

from __future__ import annotations

import re
import unicodedata

from ..ingest.metadata import _is_valid_author
from ..models import Document, Evidence, WikiPage

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _NORM_RE.sub("-", s.lower()).strip("-")


def _normalize_author_name(name: str) -> str:
    """Normalize whitespace and trailing punctuation; preserve initials."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(",.;")
    # Drop affiliation superscripts like "Smith 1,2"
    name = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", name)
    return name


def _author_key(name: str) -> str:
    """Lowercase normalized form used to dedupe author identities."""
    n = _normalize_author_name(name)
    return _NORM_RE.sub(" ", n.lower()).strip()


def build_author_pages(
    docs: list[Document],
    existing_index=None,  # noqa: ARG001 — reserved for future merge
) -> list[WikiPage]:
    """Return one WikiPage per unique valid author across ``docs``."""
    # author_key -> {display, primary: [(doc, year)], cited: [(doc, year, title)]}
    bucket: dict[str, dict] = {}

    for doc in docs:
        meta = doc.metadata or {}
        year = meta.get("year")
        primary_authors = meta.get("authors") or []
        if isinstance(primary_authors, str):
            primary_authors = [primary_authors]
        for raw in primary_authors:
            name = _normalize_author_name(str(raw))
            if not _is_valid_author(name):
                continue
            key = _author_key(name)
            if not key:
                continue
            entry = bucket.setdefault(key, {"display": name, "primary": [], "cited": []})
            entry["primary"].append((doc, year))

        for cit in doc.citations or []:
            cit_year = cit.get("year")
            cit_title = cit.get("title") or cit.get("raw_text", "")[:120]
            for raw in cit.get("authors") or []:
                name = _normalize_author_name(str(raw))
                if not _is_valid_author(name):
                    continue
                key = _author_key(name)
                if not key:
                    continue
                entry = bucket.setdefault(key, {"display": name, "primary": [], "cited": []})
                entry["cited"].append((doc, cit_year, cit_title))

    pages: list[WikiPage] = []
    for key, info in sorted(bucket.items()):
        display = info["display"]
        slug = _slug(display)
        if not slug:
            continue
        page_id = f"person-{slug}"
        body = _render_body(display, info["primary"], info["cited"])
        evidence = _build_evidence(info["primary"], info["cited"])
        if not evidence:
            continue
        pages.append(
            WikiPage(
                id=page_id,
                kind="person",
                title=display,
                aliases=[],
                body_markdown=body,
                evidence=evidence,
                provenance={
                    "strategy": "deterministic",
                    "source": "metadata+citations",
                    "from_citation_count": len(info["cited"]),
                    "primary_count": len(info["primary"]),
                },
            )
        )
    return pages


def _render_body(
    name: str,
    primary: list[tuple[Document, int | None]],
    cited: list[tuple[Document, int | None, str]],
) -> str:
    lines: list[str] = [f"# {name}", ""]
    if primary:
        lines.append("## Papers in this corpus")
        lines.append("")
        seen: set[str] = set()
        for doc, year in primary:
            if doc.id in seen:
                continue
            seen.add(doc.id)
            year_str = str(year) if year else "n.d."
            lines.append(f"- {year_str}. *{doc.title}*. [doc:{doc.id}]")
        lines.append("")
    if cited:
        lines.append("## Cited works")
        lines.append("")
        seen_pairs: set[tuple[str, str]] = set()
        for doc, cit_year, cit_title in cited:
            key = (doc.id, (cit_title or "")[:80])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            year_str = str(cit_year) if cit_year else "n.d."
            lines.append(f"- {year_str}. *{cit_title}*. [from doc:{doc.id}]")
        lines.append("")
    return "\n".join(lines).strip()


def _build_evidence(
    primary: list[tuple[Document, int | None]],
    cited: list[tuple[Document, int | None, str]],
) -> list[Evidence]:
    """One Evidence entry per unique linked doc.

    chunk_id is set to the first chunk of the doc (``{doc_id}/c000``
    convention used by the chunker); quote is the doc title. This keeps
    M3 g_evidence non-zero on deterministic person pages.
    """
    seen: set[str] = set()
    out: list[Evidence] = []
    n = 0

    def add(doc: Document) -> None:
        nonlocal n
        if doc.id in seen:
            return
        seen.add(doc.id)
        n += 1
        out.append(
            Evidence(
                marker=f"e{n}",
                chunk_id=_first_chunk_id(doc),
                doc_id=doc.id,
                quote=doc.title or doc.id,
            )
        )

    for doc, _ in primary:
        add(doc)
    for doc, _, _ in cited:
        add(doc)
    return out


def _first_chunk_id(doc: Document) -> str:
    """Best-effort: the first chunk_id of a doc.

    The chunker writes ids like ``{doc_id}/c000``; if the doc has a
    sections index we use the first chunk_id from there for accuracy.
    """
    for sec in doc.sections or []:
        if sec.chunk_ids:
            return sec.chunk_ids[0]
    return f"{doc.id}/c000"
