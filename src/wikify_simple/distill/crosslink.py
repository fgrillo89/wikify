"""Populate `links` on each WikiPage by alias matching + evidence overlap.

No LLM. Two pages are linked if (a) one mentions the other's title or alias
in its body, or (b) they share at least one source document via evidence.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import WikiPage


def crosslink(pages: list[WikiPage]) -> list[WikiPage]:
    by_id = {p.id: p for p in pages}

    # alias -> page ids
    alias_to_ids: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        alias_to_ids[p.title.lower()].append(p.id)
        for a in p.aliases:
            alias_to_ids[a.lower()].append(p.id)

    # evidence overlap by doc
    doc_to_pages: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        for ev in p.evidence:
            doc_to_pages[ev.doc_id].add(p.id)

    for p in pages:
        links: set[str] = set(p.links)
        body = (p.body_markdown or "").lower()
        for alias, ids in alias_to_ids.items():
            if alias and alias != p.title.lower() and alias in body:
                for tid in ids:
                    if tid != p.id:
                        links.add(tid)
        for ev in p.evidence:
            for tid in doc_to_pages.get(ev.doc_id, set()):
                if tid != p.id:
                    links.add(tid)
        p.links = sorted(links)
    return pages
