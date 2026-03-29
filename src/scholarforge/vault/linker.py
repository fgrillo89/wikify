"""Automatic topic/keyword extraction and vault linking.

Extracts topics from:
1. Author-declared keywords (from "Keywords:" sections in paper text)
2. TF-IDF-style extraction: terms that appear in a paper's abstract but are
   distinctive (not in every paper)

No hardcoded keyword dictionaries — topics emerge from the corpus.
"""

from __future__ import annotations

import re
from collections import Counter

from scholarforge.store.models import Paper
from scholarforge.vault.templates import topic_note
from scholarforge.vault.writer import _sanitize_filename, vault_dir

# ── Keyword extraction ───────────────────────────────────────────────────────

# Words too common or too short to be useful topics
_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "as",
    "is",
    "was",
    "are",
    "were",
    "been",
    "be",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "shall",
    "can",
    "not",
    "no",
    "nor",
    "so",
    "if",
    "than",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "we",
    "our",
    "they",
    "their",
    "he",
    "she",
    "him",
    "her",
    "his",
    "which",
    "what",
    "who",
    "whom",
    "where",
    "when",
    "how",
    "why",
    "each",
    "every",
    "all",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "also",
    "very",
    "just",
    "about",
    "above",
    "after",
    "again",
    "between",
    "into",
    "through",
    "during",
    "before",
    "below",
    "up",
    "down",
    "out",
    "off",
    "over",
    "under",
    "further",
    "then",
    "once",
    "here",
    "there",
    "any",
    "same",
    "own",
    "too",
    "using",
    "based",
    "used",
    "shows",
    "show",
    "shown",
    "found",
    "results",
    "result",
    "however",
    "therefore",
    "thus",
    "hence",
    "while",
    "since",
    "although",
    "because",
    "due",
    "et",
    "al",
    "fig",
    "figure",
    "table",
    "ref",
    "respectively",
    "i.e",
    "e.g",
    "etc",
    "new",
    "high",
    "low",
    "large",
    "small",
    "two",
    "three",
    "one",
    "first",
    "second",
    "different",
    "several",
    "many",
    "well",
    "even",
    "still",
    "much",
    "paper",
    "study",
    "work",
    "proposed",
    "approach",
    "method",
    "methods",
    "technique",
    "techniques",
    "important",
    "significant",
    "increasing",
    "devices",
    "device",
    "structure",
    "structures",
    "properties",
    "property",
    "materials",
    "material",
    "film",
    "films",
    "layer",
    "layers",
    "effect",
    "effects",
    "performance",
    "obtained",
    "reported",
    "applied",
    "process",
    "fabricated",
    "fabrication",
    "measured",
    "measurement",
    "analysis",
    "compared",
    "corresponding",
    "including",
    "recent",
    "recently",
    "demonstrated",
    "showing",
    "various",
    "possible",
    "potential",
    "computing",
    "applications",
    "application",
    "analog",
    "digital",
    "integrated",
    "integration",
    "circuit",
    "circuits",
    "array",
    "arrays",
    "system",
    "systems",
    "network",
    "networks",
    "voltage",
    "current",
    "resistance",
    "design",
    "model",
    "data",
    "energy",
    "power",
    "speed",
    "time",
    "state",
    "states",
    "level",
    "levels",
    "value",
    "values",
    "size",
    "type",
    "order",
    "number",
    "range",
    "ratio",
    "rate",
    "achieved",
    "achieve",
    "achieving",
    "attractive",
    "promising",
    "excellent",
    "superior",
    "conventional",
    "traditional",
    "provides",
    "enable",
    "enables",
    "enabled",
    "ultra-compact",
    "folded",
    "meanwhile",
    "pure",
    "good",
    "poor",
    "better",
    "worse",
    "approved",
    "release",
    "distribution",
    "unlimited",
    "access",
    "average",
    "maximum",
    "minimum",
    "total",
    "region",
    "regions",
    "area",
    "operation",
    "operations",
    "operating",
    "wafer",
    "computer",
    "memory",
    "silicon",
    "metal",
    "oxide",
}


def _extract_declared_keywords(text: str) -> list[str]:
    """Extract keywords from a 'Keywords:' or 'Index Terms:' section."""
    # Match patterns like:
    #   Keywords: word1, word2, word3
    #   KEYWORDS: word1. word2. word3
    #   Index Terms— word1, word2
    pattern = re.compile(
        r"(?:keywords?|index\s+terms|key\s+words)\s*[:\-—.]+\s*(.+?)(?:\n\s*\n|\n#+|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text[:10000])
    if not match:
        return []

    raw = match.group(1).strip()
    # Clean markdown formatting and bracket artifacts from PDF extraction
    raw = re.sub(r"[*_`]+", "", raw)
    raw = re.sub(r"\[+\]?", "", raw)  # remove [ and [] artifacts
    raw = re.sub(r"\]+", "", raw)  # remove remaining ]
    # Split on comma, semicolon, period (common in IEEE), or bullet markers
    parts = re.split(r"[,;]|\.\s+|\n\s*[-•]\s*", raw)

    keywords = []
    for part in parts:
        kw = part.strip().rstrip(".")
        # Remove leading numbers/bullets
        kw = re.sub(r"^\d+[.)]\s*", "", kw)
        kw = kw.strip()
        if 2 < len(kw) < 60 and not kw[0].isdigit() and len(kw.split()) <= 5:
            keywords.append(kw.lower())
    return keywords


def _extract_distinctive_terms(abstract: str, all_abstracts: list[str]) -> list[str]:
    """Extract bigram terms from abstract that are distinctive across the corpus.

    Only returns bigrams (two-word phrases) — single words are too noisy.
    Filters out phrases where both words are stopwords.
    """
    if not abstract or len(abstract) < 50:
        return []

    def tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z][a-z0-9/-]{2,}", text.lower())

    words = tokenize(abstract)

    # Only extract bigrams where BOTH words are not stopwords
    bigrams: list[str] = []
    for i in range(len(words) - 1):
        w1, w2 = words[i], words[i + 1]
        if w1 in _STOPWORDS or w2 in _STOPWORDS:
            continue
        bigram = f"{w1} {w2}"
        if len(bigram) > 8:
            bigrams.append(bigram)

    if not bigrams:
        return []

    # Count document frequency across all abstracts
    doc_count: Counter[str] = Counter()
    for other_abstract in all_abstracts:
        other_lower = other_abstract.lower()
        seen: set[str] = set()
        for term in bigrams:
            if term not in seen and term in other_lower:
                doc_count[term] += 1
                seen.add(term)

    # Keep bigrams present in 2+ papers (signal, not noise) but < 50% of corpus
    min_df = 2
    max_df = max(3, len(all_abstracts) * 0.5)
    local_counts = Counter(bigrams)

    distinctive = []
    for term, _count in local_counts.most_common():
        df = doc_count.get(term, 0)
        if min_df <= df < max_df:
            distinctive.append(term)
        if len(distinctive) >= 6:
            break

    return distinctive


def extract_topics(paper: Paper, text: str, all_abstracts: list[str]) -> list[str]:
    """Extract topics for a paper from declared keywords + distinctive terms."""
    # Source 1: declared keywords from the paper text
    declared = _extract_declared_keywords(text)

    # Source 2: distinctive terms from the abstract
    distinctive = _extract_distinctive_terms(paper.abstract or "", all_abstracts)

    # Merge, deduplicate, prefer declared keywords
    seen: set[str] = set()
    topics: list[str] = []
    for kw in declared + distinctive:
        normalized = kw.strip().lower()
        if normalized not in seen:
            seen.add(normalized)
            # Title-case for display
            display = kw.strip().title() if len(kw) > 4 else kw.strip().upper()
            topics.append(display)

    return topics[:15]  # Cap at 15 topics per paper


# ── Writing hub notes ────────────────────────────────────────────────────────


def write_topic_notes(topic_papers: dict[str, list[str]]) -> int:
    """Write/update topic notes. Returns count written."""
    vd = vault_dir()
    (vd / "topics").mkdir(parents=True, exist_ok=True)

    count = 0
    for topic_name, papers in topic_papers.items():
        safe_name = _sanitize_filename(topic_name)
        note_path = vd / "topics" / f"{safe_name}.md"

        existing_papers: list[str] = []
        if note_path.exists():
            content = note_path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                m = re.match(r"- \[\[papers/(.+?)\]\]", line)
                if m:
                    existing_papers.append(m.group(1))

        all_papers = list(dict.fromkeys(existing_papers + papers))
        note_content = topic_note(topic_name, all_papers)
        note_path.write_text(note_content, encoding="utf-8")
        count += 1

    return count


# ── Compute + link ───────────────────────────────────────────────────────────


def compute_all_links(
    papers_with_text: list[tuple[Paper, str]],
) -> dict[str, dict[str, list[str]]]:
    """Compute topics for all papers automatically.

    Returns {paper_id: {"topics": [...]}}
    """
    # Gather all abstracts for TF-IDF-style distinctiveness
    all_abstracts = [p.abstract or "" for p, _ in papers_with_text if p.abstract]

    result: dict[str, dict[str, list[str]]] = {}
    for paper, text in papers_with_text:
        search_text = f"{paper.title or ''} {paper.abstract or ''} {text}"
        topics = extract_topics(paper, search_text, all_abstracts)
        result[paper.id] = {"topics": topics}
    return result
