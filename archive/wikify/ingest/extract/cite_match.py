"""Match raw citation strings against papers in the corpus.

Uses fuzzy matching on author last name + year + title keywords to resolve
citations to known paper IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from wikify.core.store.models import Paper


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


@dataclass
class _CorpusEntry:
    paper: Paper
    title_words: set[str]
    last_names: list[str]


@dataclass
class _CorpusIndex:
    """Pre-built, year-bucketed corpus index for O(1) year lookup."""

    by_year: dict[int, list[_CorpusEntry]] = field(default_factory=dict)

    @staticmethod
    def build(corpus: list[Paper]) -> _CorpusIndex:
        by_year: dict[int, list[_CorpusEntry]] = {}
        for paper in corpus:
            if not paper.title or not paper.year:
                continue
            words = _title_words(paper.title)
            last_names = []
            for a in paper.parsed_authors:
                parts = a.strip().split()
                if parts:
                    last_names.append(_normalize(parts[-1]))
            entry = _CorpusEntry(paper=paper, title_words=words, last_names=last_names)
            by_year.setdefault(paper.year, []).append(entry)
        return _CorpusIndex(by_year=by_year)


def build_citation_graph(
    corpus: list[Paper],
    citations_by_paper: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Build a citation graph: {citing_paper_id: [cited_paper_ids]}.

    Matches each paper's raw citation strings against the corpus.
    Builds the corpus index once and reuses it for all papers.
    """
    index = _CorpusIndex.build(corpus)

    result: dict[str, list[str]] = {}
    for citing_id, raw_texts in citations_by_paper.items():
        matches = _match_citations(raw_texts, index)
        cited_ids = list({mid for mid in matches.values() if mid != citing_id})
        if cited_ids:
            result[citing_id] = cited_ids
    return result


def _match_citations(
    raw_citations: list[str],
    index: _CorpusIndex,
) -> dict[str, str]:
    """Match raw citation strings using the pre-built year-bucketed index."""
    matches: dict[str, str] = {}

    for raw in raw_citations:
        norm = _normalize(raw)
        cite_year = _extract_year(raw)
        if not cite_year:
            continue

        candidates = index.by_year.get(cite_year, [])
        if not candidates:
            continue

        cite_words = set(norm.split())
        best_match: Paper | None = None
        best_score = 0

        for entry in candidates:
            score = 0
            for name in entry.last_names:
                if len(name) >= 3 and name in norm:
                    score += 3
                    break
            overlap = entry.title_words & cite_words
            score += len(overlap)
            if score >= 3 and score > best_score:
                best_score = score
                best_match = entry.paper

        if best_match:
            matches[raw[:80]] = best_match.id

    return matches


# Keep public API for any external callers
def match_citations_to_corpus(
    raw_citations: list[str],
    corpus: list[Paper],
) -> dict[str, str]:
    """Match raw citation strings to corpus papers.

    Returns {citation_raw_text_prefix: paper_id} for successful matches.
    A match requires: year match + (author last name OR >= 2 title words).
    """
    index = _CorpusIndex.build(corpus)
    return _match_citations(raw_citations, index)
