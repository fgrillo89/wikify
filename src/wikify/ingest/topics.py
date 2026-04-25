"""Topic vocabulary extraction (used by GT-C in eval).

Keyword-first topic extractor. The algorithm:

1. Pass 1: scan each doc for a declared "Keywords:" / "Index Terms:"
   section. Collect all declared keywords as the corpus vocabulary,
   normalising plurals and keeping the longest canonical form.
2. Pass 2: for each doc without declared keywords, match the corpus
   vocabulary against the doc's text (whole-phrase, word-boundary).
3. Pass 3: deduplicate across the global vocabulary by absorbing
   substrings and merging plural/stem variants.

The public ``extract_topics`` keeps its ``(docs_chunks, declared_per_doc)``
signature; the algorithm is invoked internally by building a
per-doc text blob from chunk text.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Chunk

# --- public surface -----------------------------------------------------

_PHRASE_RE = re.compile(r"\b([a-z][a-z0-9-]+(?:\s+[a-z][a-z0-9-]+){0,3})\b")


@dataclass
class TopicVocabulary:
    topics: list[str] = field(default_factory=list)
    declared: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"topics": self.topics, "declared": self.declared}


def extract_topics(
    docs_chunks: Iterable[tuple[str, list[Chunk]]],
    declared_per_doc: dict[str, list[str]] | None = None,
) -> TopicVocabulary:
    declared_per_doc = dict(declared_per_doc or {})
    docs_list = list(docs_chunks)

    # Build a per-doc text blob from chunk content.
    doc_texts: dict[str, str] = {}
    for doc_id, chunks in docs_list:
        doc_texts[doc_id] = "\n".join(c.text for c in chunks)

    # Pass 1: vocabulary from all declared keywords (header parse + explicit).
    all_declared: list[str] = []
    paper_declared: dict[str, list[str]] = {}
    for doc_id, text in doc_texts.items():
        kws = list(declared_per_doc.get(doc_id, []))
        kws.extend(_extract_declared_keywords(text))
        sanitized = [k for k in (_sanitize_keyword(k) for k in kws) if _is_valid_keyword(k)]
        lower = [k.lower() for k in sanitized]
        paper_declared[doc_id] = lower
        all_declared.extend(lower)

    seen_norms: dict[str, str] = {}
    for kw in all_declared:
        norm = _normalize_topic(kw)
        if norm not in seen_norms or len(kw) > len(seen_norms[norm]):
            seen_norms[norm] = kw
    corpus_vocab = list(seen_norms.values())
    canonical_map = dict(seen_norms)
    compiled = _compile_vocabulary_patterns(corpus_vocab)

    # Pass 2: resolve each doc's topics (declared or matched).
    per_doc: dict[str, list[str]] = {}
    for doc_id, text in doc_texts.items():
        declared = paper_declared.get(doc_id, [])
        if declared:
            topics = declared
        else:
            topics = _match_corpus_vocabulary(text, corpus_vocab, compiled_patterns=compiled)
        topics = [canonical_map.get(_normalize_topic(t), t) for t in topics]
        per_doc[doc_id] = _deduplicate_topics([_to_display(t) for t in topics])[:12]

    # Flatten into the TopicVocabulary shape.
    all_topics: dict[str, str] = {}
    for v in per_doc.values():
        for t in v:
            norm = _normalize_topic(t)
            if norm not in all_topics or len(t) > len(all_topics[norm]):
                all_topics[norm] = t
    topics_sorted = sorted(all_topics.values(), key=lambda s: s.lower())

    declared_set = {_to_display(k) for ks in paper_declared.values() for k in ks}
    declared_final = sorted(
        [t for t in topics_sorted if t in declared_set],
        key=lambda s: s.lower(),
    )
    return TopicVocabulary(topics=topics_sorted, declared=declared_final)


def write_topics(path: Path, vocab: TopicVocabulary) -> None:
    from wikify.corpus.chunks import atomic_write_text

    atomic_write_text(path, json.dumps(vocab.to_dict(), indent=2))


# --- internals ------------------------------------------------------------

_KEYWORD_BAD_STARTERS = frozenset(
    {
        "a",
        "above",
        "after",
        "again",
        "against",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "based",
        "before",
        "being",
        "below",
        "between",
        "beyond",
        "both",
        "but",
        "by",
        "can",
        "closely",
        "compared",
        "demonstrating",
        "developing",
        "due",
        "during",
        "each",
        "exhibiting",
        "for",
        "from",
        "furthermore",
        "however",
        "in",
        "including",
        "inspired",
        "into",
        "it",
        "its",
        "notably",
        "of",
        "offering",
        "on",
        "or",
        "originally",
        "our",
        "over",
        "providing",
        "showing",
        "such",
        "than",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "through",
        "to",
        "toward",
        "thus",
        "under",
        "until",
        "using",
        "via",
        "we",
        "where",
        "which",
        "while",
        "with",
        "without",
    }
)
_KEYWORD_SINGLE_STOP = frozenset(
    {
        "abstract",
        "accordingly",
        "additionally",
        "also",
        "although",
        "and",
        "besides",
        "both",
        "consequently",
        "currently",
        "especially",
        "eventually",
        "fig",
        "figure",
        "finally",
        "first",
        "fortunately",
        "further",
        "furthermore",
        "generally",
        "here",
        "hereafter",
        "however",
        "importantly",
        "initially",
        "instead",
        "introduction",
        "journal",
        "keywords",
        "likewise",
        "meanwhile",
        "moreover",
        "nevertheless",
        "next",
        "notably",
        "or",
        "otherwise",
        "particularly",
        "previously",
        "quantitatively",
        "recently",
        "references",
        "respectively",
        "semicond",
        "similarly",
        "subsequently",
        "table",
        "then",
        "therefore",
        "traditionally",
        "thus",
        "vol",
    }
)
_MONTH_NAMES = frozenset(
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
_FRAGMENT_MARKERS = (
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
    " from ",
)
_METADATA_WORDS = frozenset(
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
        "grant",
        "e-mail",
        "email",
        "accepted",
        "received",
        "revised",
        "published",
        "publication",
        "manuscript",
        "corresponding",
    }
)


def _is_valid_keyword(kw: str) -> bool:
    if len(kw) < 3 or len(kw) > 50:
        return False
    if re.search(r"<\s*br\s*/?\s*>|<br>", kw, re.IGNORECASE):
        return False
    if re.search(r"\w-\s*[A-Z]", kw) and len(kw.split()) <= 2:
        return False
    if "|" in kw:
        return False
    if re.search(r"(?i)rights?\s+reserved|copyright|permission", kw):
        return False
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
    if len(words) == 2 and words[0].lower() in _MONTH_NAMES and words[1].isdigit():
        return False
    if kw.count("(") != kw.count(")"):
        return False
    kw_padded = f" {kw.lower()} "
    if any(marker in kw_padded for marker in _FRAGMENT_MARKERS):
        return False
    if set(kw.lower().split()) & _METADATA_WORDS:
        return False
    # Reject digit-suffixed fragments like "technology.6" or "device.3"
    if re.search(r"\.\d", kw):
        return False
    # Reject truncated-word starts like "ibility with cmos technology"
    # (PDF column-break artifact: "compatibility" got cut off mid-word).
    # Heuristic: a first word that begins with a vowel-vowel-consonant or
    # has no recognizable English prefix and is < 4 chars in suffix form.
    first = words[0].lower()
    if re.match(r"^(?:ibility|ility|tion|sion|ment|ness|ance|ence|ous|ive)$", first):
        return False
    # Reject email addresses and URLs
    if "@" in kw or "://" in kw:
        return False
    # Reject strings with 4+ digit sequences (postal codes, page numbers,
    # grant numbers, years used as identifiers)
    if re.search(r"\b\d{4,}\b", kw):
        return False
    # Reject author-initial patterns: "C J Wan", "D W Zhang", "A. B. Foo"
    if re.match(r"^[A-Z]\.?\s+[A-Z]\.?\s+\w", kw):
        return False
    # Reject "Foo Et Al" (citation fragments)
    if re.search(r"\bet\s+al\b", kw, re.IGNORECASE):
        return False
    # Reject institution/location names
    if re.search(
        r"(?i)\b(?:university|institut|academy|college)\b", kw,
    ):
        return False
    # Reject "Figure N" / "Table N" references
    if re.match(r"(?i)^(?:figure|fig|table)\s*\d", kw):
        return False
    # Reject label:number fragments like "B: 6 S", "D: 12 S"
    if re.match(r"^[A-Z]:\s*\d", kw):
        return False
    # Multi-word structural checks (sentence fragments):
    if len(words) > 2:
        # Starts with a preposition/conjunction -> sentence fragment
        if first in _KEYWORD_BAD_STARTERS:
            return False
        # Starts with a gerund, adverb, or past participle -> fragment
        if re.match(r"(?i)^\w+(?:ing|ly|ed)$", words[0]):
            return False
        # Section-header prefix followed by body text
        _section_re = (
            r"(?i)^(?:introduction|results|discussion"
            r"|conclusion|method|date)\b"
        )
        if re.match(_section_re, kw):
            return False
    return True


def _sanitize_keyword(kw: str) -> str:
    kw = re.sub(r"<\s*br\s*/?\s*>", " ", kw, flags=re.IGNORECASE)
    kw = re.sub(r"(\w)-\s+([a-z])", lambda m: m.group(1) + m.group(2), kw)
    kw = re.sub(r"\s+", " ", kw).strip()
    kw = kw.rstrip("|. ")
    return kw


def _extract_declared_keywords(text: str) -> list[str]:
    """Extract keywords from a "Keywords:" or "Index Terms:" section.

    Structural approach: find the header, then parse the comma/semicolon-
    delimited list that follows. Stop when we hit evidence that the keyword
    section has ended (a new section header, a sentence-length fragment,
    a paragraph break, or too many consecutive rejects).
    """
    pattern = re.compile(
        r"(?:keywords?|index\s+terms|key\s+words)\s*[:\-\u2014.]+\s*"
        r"(.+?)(?:\n\s*\n|\n#+|\n[A-Z0-9]+\.\s|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text[:10000])
    if not match:
        return []
    raw = match.group(1).strip()
    # Limit raw capture: real keyword sections are short (usually < 500 chars).
    # If we captured more, the regex overshot into body text.
    raw = raw[:600]
    raw = re.sub(r"[*_`]+", "", raw)
    raw = re.sub(r"\[+\]?", "", raw)
    raw = re.sub(r"\]+", "", raw)
    parts = re.split(r"[,;]|\.\s+|\n\s*[-\u2022]\s*", raw)

    keywords: list[str] = []
    consecutive_rejects = 0
    for part in parts:
        kw = part.strip().rstrip(".")
        kw = re.sub(r"^\d+[.)]\s*", "", kw)
        kw = _sanitize_keyword(kw)
        if not kw:
            continue
        # Structural signal: if a "keyword" is longer than 6 words or
        # 60 chars, it's a sentence fragment -- we've left the keyword
        # section. Stop immediately.
        if len(kw.split()) > 6 or len(kw) > 60:
            break
        if _is_valid_keyword(kw):
            keywords.append(kw.lower())
            consecutive_rejects = 0
        else:
            consecutive_rejects += 1
            # 3 consecutive rejects means we've left the keyword section
            if consecutive_rejects >= 3:
                break
        # Real keyword sections rarely exceed 15 entries
        if len(keywords) >= 15:
            break
    return keywords


def _normalize_topic(topic: str) -> str:
    t = topic.strip().lower()
    if t.endswith("ies") and len(t) > 5:
        t = t[:-3] + "y"
    elif t.endswith("ses") or t.endswith("xes") or t.endswith("zes"):
        t = t[:-2]
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 4:
        t = t[:-1]
    return t


def _word_stem(word: str) -> str:
    return word.lower()[:5]


def _deduplicate_topics(topics: list[str]) -> list[str]:
    norm_to_display: dict[str, str] = {}
    for t in topics:
        n = _normalize_topic(t)
        if n not in norm_to_display:
            norm_to_display[n] = t
    surviving = list(norm_to_display.values())
    absorbed: set[str] = set()
    for i, short in enumerate(surviving):
        short_lower = short.lower()
        short_words = short_lower.split()
        for j, long in enumerate(surviving):
            if i == j:
                continue
            long_lower = long.lower()
            if short_lower != long_lower and short_lower in long_lower:
                absorbed.add(short)
                break
            if len(short_words) == 1 and len(long_lower.split()) > 1:
                stem = _word_stem(short_words[0])
                if len(stem) >= 5 and any(_word_stem(w) == stem for w in long_lower.split()):
                    absorbed.add(short)
                    break
    return [t for t in surviving if t not in absorbed]


def _compile_vocabulary_patterns(vocab: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    pairs = []
    for kw in sorted(vocab, key=len, reverse=True):
        pairs.append((kw, re.compile(r"\b" + re.escape(kw.lower()) + r"\b")))
    return pairs


def _match_corpus_vocabulary(
    text: str,
    vocab: list[str],
    max_matches: int = 8,
    compiled_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[str]:
    patterns = compiled_patterns or _compile_vocabulary_patterns(vocab)
    text_lower = text.lower()
    out: list[str] = []
    for kw, pat in patterns:
        if pat.search(text_lower):
            out.append(kw)
            if len(out) >= max_matches:
                break
    return out


def _to_display(kw: str) -> str:
    kw = re.sub(r"[\x00-\x1f\x7f]+", " ", kw).strip()
    kw = re.sub(r"\s+", " ", kw)
    return kw.title() if len(kw) > 4 else kw.upper()
