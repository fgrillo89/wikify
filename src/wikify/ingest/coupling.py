"""Bibliographic coupling between documents.

Operates on ``Document.citations`` in memory. Two docs
are coupled when they share references. Coupling strength is the
count of shared references, matched by a normalised fingerprint.
"""

import re
from collections import defaultdict

from ..models import Document


def _fingerprint(raw_text: str) -> str:
    """Normalise a citation string to a stable fingerprint.

    Lowercase, strip punctuation, collapse whitespace, first 80 chars.
    """
    text = (raw_text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def _citation_fingerprints(cit: dict) -> list[str]:
    """Extract candidate fingerprints from a parsed citation dict.

    Prefers ``raw_text``; falls back to a synthetic "author year title"
    string built from structured fields when raw_text is missing.
    """
    out: list[str] = []
    raw = cit.get("raw_text")
    if isinstance(raw, str) and raw.strip():
        fp = _fingerprint(raw)
        if fp:
            out.append(fp)
        return out
    # Fallback: build synthetic fingerprint from available fields
    authors = cit.get("authors") or cit.get("author_last_names") or []
    first_author = str(authors[0]) if authors else ""
    year = str(cit.get("year") or "")
    title = str(cit.get("title") or "")
    synth = f"{first_author} {year} {title}".strip()
    if synth:
        fp = _fingerprint(synth)
        if fp:
            out.append(fp)
    return out


def compute_coupling(
    docs: list[Document],
    *,
    min_strength: int = 3,
    top_k: int = 5,
) -> dict[str, list[str]]:
    """Compute bibliographic coupling for ``docs``.

    Returns ``{doc_id: [coupled_doc_id, ...]}`` sorted by coupling
    strength descending (with ties broken by doc_id ascending), capped
    at ``top_k`` per doc. Coupling is symmetric.
    """
    if not docs:
        return {}

    fp_to_docs: dict[str, set[str]] = defaultdict(set)
    for d in docs:
        for cit in d.citations or []:
            for fp in _citation_fingerprints(cit):
                fp_to_docs[fp].add(d.id)

    pair_strength: dict[tuple[str, str], int] = defaultdict(int)
    for citing in fp_to_docs.values():
        if len(citing) < 2:
            continue
        ids = sorted(citing)
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pair_strength[(a, b)] += 1

    neighbours: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (a, b), strength in pair_strength.items():
        if strength < min_strength:
            continue
        neighbours[a].append((strength, b))
        neighbours[b].append((strength, a))

    result: dict[str, list[str]] = {}
    for d in docs:
        nbrs = neighbours.get(d.id, [])
        nbrs.sort(key=lambda x: (-x[0], x[1]))
        result[d.id] = [other for _, other in nbrs[:top_k]]
    return result
