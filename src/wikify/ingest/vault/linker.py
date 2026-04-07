"""Automatic topic/keyword extraction and vault linking.

Strategy:
1. Extract declared keywords from every paper's "Keywords:" / "Index Terms:" section
2. Build a corpus vocabulary from ALL declared keywords
3. For papers without declared keywords, match corpus vocabulary against their text
4. Normalize: merge plurals, absorb substrings into longer forms

No hardcoded topic dictionaries — topics emerge from what authors declare.
"""

from __future__ import annotations

import re

from wikify.core.store.models import Paper
from wikify.ingest.vault.templates import topic_note
from wikify.ingest.vault.writer import _sanitize_filename, vault_dir

# ── Keyword extraction ───────────────────────────────────────────────────────

# First word of a keyword phrase must not be a function word, conjunction,
# preposition, or participial opener — these indicate sentence fragments, not topics.
_KEYWORD_BAD_STARTERS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "based",
        "being",
        "both",
        "but",
        "by",
        "can",
        "closely",
        "demonstrating",
        "developing",
        "due",
        "each",
        "exhibiting",
        "for",
        "from",
        "furthermore",
        "however",
        "in",
        "including",
        "inspired",
        "it",
        "its",
        "notably",
        "of",
        "offering",
        "on",
        "or",
        "originally",
        "our",
        "providing",
        "showing",
        "such",
        "the",
        "these",
        "this",
        "through",
        "to",
        "toward",
        "thus",
        "using",
        "via",
        "we",
        "where",
        "which",
        "while",
        "with",
    }
)

# Single-word strings that are clearly not academic topics
_KEYWORD_SINGLE_STOP: frozenset[str] = frozenset(
    {
        "abstract",
        "additionally",
        "also",
        "although",
        "and",
        "both",
        "figure",
        "furthermore",
        "here",
        "however",
        "importantly",
        "initially",
        "introduction",
        "journal",
        "keywords",
        "likewise",
        "moreover",
        "notably",
        "or",
        "previously",
        "references",
        "respectively",
        "similarly",
        "subsequently",
        "table",
        "therefore",
        "thus",
        "vol",
    }
)

# Month names — dates are not topics
_MONTH_NAMES: frozenset[str] = frozenset(
    {
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
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    }
)

# Inner-phrase words that indicate a sentence fragment, not a noun phrase
_FRAGMENT_MARKERS: tuple[str, ...] = (
    " this ",
    " these ",
    " our ",
    " their ",
    " is ",
    " are ",
    " was ",
    " were ",
    " has ",
    " have ",
    " had ",
    " its ",
    " from ",  # copyright / provenance phrases ("content from X")
)

# Any word in this set appearing anywhere in the phrase signals bibliographic metadata
_METADATA_WORDS: frozenset[str] = frozenset(
    {
        "doi",
        "issn",
        "isbn",
        "arxiv",
        "preprint",
        "citation",
        "copyright",
        "licence",
        "license",
        "cc-by",
        "vol",
        "issue",
        "pp",
        "pages",
    }
)


def _is_valid_keyword(kw: str) -> bool:
    """Return True if kw looks like an academic keyword, not a sentence fragment.

    Applies structural checks that generalize across all research domains:
    - Length and word count bounds
    - First word must not be a function word / conjunction / participial opener
    - Single-word stop terms rejected
    - Inner-phrase sentence markers rejected
    - Date-like patterns rejected
    - HTML/PDF artifacts rejected
    """
    if len(kw) < 3 or len(kw) > 50:
        return False
    # Reject HTML tags and PDF line-break artifacts
    if re.search(r"<\s*br\s*/?\s*>|<br>", kw, re.IGNORECASE):
        return False
    # Reject hyphenated line-break artifacts (e.g. "Further-\nMore" -> "Further-More")
    if re.search(r"\w-\s*[A-Z]", kw) and len(kw.split()) <= 2:
        return False
    # Reject pipe characters (PDF table artifacts)
    if "|" in kw:
        return False
    # Reject "all rights reserved" and similar copyright noise
    if re.search(r"(?i)rights?\s+reserved|copyright|permission", kw):
        return False
    # Reject keywords ending with "article", "letter", "paper" (journal noise)
    if re.search(r"(?i)\b(?:article|letter|paper)\s*$", kw):
        return False
    words = kw.split()
    if not words or len(words) > 5:
        return False
    if words[0][0].isdigit():
        return False
    first = words[0].lower()
    if first in _KEYWORD_BAD_STARTERS:
        return False
    if len(words) == 1 and first in _KEYWORD_SINGLE_STOP:
        return False
    if len(words) == 1 and first in _MONTH_NAMES:
        return False
    # Reject "month year" date fragments
    if len(words) == 2 and words[0].lower() in _MONTH_NAMES and words[1].isdigit():
        return False
    # Reject unbalanced parentheses / bracket artifacts from PDF extraction
    if kw.count("(") != kw.count(")"):
        return False
    # Reject sentence fragments indicated by inner function words
    kw_padded = f" {kw.lower()} "
    if any(marker in kw_padded for marker in _FRAGMENT_MARKERS):
        return False
    # Reject phrases containing bibliographic metadata words
    kw_words = set(kw.lower().split())
    if kw_words & _METADATA_WORDS:
        return False
    return True


def _sanitize_keyword(kw: str) -> str:
    """Clean PDF artifacts from a keyword before validation.

    Fixes <br> tags, hyphenated line breaks, and trailing noise.
    """
    # Remove HTML <br> tags and rejoin
    kw = re.sub(r"<\s*br\s*/?\s*>", " ", kw, flags=re.IGNORECASE)
    # Fix hyphenated line breaks: "Further-\n  More" or "Further- More" -> "Furthermore"
    kw = re.sub(r"(\w)-\s+([a-z])", lambda m: m.group(1) + m.group(2), kw)
    # Collapse whitespace
    kw = re.sub(r"\s+", " ", kw).strip()
    # Strip trailing pipe or period
    kw = kw.rstrip("|. ")
    return kw


def _extract_declared_keywords(text: str) -> list[str]:
    """Extract keywords from a 'Keywords:' or 'Index Terms:' section."""
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
    raw = re.sub(r"\[+\]?", "", raw)
    raw = re.sub(r"\]+", "", raw)
    # Split on comma, semicolon, period (common in IEEE), or bullet markers
    parts = re.split(r"[,;]|\.\s+|\n\s*[-•]\s*", raw)

    keywords = []
    for part in parts:
        kw = part.strip().rstrip(".")
        kw = re.sub(r"^\d+[.)]\s*", "", kw)
        kw = _sanitize_keyword(kw)
        if _is_valid_keyword(kw):
            keywords.append(kw.lower())
    return keywords


def _normalize_topic(topic: str) -> str:
    """Normalize a topic string for deduplication.

    Lowercases, strips trailing 's'/'es' for simple plural handling.
    """
    t = topic.strip().lower()
    # Simple plural normalization
    if t.endswith("ies") and len(t) > 5:
        t = t[:-3] + "y"  # e.g., "vacancies" → "vacancy"
    elif t.endswith("ses") or t.endswith("xes") or t.endswith("zes"):
        t = t[:-2]  # e.g., "synapses" → "synapse"
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 4:
        t = t[:-1]  # e.g., "memristors" → "memristor"
    return t


def _word_stem(word: str) -> str:
    """Get the stem of a word (first 5 chars, lowercased)."""
    return word.lower()[:5]


def _deduplicate_topics(topics: list[str]) -> list[str]:
    """Deduplicate topics: merge plurals, absorb substrings, and merge stem variants.

    Rules applied in order:
    1. Normalize plurals → group by normalized form
    2. Literal substring absorption: "neuromorphic" absorbed by "neuromorphic computing"
    3. Stem absorption for single-word topics: "synapse" (stem "synap") absorbed by
       "synaptic device" (contains stem "synap") — only single-word topics get absorbed
       this way, to avoid merging multi-word topics that are genuinely distinct.
    """
    # Group by normalized form, keep the first (most common) spelling
    norm_to_display: dict[str, str] = {}
    for t in topics:
        norm = _normalize_topic(t)
        if norm not in norm_to_display:
            norm_to_display[norm] = t

    surviving = list(norm_to_display.values())
    absorbed: set[str] = set()

    for i, short in enumerate(surviving):
        short_lower = short.lower()
        short_words = short_lower.split()

        for j, long in enumerate(surviving):
            if i == j:
                continue
            long_lower = long.lower()

            # Rule 2: literal substring absorption
            if short_lower != long_lower and short_lower in long_lower:
                absorbed.add(short)
                break

            # Rule 3: stem absorption for single-word topics only
            if len(short_words) == 1 and len(long_lower.split()) > 1:
                stem = _word_stem(short_words[0])
                if len(stem) >= 5 and any(_word_stem(w) == stem for w in long_lower.split()):
                    absorbed.add(short)
                    break

    return [t for t in surviving if t not in absorbed]


def _compile_vocabulary_patterns(
    vocabulary: list[str],
) -> list[tuple[str, re.Pattern[str]]]:
    """Pre-compile vocabulary into (keyword, compiled_pattern) pairs.

    Sorted by length descending so longer (more specific) terms match first.
    """
    pairs = []
    for kw in sorted(vocabulary, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(kw.lower()) + r"\b")
        pairs.append((kw, pattern))
    return pairs


def _match_corpus_vocabulary(
    text: str,
    vocabulary: list[str],
    max_matches: int = 8,
    compiled_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[str]:
    """Find which corpus keywords appear in this paper's text.

    Only matches whole-phrase occurrences (word boundaries).
    Returns up to max_matches keywords, ordered by specificity (longer first).
    Pass compiled_patterns to avoid re-compiling on every call.
    """
    text_lower = text.lower()
    matches: list[str] = []

    patterns = compiled_patterns or _compile_vocabulary_patterns(vocabulary)
    for kw, pat in patterns:
        if pat.search(text_lower):
            matches.append(kw)
            if len(matches) >= max_matches:
                break

    return matches


def _to_display(kw: str) -> str:
    """Convert a keyword to display form (title case, short words uppercase)."""
    # Strip control characters that could break filenames
    kw = re.sub(r"[\x00-\x1f\x7f]+", " ", kw).strip()
    kw = re.sub(r"\s+", " ", kw)
    return kw.title() if len(kw) > 4 else kw.upper()


def extract_topics(
    paper: Paper,
    text: str,
    corpus_vocabulary: list[str],
    canonical_map: dict[str, str] | None = None,
    compiled_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[str]:
    """Extract topics for a paper.

    Uses declared keywords if available, otherwise matches against the corpus
    vocabulary (keywords declared by other papers). When canonical_map is
    provided, normalizes keywords to canonical forms (merging plurals etc.).
    Pass compiled_patterns to avoid re-compiling vocabulary regex on each call.
    """
    declared = _extract_declared_keywords(text)

    if declared:
        topics = declared
    else:
        # Fall back: match this paper's text against keywords from other papers
        search_text = f"{paper.title or ''} {paper.summary or ''} {text[:3000]}"
        topics = _match_corpus_vocabulary(
            search_text, corpus_vocabulary, compiled_patterns=compiled_patterns
        )

    # Normalize to canonical forms if available
    if canonical_map:
        topics = [canonical_map.get(_normalize_topic(t), t) for t in topics]

    display_topics = [_to_display(kw) for kw in topics]
    return _deduplicate_topics(display_topics)[:12]


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
) -> tuple[dict[str, dict[str, list[str]]], list[str], dict[str, list[str]]]:
    """Compute topics for all papers automatically.

    Two-pass approach:
    1. Extract declared keywords from all papers → build corpus vocabulary
    2. For each paper, use its declared keywords or match against the vocabulary

    Returns (per_paper_links, corpus_vocabulary, paper_declared) where
    paper_declared maps paper_id → list of declared keywords (empty list means
    the paper had no keywords section and topics came from corpus vocabulary).
    """
    # Pass 1: build corpus vocabulary from all declared keywords
    all_declared: list[str] = []
    paper_declared: dict[str, list[str]] = {}
    for paper, text in papers_with_text:
        search_text = f"{paper.title or ''} {paper.summary or ''} {text}"
        declared = _extract_declared_keywords(search_text)
        paper_declared[paper.id] = declared
        all_declared.extend(declared)

    # Deduplicate vocabulary (normalize plurals), pick longest form as canonical
    seen_norms: dict[str, str] = {}
    for kw in all_declared:
        norm = _normalize_topic(kw)
        if norm not in seen_norms or len(kw) > len(seen_norms[norm]):
            seen_norms[norm] = kw
    corpus_vocabulary = list(seen_norms.values())

    # Build canonical map: normalized_form → canonical_keyword
    canonical_map = dict(seen_norms)

    # Pre-compile vocabulary patterns once (avoids n * V re-compilations)
    compiled_patterns = _compile_vocabulary_patterns(corpus_vocabulary)

    # Pass 2: assign topics to each paper
    result: dict[str, dict[str, list[str]]] = {}
    for paper, text in papers_with_text:
        search_text = f"{paper.title or ''} {paper.summary or ''} {text}"
        topics = extract_topics(
            paper, search_text, corpus_vocabulary, canonical_map, compiled_patterns
        )
        result[paper.id] = {"topics": topics}

    # Pass 3: global normalization — merge plural variants only
    # (Substring absorption already happens per-paper; don't do it globally
    # because "Resistive Switching" ≠ "Analog Resistive Switching")
    all_global_topics: dict[str, str] = {}  # norm → display
    for v in result.values():
        for t in v["topics"]:
            norm = _normalize_topic(t)
            # Keep the most common display form (longest wins for ties)
            if norm not in all_global_topics or len(t) > len(all_global_topics[norm]):
                all_global_topics[norm] = t

    # Build rename map: old display → canonical display
    rename: dict[str, str] = {}
    for v in result.values():
        for t in v["topics"]:
            canonical = all_global_topics[_normalize_topic(t)]
            if t != canonical:
                rename[t] = canonical

    # Apply renames
    if rename:
        for paper_id in result:
            result[paper_id]["topics"] = list(
                dict.fromkeys(rename.get(t, t) for t in result[paper_id]["topics"])
            )

    return result, corpus_vocabulary, paper_declared
