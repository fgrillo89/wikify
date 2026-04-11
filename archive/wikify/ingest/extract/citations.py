"""Extract bibliography/reference entries from academic paper markdown text."""

from __future__ import annotations

import hashlib
import re

from wikify.core.store.models import Citation

# Heading pattern for references/bibliography sections
_REFS_HEADING_RE = re.compile(
    r"^(#{1,3})\s*(references|bibliography|works cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Numbered entry patterns: [1] or 1.
_NUMBERED_ENTRY_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+", re.MULTILINE)

# Markdown formatting artifacts to strip
_MD_BOLD_RE = re.compile(r"\*{1,2}(.+?)\*{1,2}")
_MD_ITALIC_RE = re.compile(r"_{1,2}(.+?)_{1,2}")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`+([^`]*)`+")


def _clean_markdown(text: str) -> str:
    """Strip common markdown formatting artifacts from raw text."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    return text.strip()


def _find_refs_section(md_text: str) -> str | None:
    """Return the text of the references section, or None if not found."""
    match = _REFS_HEADING_RE.search(md_text)
    if match is None:
        return None

    heading_level = len(match.group(1))  # number of '#' characters
    start = match.end()

    # Find the next heading at the same or higher level (fewer #'s)
    next_heading_re = re.compile(
        rf"^#{{1,{heading_level}}}\s+\S",
        re.MULTILINE,
    )
    next_match = next_heading_re.search(md_text, start)
    end = next_match.start() if next_match else len(md_text)

    return md_text[start:end]


def _split_entries(section_text: str) -> list[str]:
    """Split the references section into individual entry strings."""
    # Check whether this looks like a numbered list
    if _NUMBERED_ENTRY_RE.search(section_text):
        # Split on numbered markers; keep the marker with the entry
        parts = _NUMBERED_ENTRY_RE.split(section_text)
        # First part is text before the first numbered entry (discard)
        entries = []
        for part in parts[1:]:
            entries.append(part.strip())
        return entries
    else:
        # Unnumbered: entries separated by blank lines
        blocks = re.split(r"\n\s*\n", section_text)
        return [b.strip() for b in blocks]


def extract_citations(md_text: str, paper_id: str) -> list[Citation]:
    """Extract bibliography entries from academic paper markdown text.

    Args:
        md_text: Full markdown text of the paper.
        paper_id: Identifier of the parent paper (used in citation IDs).

    Returns:
        List of Citation objects, one per reference entry found.
        Returns an empty list if no references section is detected.
    """
    section = _find_refs_section(md_text)
    if section is None:
        return []

    raw_entries = _split_entries(section)

    citations: list[Citation] = []
    for raw in raw_entries:
        cleaned = _clean_markdown(raw)
        if len(cleaned) < 20:
            # Too short — likely noise or an empty block
            continue

        truncated = cleaned[:1000]
        digest = hashlib.sha256((paper_id + truncated).encode()).hexdigest()[:16]

        citations.append(
            Citation(
                id=digest,
                paper_id=paper_id,
                cited_paper_id=None,
                raw_text=truncated,
                bibtex=None,
                csl_json=None,
                context_chunk_id=None,
            )
        )

    return citations
