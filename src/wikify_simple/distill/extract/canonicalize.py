"""Pure-python merge of candidate concepts/people into WikiPage skeletons.

Deterministic. No LLM. Inputs are extracted candidates from the extractor;
outputs are WikiPage skeletons each marked as new / update / merge against
the wiki dir already on disk. Match rule: normalised title equality OR
alias intersection. The pipeline writes the resulting pages.

Person candidates (``kind="person"``) are merged into existing author pages
when an alias match exists. When no match is found, a new person page is
created with ``provenance.source = "extraction"``. This allows the model
to enrich person pages when enough evidence accumulates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..agents.schema import ExtractedConcept
from ..models import Evidence, WikiPage
from ..store.page_naming import page_id_from_title

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
    # per-page list of (confidence_label, score) aligned with the
    # evidence list; written into page.provenance below.
    conf_by_page: dict[str, list[tuple[str, float]]] = {}

    for p in existing:
        by_id[p.id] = p
        alias_index[_normalize(p.title)] = p.id
        for a in p.aliases:
            alias_index[_normalize(a)] = p.id
        existing_scores = (p.provenance or {}).get("confidence_scores") or []
        conf_by_page[p.id] = [
            (str(s.get("label", "extracted")), float(s.get("score", 1.0)))
            for s in existing_scores
            if isinstance(s, dict)
        ]

    for cand in candidates:
        norm = _normalize(cand.concept.title)
        if not norm:
            continue

        # Person candidates: merge into existing author page by alias,
        # or create a new extracted-person page if no match.
        if cand.concept.kind == "person":
            page_id = alias_index.get(norm)
            if page_id is not None:
                # Merge evidence into existing page (author or prior person).
                page = by_id[page_id]
                page.evidence.append(
                    Evidence(
                        marker=f"e{len(page.evidence) + 1}",
                        chunk_id=cand.chunk_id,
                        doc_id=cand.doc_id,
                        quote=cand.concept.quote,
                    )
                )
                conf_by_page.setdefault(page_id, []).append(
                    (cand.concept.confidence, float(cand.concept.score))
                )
            else:
                # New person page from extraction (not a corpus author).
                page_id = page_id_from_title(cand.concept.title)
                if not page_id:
                    continue
                page = WikiPage(
                    id=page_id,
                    kind="person",
                    title=cand.concept.title,
                    aliases=list(cand.concept.aliases),
                    body_markdown="",
                    evidence=[
                        Evidence(
                            marker="e1",
                            chunk_id=cand.chunk_id,
                            doc_id=cand.doc_id,
                            quote=cand.concept.quote,
                        )
                    ],
                    provenance={"source": "extraction"},
                )
                by_id[page_id] = page
                alias_index[norm] = page_id
                conf_by_page[page_id] = [(cand.concept.confidence, float(cand.concept.score))]
                for a in cand.concept.aliases:
                    alias_index[_normalize(a)] = page_id
            continue

        # Concept candidates: same logic as before.
        page_id = alias_index.get(norm)
        if page_id is None:
            page_id = page_id_from_title(cand.concept.title)
            if not page_id:
                continue
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
            conf_by_page[page_id] = []
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
        conf_by_page.setdefault(page_id, []).append(
            (cand.concept.confidence, float(cand.concept.score))
        )

    # Stamp aggregated confidence into each page's provenance dict.
    for pid, page in by_id.items():
        scores = conf_by_page.get(pid, [])
        if not scores:
            continue
        labels = [lab for lab, _ in scores]
        nums = [s for _, s in scores]
        prov = dict(page.provenance or {})
        prov["confidence_scores"] = [{"label": lab, "score": s} for lab, s in scores]
        prov["confidence_min"] = min(nums)
        prov["confidence_mean"] = sum(nums) / len(nums)
        prov["confidence_count_by_label"] = {lab: labels.count(lab) for lab in sorted(set(labels))}
        page.provenance = prov

    return list(by_id.values())
