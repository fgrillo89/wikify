"""B2 baseline: post-hoc citation.

Takes B1 output (plain prose pages) and adds document-level citations by
embedding each paragraph and finding the most similar source document.
Appends [N] Author et al., Year citations. No chunk-level tracing, no
verbatim quotes.

This matches the ALCE/HAGRID post-hoc attribution paradigm.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..models import Evidence, WikiPage

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph

_PARA_SPLIT = re.compile(r"\n\n+")


def add_post_hoc_citations(
    pages: list[WikiPage],
    kg: KnowledgeGraph,
) -> list[WikiPage]:
    """Add document-level citations to B1 pages.

    For each paragraph, find the most similar source document via KG
    search and append a [N] citation. Returns new WikiPage objects with
    evidence populated at doc level.
    """
    result: list[WikiPage] = []

    for page in pages:
        if not page.body_markdown:
            result.append(page)
            continue

        paragraphs = _PARA_SPLIT.split(page.body_markdown.strip())
        cited_docs: dict[str, int] = {}  # doc_id -> citation number
        evidence: list[Evidence] = []
        new_paragraphs: list[str] = []
        cite_counter = 0

        for para in paragraphs:
            if not para.strip() or para.startswith("##"):
                new_paragraphs.append(para)
                continue

            # Find most similar source for this paragraph
            hits = kg.sources(kind="corpus").search(para[:500], top_k=1)
            if not hits:
                new_paragraphs.append(para)
                continue

            doc_id = hits[0]["id"]
            if doc_id not in cited_docs:
                cite_counter += 1
                cited_docs[doc_id] = cite_counter
                year = hits[0].get("year", "")
                authors = hits[0].get("authors", [])
                author_str = authors[0].split(",")[0] if authors else "Unknown"
                if len(authors) > 1:
                    author_str += " et al."

                evidence.append(Evidence(
                    marker=f"e{cite_counter}",
                    chunk_id="",
                    doc_id=doc_id,
                    quote="",
                    locator=f"{author_str}, {year}" if year else author_str,
                ))

            n = cited_docs[doc_id]
            # Append citation to end of paragraph
            new_paragraphs.append(f"{para} [^e{n}]")

        # Build references section
        refs = "\n\n## References\n\n"
        for ev in evidence:
            refs += f"[^{ev.marker}]: {ev.doc_id} ({ev.locator})\n"

        new_body = "\n\n".join(new_paragraphs) + refs

        result.append(WikiPage(
            id=page.id,
            kind=page.kind,
            title=page.title,
            aliases=page.aliases,
            body_markdown=new_body,
            evidence=evidence,
            links=page.links,
            provenance={**page.provenance, "condition": "B2"},
        ))

    return result
