"""Reference resolver: converts [REF:display_name] markers to numbered citations."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wikify.store.models import Paper

logger = logging.getLogger(__name__)


class ReferenceResolver:
    """Resolve [REF:...] semantic markers to sequential numbered citations.

    The LLM emits [REF:display_name] during writing. This resolver:
    1. Finds all markers in order of first appearance
    2. Matches each to a Paper (exact, then fuzzy)
    3. Replaces with [1], [2], etc.
    4. Builds a bibliography section
    """

    def __init__(self, papers: list[Paper]):
        self._papers = papers
        self._name_to_paper: dict[str, Paper] = {}
        self._fuzzy_index: list[tuple[str, str, Paper]] = []  # (year, last_name, paper)

        for p in papers:
            marker = p.display_name()
            self._name_to_paper[marker.lower()] = p
            # Build fuzzy index: year + first author last name
            authors = p.parsed_authors
            last_name = authors[0].split()[-1].lower() if authors else ""
            year_str = str(p.year) if p.year else ""
            self._fuzzy_index.append((year_str, last_name, p))

    def resolve(self, raw_markdown: str) -> tuple[str, list[Paper]]:
        """Replace [REF:...] markers with [N] numbers.

        Returns (numbered_markdown, ordered_papers) where ordered_papers
        is the reference list in order of first citation.
        """
        # Find all [REF:...] markers
        marker_pattern = re.compile(r"\[REF:([^\]]+)\]")
        seen_papers: dict[str, int] = {}  # paper_id -> ref number
        ordered: list[Paper] = []

        def _replace_marker(match: re.Match) -> str:
            display_name = match.group(1).strip()
            paper = self._match_paper(display_name)
            if paper is None:
                logger.warning("Could not resolve REF marker: %r", display_name)
                return f"[?:{display_name}]"
            if paper.id not in seen_papers:
                seen_papers[paper.id] = len(ordered) + 1
                ordered.append(paper)
            return f"[{seen_papers[paper.id]}]"

        numbered = marker_pattern.sub(_replace_marker, raw_markdown)
        return numbered, ordered

    def build_bibliography(
        self,
        ordered_papers: list[Paper],
        style: str = "numbered",
        reference_format: str = "",
    ) -> str:
        """Format a bibliography section from ordered papers.

        If *reference_format* is provided (from a JournalProfile), it is used
        as a Python format string with keys: number, authors, title, journal,
        volume, pages, year, doi.  Otherwise falls back to a default numbered
        style.
        """
        lines: list[str] = []
        for i, paper in enumerate(ordered_papers, 1):
            entry = _format_reference(paper, i, style, reference_format)
            lines.append(entry)
        return "\n".join(lines)

    def _match_paper(self, display_name: str) -> Paper | None:
        """Match a display_name string to a Paper. Exact first, then fuzzy.

        Matching strategy (in order):
        1. Exact case-insensitive match against display_name()
        2. Prefix match: the LLM marker may be a prefix of a truncated display_name
        3. Fuzzy: year + author last name + title word overlap (score >= 4 required)
        """
        key = display_name.lower().strip()

        # 1. Exact match (case-insensitive)
        if key in self._name_to_paper:
            return self._name_to_paper[key]

        # 2. Prefix match: handle truncated display names (display_name caps at 200 chars)
        for stored_key, paper in self._name_to_paper.items():
            if stored_key.startswith(key) or key.startswith(stored_key):
                return paper

        # 3. Fuzzy: extract year and author from the marker
        year_match = re.search(r"\b((?:19|20)\d{2})\b", display_name)
        year = year_match.group(1) if year_match else ""

        # First word that looks like a name (capitalized, not a number)
        words = display_name.split()
        author_word = ""
        for w in words:
            cleaned = w.strip(",-.")
            if cleaned and cleaned[0].isupper() and not cleaned.isdigit():
                author_word = cleaned.lower()
                break

        # Score each paper — require at least year+author OR strong title overlap
        stopwords = {"the", "of", "a", "an", "and", "in", "for", "on", "with", "by"}
        best: Paper | None = None
        best_score = 0
        for p_year, p_last, paper in self._fuzzy_index:
            score = 0
            if year and p_year == year:
                score += 3
            if author_word and p_last and (author_word in p_last or p_last in author_word):
                score += 3
            # Title word overlap (content words only)
            title_words = set(paper.title.lower().split()) - stopwords
            marker_words = set(display_name.lower().split()) - stopwords
            overlap = len(title_words & marker_words)
            score += min(overlap, 3)
            if score > best_score:
                best_score = score
                best = paper

        # Require score >= 4 to avoid year-only false positives
        if best_score < 4:
            logger.warning("REF marker unresolved (best_score=%d): %r", best_score, display_name)
            return None
        return best


def _format_reference(
    paper: Paper,
    number: int,
    style: str = "numbered",
    reference_format: str = "",
) -> str:
    """Format a single reference entry.

    If *reference_format* is a non-empty Python format string (from a
    JournalProfile), it is used directly.  Missing fields are replaced
    with empty strings so the template never raises KeyError.
    """
    authors = paper.parsed_authors
    if len(authors) > 3:
        author_str = f"{authors[0]}, {authors[1]}, {authors[2]} et al."
    elif authors:
        author_str = ", ".join(authors)
    else:
        author_str = "Unknown"

    title = paper.title or "Untitled"
    year = paper.year or "n.d."
    doi_str = f"https://doi.org/{paper.doi}" if paper.doi else ""

    # Use journal profile format if provided
    if reference_format:
        return (
            reference_format.format(
                number=number,
                authors=author_str,
                title=title,
                journal="",  # not available in Paper model yet
                volume="",
                pages="",
                year=year,
                doi=doi_str,
            ).rstrip(", .")
            + "."
        )

    if style == "numbered":
        return f"[{number}] {author_str}. {title}. ({year}). {doi_str}".rstrip()
    return f"{author_str}. {title}. ({year}). {doi_str}".rstrip()
