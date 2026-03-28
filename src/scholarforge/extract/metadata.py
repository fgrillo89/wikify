"""Metadata extraction from PDF documents."""

from __future__ import annotations

import re
from typing import Any


def extract_metadata(doc, md_text: str, filename: str) -> dict[str, Any]:
    """Extract title, authors, abstract, year, DOI from a PDF document.

    Args:
        doc: A fitz.Document instance.
        md_text: The markdown text extracted by pymupdf4llm.
        filename: Original filename (fallback for title).

    Returns:
        Dict with keys: title, authors, abstract, year, doi.
    """
    meta = doc.metadata or {}

    # Title: prefer PDF metadata, fall back to first heading or filename
    title = meta.get("title", "").strip()
    if not title:
        title = _first_heading(md_text) or filename.replace(".pdf", "")

    # Authors
    authors_raw = meta.get("author", "")
    authors = _parse_authors(authors_raw) if authors_raw else []

    # Abstract: look for "Abstract" section in markdown
    abstract = _extract_abstract(md_text)

    # Year: from metadata date or filename
    year = _extract_year(meta)

    # DOI: search in first page text
    doi = _extract_doi(md_text[:3000])

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "year": year,
        "doi": doi,
    }


def _first_heading(md_text: str) -> str | None:
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
    return None


def _parse_authors(raw: str) -> list[str]:
    # Handle semicolons, "and", commas
    raw = raw.replace(";", ",").replace(" and ", ",")
    return [a.strip() for a in raw.split(",") if a.strip()]


def _extract_abstract(md_text: str) -> str | None:
    pattern = re.compile(
        r"(?:^|\n)#+\s*[Aa]bstract\s*\n(.*?)(?=\n#+\s|\Z)",
        re.DOTALL,
    )
    match = pattern.search(md_text)
    if match:
        return match.group(1).strip()

    # Try non-heading abstract (bold or uppercase)
    pattern2 = re.compile(
        r"(?:^|\n)\*?\*?[Aa]bstract\*?\*?\s*[:\-—]?\s*\n?(.*?)(?=\n\n|\n#+|\Z)",
        re.DOTALL,
    )
    match2 = pattern2.search(md_text[:5000])
    if match2:
        return match2.group(1).strip()

    return None


def _extract_year(meta: dict) -> int | None:
    for key in ("creationDate", "modDate"):
        val = meta.get(key, "")
        match = re.search(r"((?:19|20)\d{2})", val)
        if match:
            return int(match.group(1))
    return None


def _extract_doi(text: str) -> str | None:
    match = re.search(r"(10\.\d{4,}/[^\s]+)", text)
    if match:
        doi = match.group(1).rstrip(".,;)")
        return doi
    return None
