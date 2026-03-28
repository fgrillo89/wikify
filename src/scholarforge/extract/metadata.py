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

    # Parse structured info from filename pattern: [YYYY Author] Title.pdf
    fn_year, fn_author, fn_title = _parse_filename(filename)

    # Title: try multiple sources, pick the best one
    heading_title = _first_heading(md_text)
    pdf_title = meta.get("title", "").strip()

    # Priority: clean heading > clean PDF title > filename title > raw filename
    if heading_title and not _is_garbled_title(heading_title):
        title = heading_title
    elif pdf_title and not _is_garbled_title(pdf_title):
        title = pdf_title
    elif fn_title:
        title = fn_title
    else:
        title = filename.replace(".pdf", "")

    # Authors: prefer PDF metadata, fall back to filename author
    authors_raw = meta.get("author", "")
    authors = _parse_authors(authors_raw) if authors_raw else []
    if not authors and fn_author:
        authors = [fn_author]

    # Abstract: look for "Abstract" section in markdown
    abstract = _extract_abstract(md_text)

    # Year: prefer filename year, then metadata date
    year = fn_year or _extract_year(meta)

    # DOI: search in first page text
    doi = _extract_doi(md_text[:3000])

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "year": year,
        "doi": doi,
    }


def _parse_filename(filename: str) -> tuple[int | None, str | None, str | None]:
    """Parse [YYYY Author] Title.pdf pattern. Returns (year, author, title)."""
    # Match [YYYY Author(s)] Title
    m = re.match(r"\[(\d{4})\s+([^\]]+)\]\s*(.+?)\.(?:pdf|docx|pptx)$", filename, re.IGNORECASE)
    if m:
        year = int(m.group(1))
        author = m.group(2).strip()
        title = m.group(3).strip()
        return year, author, title

    # Match [YYYY] Title
    m = re.match(r"\[(\d{4})\]\s*(.+?)\.(?:pdf|docx|pptx)$", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), None, m.group(2).strip()

    return None, None, None


def _is_garbled_title(title: str) -> bool:
    """Check if a title looks like a garbled internal PDF reference."""
    # Patterns like "acs_nn_nn-2014-01824r 1..7" or "la6b01014 1..13"
    if re.search(r"\d+\.\.\d+", title):
        return True
    # Short alphanumeric codes
    if re.match(r"^[a-z0-9_\-]{3,20}$", title, re.IGNORECASE):
        return True
    if re.match(r"^untitled$", title, re.IGNORECASE):
        return True
    if len(title) < 5 and not any(c.isalpha() for c in title):
        return True
    # ACS/journal internal refs
    if re.match(r"^[a-z]{2,4}[_\-]", title) and re.search(r"\d{4}", title):
        return True
    return False


def _clean_markdown(text: str) -> str:
    """Remove markdown formatting from text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*(.+?)\*", r"\1", text)  # italic
    text = re.sub(r"_(.+?)_", r"\1", text)  # underline-italic
    text = re.sub(r"`(.+?)`", r"\1", text)  # inline code
    return text.strip()


def _first_heading(md_text: str) -> str | None:
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped.lstrip("# ").strip()
            heading = _clean_markdown(heading)
            if heading:
                return heading
    return None


def _parse_authors(raw: str) -> list[str]:
    # Handle semicolons and "and" as primary delimiters
    raw = raw.replace(";", ",").replace(" and ", ",")
    parts = [a.strip() for a in raw.split(",") if a.strip()]

    # Reassemble "LastName, Initials" pairs: if a part looks like initials
    # (all uppercase, short, possibly with dots), merge it with the previous part
    authors: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        # Check if next part looks like initials (e.g., "J. J." or "A.")
        if i + 1 < len(parts) and re.match(r"^[A-Z][.\s]*[A-Z]?\.?$", parts[i + 1]):
            authors.append(f"{parts[i + 1]} {part}")  # "J. J. Yang"
            i += 2
        else:
            authors.append(part)
            i += 1

    return authors


def _extract_abstract(md_text: str) -> str | None:
    # Strategy 1: heading-style abstract (## Abstract\n text...)
    pattern = re.compile(
        r"(?:^|\n)#+\s*[Aa]bstract\s*\n(.*?)(?=\n#+\s|\Z)",
        re.DOTALL,
    )
    match = pattern.search(md_text)
    if match:
        text = match.group(1).strip()
        if len(text) > 50:
            return text

    # Strategy 2: inline abstract — "ABSTRACT text..." or "**Abstract** text..."
    # Common in ACS, IEEE, Nature-style PDFs
    pattern2 = re.compile(
        r"(?:^|\n)\s*\*?\*?(?:ABSTRACT|Abstract)\*?\*?\s*[:\-—.]?\s*(.*?)(?=\n\n|\n#+|\Z)",
        re.DOTALL,
    )
    match2 = pattern2.search(md_text[:8000])
    if match2:
        text = match2.group(1).strip()
        if len(text) > 50:
            return text

    # Strategy 3: "Abstract—" or "Abstract:" on same line as text
    pattern3 = re.compile(
        r"(?:^|\n)\s*\*?\*?[Aa]bstract\*?\*?\s*[—:\-]\s*(.+?)(?=\n\n|\n#+|\Z)",
        re.DOTALL,
    )
    match3 = pattern3.search(md_text[:8000])
    if match3:
        text = match3.group(1).strip()
        if len(text) > 50:
            return text

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
