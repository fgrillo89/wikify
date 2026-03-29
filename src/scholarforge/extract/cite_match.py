"""Match raw citation strings against papers in the corpus.

Uses fuzzy matching on author last name + year + title keywords to resolve
citations to known paper IDs.
"""

from __future__ import annotations

import json
import re

from scholarforge.store.models import Paper


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_year(text: str) -> int | None:
    """Extract a 4-digit year (1950-2030) from citation text."""
    years = re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", text)
    return int(years[0]) if years else None


def _title_words(title: str, min_len: int = 4) -> set[str]:
    """Extract meaningful words from a title (lowercase, >= min_len chars)."""
    return {w for w in _normalize(title).split() if len(w) >= min_len}


def build_citation_graph(
    corpus: list[Paper],
    citations_by_paper: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Build a citation graph: {citing_paper_id: [cited_paper_ids]}.

    Matches each paper's raw citation strings against the corpus.
    Filters self-citations.
    """
    result: dict[str, list[str]] = {}
    for citing_id, raw_texts in citations_by_paper.items():
        matches = match_citations_to_corpus(raw_texts, corpus)
        cited_ids = list({mid for mid in matches.values() if mid != citing_id})
        if cited_ids:
            result[citing_id] = cited_ids
    return result


def match_citations_to_corpus(
    raw_citations: list[str],
    corpus: list[Paper],
) -> dict[str, str]:
    """Match raw citation strings to corpus papers.

    Returns {citation_raw_text_prefix: paper_id} for successful matches.
    A match requires: year match + (author last name OR >= 2 title words).
    """
    # Build corpus index
    corpus_entries: list[tuple[Paper, str, set[str], list[str]]] = []
    for paper in corpus:
        if not paper.title:
            continue
        year = paper.year
        words = _title_words(paper.title)

        # Extract author last names
        authors = json.loads(paper.authors) if paper.authors else []
        last_names = []
        for a in authors:
            parts = a.strip().split()
            if parts:
                last_names.append(_normalize(parts[-1]))

        corpus_entries.append((paper, str(year) if year else "", words, last_names))

    matches: dict[str, str] = {}

    for raw in raw_citations:
        norm = _normalize(raw)
        cite_year = _extract_year(raw)

        best_match: Paper | None = None
        best_score = 0

        for paper, year_str, title_words, last_names in corpus_entries:
            # Year must match
            if not cite_year or cite_year != paper.year:
                continue

            score = 0

            # Check author last name match
            for name in last_names:
                if len(name) >= 3 and name in norm:
                    score += 3
                    break

            # Check title word overlap
            cite_words = set(norm.split())
            overlap = title_words & cite_words
            score += len(overlap)

            # Need at least author match OR 2+ title words
            if score >= 3 and score > best_score:
                best_score = score
                best_match = paper

        if best_match:
            # Use first 80 chars as key (matches citation ID generation)
            matches[raw[:80]] = best_match.id

    return matches
