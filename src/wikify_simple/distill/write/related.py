"""Compute related wiki pages for a candidate page.

Pure function: no model calls, no I/O.

Algorithm (O(n) over existing pages, capped at k):
  1. Tokenise the candidate page's title + aliases into a term set T_cand.
  2. For each existing page p:
     a. Tokenise p's title + aliases into T_p.
     b. token_overlap = |T_cand & T_p| / |T_cand | T_p|  (Jaccard on terms)
     c. doc_jaccard   = |docs_cand & docs_p| / |docs_cand | docs_p|
     d. score = 0.5 * token_overlap + 0.5 * doc_jaccard
  3. Take top-k by score (exclude self).
  4. Build the result dict: {id, title, topic_overlap, body_excerpt, see_also,
     evidence_doc_ids}. body_excerpt capped at 500 chars.

Jaccard on the empty set is defined as 0.0.
"""

from __future__ import annotations

import re

from wikify_simple.models import WikiPage

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "is",
        "in",
        "on",
        "for",
        "with",
        "by",
        "at",
        "from",
        "as",
    }
)
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_-]+")
_SEE_ALSO_RE = re.compile(r"^##\s*see\s+also\b", re.IGNORECASE | re.MULTILINE)


def _tokenise(text: str) -> frozenset[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return frozenset(t for t in tokens if t not in _STOP and len(t) >= 3)


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _extract_see_also(body: str) -> list[str]:
    """Return lines from a ## See also section (if present)."""
    m = _SEE_ALSO_RE.search(body)
    if m is None:
        return []
    after = body[m.end():]
    # Collect until next ## heading or end.
    next_h2 = re.search(r"^##\s", after, re.MULTILINE)
    section = after[: next_h2.start()] if next_h2 else after
    links: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") or stripped.startswith("*"):
            stripped = stripped.lstrip("-*").strip()
        if stripped:
            links.append(stripped)
    return links[:10]


def compute_related_pages(
    page: WikiPage,
    all_pages: list[WikiPage],
    k: int = 5,
) -> list[dict]:
    """Return top-k related pages for *page* from *all_pages*.

    Each result is:
    ``{id, title, topic_overlap, body_excerpt, see_also, evidence_doc_ids}``

    The caller is responsible for excluding *page* itself via ``page.id``;
    this function also skips pages without a body or without evidence.
    """
    cand_terms = _tokenise(page.title + " " + " ".join(page.aliases))
    cand_docs: frozenset[str] = frozenset(ev.doc_id for ev in page.evidence)

    scored: list[tuple[float, WikiPage]] = []
    for other in all_pages:
        if other.id == page.id:
            continue
        other_terms = _tokenise(other.title + " " + " ".join(other.aliases))
        other_docs: frozenset[str] = frozenset(ev.doc_id for ev in other.evidence)
        token_j = _jaccard(cand_terms, other_terms)
        doc_j = _jaccard(cand_docs, other_docs)
        score = 0.5 * token_j + 0.5 * doc_j
        if score > 0.0:
            scored.append((score, other))

    scored.sort(key=lambda t: -t[0])
    top = scored[:k]

    results: list[dict] = []
    for score, other in top:
        body = other.body_markdown or ""
        excerpt = body.strip()[:500]
        see_also = _extract_see_also(body)
        results.append(
            {
                "id": other.id,
                "title": other.title,
                "topic_overlap": round(score, 4),
                "body_excerpt": excerpt,
                "see_also": see_also,
                "evidence_doc_ids": [ev.doc_id for ev in other.evidence],
            }
        )
    return results
