"""Metadata extraction from PDF documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def extract_metadata(doc, md_text: str, filename: str) -> dict[str, Any]:
    """Extract title, authors, summary, year, DOI from a PDF document.

    Args:
        doc: A fitz.Document instance.
        md_text: The markdown text extracted by pymupdf4llm.
        filename: Original filename (fallback for title).

    Returns:
        Dict with keys: title, authors, summary, year, doi.
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

    # Summary: look for "Abstract" or "Summary" section in markdown
    summary = _extract_summary(md_text)

    # Year: prefer filename year, then metadata date
    year = fn_year or _extract_year(meta)

    # DOI: search in first page text
    doi = _extract_doi(md_text[:3000])

    return {
        "title": title,
        "authors": authors,
        "summary": summary,
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


@dataclass
class _Slide:
    """A single parsed slide from PPTX markdown."""

    index: int
    title: str
    body: str
    notes: str


def _parse_slides(md_text: str) -> list[_Slide]:
    """Split PPTX markdown into individual slides with title, body, and notes."""
    # Split on slide headings: ## Slide N: Title  or  ## Slide N
    slide_splits = re.split(r"(?=^## (?:Slide \d+))", md_text, flags=re.MULTILINE)
    slides: list[_Slide] = []

    for block in slide_splits:
        block = block.strip()
        if not block:
            continue
        # Extract heading
        heading_match = re.match(r"^## Slide (\d+)(?::\s*(.+))?$", block, re.MULTILINE)
        if not heading_match:
            continue

        index = int(heading_match.group(1))
        title = (heading_match.group(2) or "").strip()
        rest = block[heading_match.end() :].strip()

        # Separate notes (blockquote lines starting with >) from body
        body_lines: list[str] = []
        note_lines: list[str] = []
        for line in rest.splitlines():
            if line.strip().startswith(">"):
                note_lines.append(line.strip().lstrip("> ").strip())
            else:
                body_lines.append(line)

        body = "\n".join(body_lines).strip()
        notes = " ".join(note_lines).strip()
        # Remove "Note:" prefix from notes
        notes = re.sub(r"^Note:\s*", "", notes, flags=re.IGNORECASE).strip()

        slides.append(_Slide(index=index, title=title, body=body, notes=notes))

    return slides


def _is_conclusion_slide(slide: _Slide) -> bool:
    """Check if a slide looks like a conclusion/summary slide."""
    title_lower = slide.title.lower()
    conclusion_keywords = (
        "conclusion",
        "concluding",
        "summary",
        "takeaway",
        "key finding",
        "wrap up",
        "wrap-up",
        "closing",
        "final",
        "outlook",
        "future work",
    )
    return any(kw in title_lower for kw in conclusion_keywords)


def _synthesize_slide_summary(slides: list[_Slide]) -> str:
    """Build a summary from first 3 slides + conclusion slides from last 3.

    Structure: title context from opening slides, then conclusion content.
    """
    parts: list[str] = []

    # ── Opening: first 3 slides (title + body + notes) ──────────────────────
    for slide in slides[:3]:
        slide_text = ""
        if slide.title:
            slide_text = slide.title
        body_clean = _clean_markdown(slide.body)
        if body_clean:
            # Take first ~150 words of body to keep summary concise
            words = body_clean.split()
            excerpt = " ".join(words[:150])
            slide_text = f"{slide_text}. {excerpt}" if slide_text else excerpt
        if slide.notes:
            # Speaker notes often have the real explanation
            notes_clean = _clean_markdown(slide.notes)
            words = notes_clean.split()
            notes_excerpt = " ".join(words[:80])
            slide_text = f"{slide_text} {notes_excerpt}" if slide_text else notes_excerpt
        if slide_text:
            parts.append(slide_text.strip())

    # ── Closing: check last 3 slides for conclusions ────────────────────────
    tail_slides = slides[-3:] if len(slides) > 3 else []
    conclusion_parts: list[str] = []
    for slide in tail_slides:
        if _is_conclusion_slide(slide):
            conclusion_text = ""
            if slide.title:
                conclusion_text = slide.title
            body_clean = _clean_markdown(slide.body)
            if body_clean:
                words = body_clean.split()
                excerpt = " ".join(words[:200])
                conclusion_text = f"{conclusion_text}. {excerpt}" if conclusion_text else excerpt
            if slide.notes:
                notes_clean = _clean_markdown(slide.notes)
                words = notes_clean.split()
                notes_excerpt = " ".join(words[:100])
                conclusion_text = (
                    f"{conclusion_text} {notes_excerpt}" if conclusion_text else notes_excerpt
                )
            if conclusion_text:
                conclusion_parts.append(conclusion_text.strip())

    if conclusion_parts:
        parts.append("Conclusions: " + ". ".join(conclusion_parts))

    if not parts:
        return ""

    # Join and clean up
    text = ". ".join(parts)
    # Collapse multiple periods/spaces
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_summary(md_text: str) -> str | None:
    """Extract a document summary from markdown text.

    Handles papers (abstract), reports (executive summary), slides
    (concatenated slide titles + bullets), and unstructured notes
    (first ~400 words of content).

    Strategies tried in order:
    1. Slide-aware: first 3 slides + conclusion slides from last 3
    2. Labeled section (Abstract, Summary, Executive Summary, Overview, Scope)
    3. First substantial prose paragraph (>100 chars with sentence punctuation)
    4. Fallback: first ~400 words of body text
    """
    # ── Strategy 1: Slide-aware synthesis ────────────────────────────────────
    # Check for slide decks first — their body text can false-positive on labels
    slides = _parse_slides(md_text)
    if len(slides) >= 3:
        summary = _synthesize_slide_summary(slides)
        if summary and len(summary) > 50:
            return summary

    # Strip markdown formatting for matching purposes
    search_text = _clean_markdown(md_text[:10000])

    # ── Strategy 2: Labeled section ──────────────────────────────────────────
    # Matches heading or inline label for abstract-like sections
    label_re = re.compile(
        r"(?:^|\n)\s*(?:#+\s*)?"
        r"(?:abstract|summary|executive\s+summary|overview|scope|synopsis"
        r"|project\s+(?:summary|description)|purpose)"
        r"\s*[:\-—.]*\s*",
        re.IGNORECASE,
    )
    match = label_re.search(search_text)
    if match:
        after_label = search_text[match.end() :]
        # End at: next heading, Keywords, Introduction, Index Terms, or similar
        end_re = re.compile(
            r"\n\s*(?:#+\s+|(?:keywords?|introduction|index\s+terms"
            r"|i\.\s+introduction|table\s+of\s+contents|background)\b)",
            re.IGNORECASE,
        )
        end_match = end_re.search(after_label)
        if end_match:
            text = after_label[: end_match.start()].strip()
        else:
            text = after_label[:3000].strip()

        # Clean up: collapse line breaks that aren't paragraph breaks
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        text = re.sub(r"\n{2,}", "\n\n", text)
        paragraphs_in_abstract = text.split("\n\n")
        text = paragraphs_in_abstract[0].strip()

        # If too short, concatenate more paragraphs
        word_count = len(text.split())
        if word_count < 50 and len(paragraphs_in_abstract) > 1:
            for extra in paragraphs_in_abstract[1:]:
                extra = extra.strip()
                if _is_noise_paragraph(extra):
                    break
                text += " " + extra
                if len(text.split()) >= 50:
                    break

        if len(text) > 50 and not _is_noise_paragraph(text):
            return _clean_markdown(text)

    # ── Strategy 3: First substantial prose paragraph ──────────────────────
    paragraphs = re.split(r"\n\s*\n", search_text)
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        if _is_noise_paragraph(para):
            continue
        if len(para) > 100 and re.search(r"[.!?]", para):
            return _clean_markdown(para)

    # ── Strategy 4: Fallback — first ~400 words of body text ─────────────
    body_words: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        if _is_noise_paragraph(para):
            continue
        if len(para) < 10:
            continue
        body_words.extend(para.split())
        if len(body_words) >= 400:
            break
    if body_words:
        text = " ".join(body_words[:400])
        last_period = max(text.rfind(". "), text.rfind(".\n"), text.rfind("."))
        if last_period > 50:
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
