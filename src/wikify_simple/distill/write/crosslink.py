"""Populate `links` on each WikiPage by alias matching + evidence overlap.

No LLM. Two pages are linked if (a) one mentions the other's title or alias
in its body, or (b) they share at least one source document via evidence.
"""

import re
from collections import defaultdict

from wikify_simple.models import WikiPage

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def crosslink(pages: list[WikiPage]) -> list[WikiPage]:
    # alias -> page ids
    alias_to_ids: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        alias_to_ids[p.title.lower()].append(p.id)
        for a in p.aliases:
            alias_to_ids[a.lower()].append(p.id)
    alias_by_first_token: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for alias, ids in alias_to_ids.items():
        toks = _TOKEN_RE.findall(alias)
        if not toks:
            continue
        alias_by_first_token[toks[0]].append((alias, ids))

    # evidence overlap by doc
    doc_to_pages: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        for ev in p.evidence:
            doc_to_pages[ev.doc_id].add(p.id)

    for p in pages:
        links: set[str] = set(p.links)
        body = (p.body_markdown or "").lower()
        body_tokens = set(_TOKEN_RE.findall(body))
        scanned: set[str] = set()
        for tok in body_tokens:
            for alias, ids in alias_by_first_token.get(tok, []):
                if alias in scanned:
                    continue
                scanned.add(alias)
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
