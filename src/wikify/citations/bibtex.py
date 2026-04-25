"""Convert OpenAlex work metadata to BibTeX strings."""

from __future__ import annotations

import re
import unicodedata


def _sanitize_key(text: str) -> str:
    """Strip diacritics and non-alphanumeric chars for a BibTeX key."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]", "", ascii_only)


def _escape_bibtex(text: str) -> str:
    """Escape special LaTeX characters in a BibTeX field value."""
    for ch in ("&", "%", "#", "_"):
        text = text.replace(ch, f"\\{ch}")
    return text


def _make_bibtex_key(authors: list[str], year: int | None, title: str) -> str:
    """Generate a readable BibTeX key like Smith2024Atomic."""
    first_author = ""
    if authors:
        parts = authors[0].split()
        first_author = _sanitize_key(parts[-1]) if parts else "Unknown"
    year_str = str(year) if year else "XXXX"
    first_word = ""
    for word in title.split():
        cleaned = _sanitize_key(word)
        if len(cleaned) >= 3:
            first_word = cleaned
            break
    return f"{first_author}{year_str}{first_word}" or "unknown"


def _format_pages(first_page: str, last_page: str) -> str:
    if first_page and last_page:
        return f"{first_page}--{last_page}"
    return first_page or last_page or ""


def openalex_to_bibtex(work: dict) -> str:
    """Map an OpenAlex work response dict to a BibTeX @article string.

    Accepts the raw OpenAlex JSON dict (not a Work dataclass) so it can
    be called during parsing before the dataclass is constructed.
    """
    # Extract authors
    authorships = work.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in authorships
        if a.get("author", {}).get("display_name")
    ]

    title = work.get("title") or ""
    year = work.get("publication_year")

    # Journal from primary_location
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    journal = source.get("display_name") or ""

    # Biblio fields
    biblio = work.get("biblio") or {}
    volume = biblio.get("volume") or ""
    issue = biblio.get("issue") or ""
    first_page = biblio.get("first_page") or ""
    last_page = biblio.get("last_page") or ""

    # DOI - strip the URL prefix if present
    doi_raw = work.get("doi") or ""
    doi = doi_raw.replace("https://doi.org/", "")

    publisher = ""
    if location.get("source"):
        publisher = source.get("host_organization_name") or ""

    key = _make_bibtex_key(authors, year, title)
    pages = _format_pages(first_page, last_page)

    lines = [f"@article{{{key},"]

    fields: list[tuple[str, str]] = [
        ("title", f"{{{_escape_bibtex(title)}}}"),
        ("author", " and ".join(_escape_bibtex(a) for a in authors)),
        ("year", str(year) if year else ""),
        ("journal", _escape_bibtex(journal)),
        ("volume", volume),
        ("number", issue),
        ("pages", pages),
        ("doi", doi),
        ("publisher", _escape_bibtex(publisher)),
    ]

    for name, value in fields:
        if value:
            lines.append(f"  {name} = {{{value}}},")

    lines.append("}")
    return "\n".join(lines)
