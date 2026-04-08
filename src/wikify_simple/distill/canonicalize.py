"""Pure-python merge of candidate concepts/people into WikiPage skeletons.

Deterministic. No LLM. Inputs are extracted candidates from the extractor;
outputs are WikiPage skeletons each marked as new / update / merge against
the wiki dir already on disk. Match rule: normalised title equality OR
alias intersection. The pipeline writes the resulting pages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..agents.schema import ExtractedConcept
from ..models import Evidence, WikiPage

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    return _NORM_RE.sub("-", s.lower()).strip("-")


@dataclass
class Candidate:
    concept: ExtractedConcept
    chunk_id: str
    doc_id: str


def canonicalize(
    candidates: Iterable[Candidate],
    existing: list[WikiPage],
) -> list[WikiPage]:
    by_id: dict[str, WikiPage] = {}
    alias_index: dict[str, str] = {}

    for p in existing:
        by_id[p.id] = p
        alias_index[_normalize(p.title)] = p.id
        for a in p.aliases:
            alias_index[_normalize(a)] = p.id

    for cand in candidates:
        norm = _normalize(cand.concept.title)
        if not norm:
            continue
        page_id = alias_index.get(norm)
        marker = f"e{1}"
        if page_id is None:
            page_id = f"{cand.concept.kind}-{norm}"
            page = WikiPage(
                id=page_id,
                kind=cand.concept.kind,
                title=cand.concept.title,
                aliases=list(cand.concept.aliases),
                body_markdown="",
                evidence=[],
            )
            by_id[page_id] = page
            alias_index[norm] = page_id
            for a in cand.concept.aliases:
                alias_index[_normalize(a)] = page_id
        page = by_id[page_id]
        page.evidence.append(
            Evidence(
                marker=f"e{len(page.evidence) + 1}",
                chunk_id=cand.chunk_id,
                doc_id=cand.doc_id,
                quote=cand.concept.quote,
            )
        )

    return list(by_id.values())
