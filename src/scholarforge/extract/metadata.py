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

    # Authors: prefer PDF metadata, then markdown author line, then filename
    authors_raw = meta.get("author", "")
    authors = _parse_authors(authors_raw) if authors_raw else []
    if not authors:
        authors = _extract_authors_from_markdown(md_text)
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


def _extract_authors_from_markdown(md_text: str) -> list[str]:
    """Try to extract author names from markdown text near the top.

    Looks for author-like lines between the title and the abstract/body.
    """
    lines = md_text[:5000].split("\n")

    # Find first non-metadata heading (title)
    title_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped.lstrip("# ")) > 5:
            title_idx = i
            break

    if title_idx < 0:
        return []

    # Collect candidate author lines (between title and abstract/introduction)
    candidates: list[str] = []
    for i in range(title_idx + 1, min(title_idx + 15, len(lines))):
        line = lines[i].strip()
        if not line:
            continue
        # Stop at abstract or introduction heading
        if re.match(r"(?i)^#*\s*\*?\*?(abstract|introduction|index\s+terms)", line):
            break
        # Skip affiliation/address lines
        if re.search(
            r"(?i)(university|department|institute|school|laboratory"
            r"|lab\b|@|e-mail|email|thuwal|saudi|china|usa\b|states\b)",
            line,
        ):
            continue
        candidates.append(line)

    # Try each candidate line
    for line in candidates:
        names = _parse_author_line(line)
        if len(names) >= 2:
            return names

    return []


# Words that are NOT author names (IEEE membership, roles, noise)
_AUTHOR_NOISE = {
    "ieee",
    "member",
    "senior",
    "fellow",
    "student",
    "life",
    "associate",
    "et",
    "al",
    "and",
    "the",
    "of",
    "vol",
    "no",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "transactions",
    "journal",
    "proceedings",
    "letters",
}


def _parse_author_line(line: str) -> list[str]:
    """Parse a single line into author names, filtering noise."""
    # Strip heading markers and markdown formatting
    cleaned = re.sub(r"^#+\s*", "", line)
    cleaned = re.sub(r"\*+", "", cleaned)  # bold/italic
    cleaned = re.sub(r"_+", " ", cleaned)  # underscores used as italic
    # Remove IEEE membership titles before splitting
    cleaned = re.sub(
        r",?\s*(?:Life |Senior |Student |Associate )?(?:Fellow|Member),?\s*(?:IEEE)?,?",
        ",",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Remove superscripts, footnote markers, affiliations in brackets
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"[†‡§]+", "", cleaned)
    # Remove trailing asterisks (corresponding author markers)
    cleaned = re.sub(r"\*+", "", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return []

    # Split on ", " or " and "
    parts = re.split(r",\s*|\s+and\s+", cleaned)

    names: list[str] = []
    for part in parts:
        part = part.strip().rstrip(",. ")
        # Strip "et al" / "et al." suffix
        part = re.sub(r"\s+et\s+al\.?$", "", part, flags=re.IGNORECASE).strip()
        if not part:
            continue
        # Skip if all words are noise
        words = part.split()
        if all(w.lower() in _AUTHOR_NOISE for w in words):
            continue
        # Skip numbers, single characters, very short tokens
        if re.match(r"^\d", part) or len(part) < 2:
            continue
        # Must start with uppercase (name-like)
        if not words[0][0:1].isupper():
            continue
        # Skip if too many words (probably a sentence, not a name)
        if len(words) > 5:
            continue
        names.append(part)

    return names


def _extract_abstract(md_text: str) -> str | None:
    """Extract abstract/summary from markdown text.

    Tries labeled sections first, then falls back to the first substantial
    paragraph of body text. Works for papers, reports, grant proposals, etc.
    """
    # Strip markdown formatting for matching purposes
    search_text = _clean_markdown(md_text[:10000])

    # ── Strategy 1: Labeled section ──────────────────────────────────────────
    # Matches "Abstract", "Summary", "Executive Summary" as heading or inline label
    # Handles: ## Abstract, ABSTRACT, **Abstract**—, _Abstract:_, etc.
    label_re = re.compile(
        r"(?:^|\n)\s*(?:#+\s*)?"
        r"(?:abstract|summary|executive\s+summary)"
        r"\s*[:\-—.]*\s*"
        r"(.*?)(?=\n\s*\n|\n#+|\n\s*(?:keywords?|introduction|index\s+terms)\b|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = label_re.search(search_text)
    if match:
        text = match.group(1).strip()
        # Sometimes the label and text are on separate lines
        if len(text) < 30:
            # Try grabbing the next paragraph after the label
            label_end = match.end()
            next_para = re.search(r"\S.*?(?=\n\s*\n|\n#+|\Z)", search_text[label_end:], re.DOTALL)
            if next_para:
                text = next_para.group(0).strip()
        if len(text) > 50 and not _is_noise_paragraph(text):
            return _clean_markdown(text)

    # ── Strategy 2: First substantial prose paragraph ──────────────────────
    # Skip title, author lines, metadata, and take the first real paragraph
    paragraphs = re.split(r"\n\s*\n", search_text)
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        if _is_noise_paragraph(para):
            continue
        # Must look like prose: long enough and contains sentence-ending punctuation
        if len(para) > 100 and re.search(r"[.!?]", para):
            return _clean_markdown(para)

    # ── Strategy 3: Fallback — first ~400 words of body text ─────────────
    # Concatenate all non-noise, non-heading paragraphs up to ~400 words
    body_words: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        if _is_noise_paragraph(para):
            continue
        if len(para) < 30:
            continue
        body_words.extend(para.split())
        if len(body_words) >= 400:
            break
    if body_words:
        text = " ".join(body_words[:400])
        # Try to cut at last sentence boundary
        last_period = max(text.rfind(". "), text.rfind(".\n"), text.rfind("."))
        if last_period > 100:
            text = text[: last_period + 1]
        return _clean_markdown(text)

    return None


def _is_noise_paragraph(text: str) -> bool:
    """Check if a paragraph is metadata noise rather than content."""
    lower = text.lower()
    noise_markers = [
        "authorized licensed use",
        "downloaded on",
        "©",
        "copyright",
        "all rights reserved",
        "using government drawings",
        "this report is the result of",
        "ieee transactions",
        "proceedings of",
        "permission to make digital",
        "this article has been accepted",
        "personal use of this material",
        "redistribution",
        "university of",
        "department of",
        "manuscript received",
        "doi:",
        "published by",
        "accepted for publication",
        "public release; distribution",
        "fundamental research",
        "approved for public",
        "report number",
        "technical report",
        "contract no",
        "scientific and technical information",
        "in the interest of",
        "==> picture",
        "intentionally omitted",
    ]
    return any(marker in lower for marker in noise_markers)


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
