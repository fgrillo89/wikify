"""Detect the most likely field for a corpus from its topics.

The wikify library is general-purpose. Field-specific writing guides
(``prompts/fields/*.md``) should only fire when the corpus content
actually warrants them. This module scores each field's distinctive
keyword set against the corpus topic list and returns the best match,
or ``"generic"`` when no field clearly dominates.

Result is cached in ``<corpus>/field.txt`` so subsequent runs don't
re-detect.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from wikify_simple.paths import CorpusPaths
from wikify_simple.prompts import available_field_guides

_FIELDS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "fields"
_FIELD_CACHE_FILENAME = "field.txt"
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")

# Words too generic to be useful as field signal.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "this",
        "that",
        "are",
        "was",
        "were",
        "field",
        "guide",
        "writing",
        "paper",
        "papers",
        "work",
        "works",
        "research",
        "study",
        "studies",
        "report",
        "reports",
        "section",
        "sections",
        "conventions",
        "exemplary",
        "actionable",
        "instructions",
        "use",
        "used",
        "using",
        "general",
        "context",
        "open",
        "note",
        "notes",
        "prior",
        "style",
        "tone",
        "voice",
        "figure",
        "figures",
        "table",
        "tables",
        "authors",
        "year",
        "years",
        "data",
        "method",
        "methods",
        "result",
        "results",
        "discussion",
        "conclusion",
        "abstract",
        "standard",
        "primary",
        "secondary",
        "journal",
        "journals",
        "review",
        "reviews",
        "science",
        "sciences",
        "scientific",
    }
)


@lru_cache(maxsize=1)
def _field_keywords() -> dict[str, frozenset[str]]:
    """Return ``{field_name: {keyword, ...}}`` derived from each field guide.

    Extracts lowercase tokens from the "Field Conventions" and
    "Exemplary Papers" sections of each ``prompts/fields/<field>.md``.
    """
    out: dict[str, frozenset[str]] = {}
    for f in sorted(_FIELDS_DIR.glob("*.md")):
        name = f.stem
        if name == "generic":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        # Keep only the two distinctive sections.
        keep_lines: list[str] = []
        active = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                heading = stripped[3:].lower()
                active = "field conventions" in heading or "exemplary papers" in heading
                continue
            if active:
                keep_lines.append(line)
        body = " ".join(keep_lines).lower()
        tokens = {t for t in _WORD_RE.findall(body) if len(t) >= 4 and t not in _STOPWORDS}
        out[name] = frozenset(tokens)
    return out


def _score_topics(topics: list[str], keywords: frozenset[str]) -> int:
    """Return the number of distinct field keywords appearing in the
    corpus topic list (case-insensitive, token-based).
    """
    topic_tokens: set[str] = set()
    for t in topics:
        for tok in _WORD_RE.findall(t.lower()):
            if len(tok) >= 4 and tok not in _STOPWORDS:
                topic_tokens.add(tok)
    return len(topic_tokens & keywords)


def _load_topics(corpus: CorpusPaths) -> list[str]:
    path = corpus.topics_path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        topics = data.get("topics") or data.get("declared") or []
    else:
        topics = data
    return [str(t) for t in topics if isinstance(t, str)]


def _field_cache_path(corpus: CorpusPaths) -> Path:
    return corpus.root / _FIELD_CACHE_FILENAME


def detect_field(corpus: CorpusPaths) -> str:
    """Detect the dominant field for a corpus.

    Returns one of ``available_field_guides()``. Falls back to
    ``"generic"`` when no field scores clearly higher than the others.
    Result is cached at ``<corpus>/field.txt``.
    """
    cache = _field_cache_path(corpus)
    if cache.exists():
        value = cache.read_text(encoding="utf-8").strip()
        if value in available_field_guides():
            return value

    field, _ = _score_corpus(corpus)
    try:
        cache.write_text(field, encoding="utf-8")
    except OSError:
        pass
    return field


def _score_corpus(corpus: CorpusPaths) -> tuple[str, list[tuple[str, int]]]:
    topics = _load_topics(corpus)
    if not topics:
        return "generic", []
    keywords = _field_keywords()
    scores: list[tuple[str, int]] = sorted(
        ((name, _score_topics(topics, kws)) for name, kws in keywords.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not scores:
        return "generic", scores
    top_name, top_score = scores[0]
    runner_score = scores[1][1] if len(scores) > 1 else 0
    # Require >=3 keyword hits and a clear lead over the runner-up.
    if top_score < 3:
        return "generic", scores
    if runner_score > 0 and top_score < 2 * runner_score:
        return "generic", scores
    if top_name not in available_field_guides():
        return "generic", scores
    return top_name, scores


def detect_field_scores(corpus: CorpusPaths) -> list[tuple[str, int]]:
    """Return the raw per-field keyword overlap scores for diagnostics."""
    _, scores = _score_corpus(corpus)
    return scores
