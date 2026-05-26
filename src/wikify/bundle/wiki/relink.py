"""Recompute per-page wikilink crosslinks from shared-evidence overlap.

Implements ``wikify wiki relink``: walks every committed page, computes
pairwise evidence-doc overlap, and updates each page's ``links`` field
to the top-K candidates. Useful after incremental adds, when existing
pages' link sets are frozen at their original write time and have no
edges to pages added later.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from pathlib import Path

from .page import Bundle

_LINKS_LINE_RE = re.compile(r"^links:.*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<fm>.*?)\n---\s*\n", re.DOTALL)


@dataclass
class RelinkResult:
    updated: list[str]
    unchanged: list[str]
    links_per_page: dict[str, list[str]]


def compute_relinks(
    bundle: Bundle, *, max_links: int = 5, min_overlap: int = 2
) -> dict[str, list[str]]:
    """For each page, pick up to ``max_links`` peers by shared-evidence overlap.

    Peers must share at least ``min_overlap`` evidence doc_ids. Ordered
    by overlap descending, then page id ascending. Deterministic — no
    LLM, no randomness; safe to re-run.
    """
    page_docs: dict[str, set[str]] = {
        p.id: {ev.doc_id for ev in p.evidence if ev.doc_id}
        for p in bundle.pages
    }
    out: dict[str, list[str]] = {}
    for p in bundle.pages:
        mine = page_docs[p.id]
        if not mine:
            out[p.id] = []
            continue
        scored: list[tuple[int, str, str]] = []
        for q in bundle.pages:
            if q.id == p.id:
                continue
            overlap = len(mine & page_docs[q.id])
            if overlap >= min_overlap:
                scored.append((-overlap, q.id.lower(), q.id))
        scored.sort()
        out[p.id] = [pid for _, _, pid in scored[:max_links]]
    return out


def apply_relinks(
    bundle: Bundle,
    *,
    max_links: int = 5,
    min_overlap: int = 2,
    dry_run: bool = False,
) -> RelinkResult:
    """Compute and (unless ``dry_run``) write each page's new ``links`` field."""
    new_links = compute_relinks(bundle, max_links=max_links, min_overlap=min_overlap)
    updated: list[str] = []
    unchanged: list[str] = []
    for p in bundle.pages:
        target = new_links.get(p.id, [])
        if sorted(p.links) == sorted(target):
            unchanged.append(p.id)
            continue
        if not dry_run:
            _rewrite_links_field(p.path, target)
        updated.append(p.id)
    return RelinkResult(updated=updated, unchanged=unchanged, links_per_page=new_links)


def _rewrite_links_field(path: Path, links: list[str]) -> None:
    """Surgically replace (or insert) the ``links:`` line in YAML frontmatter."""
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"page {path} has no frontmatter")
    fm = m.group("fm")
    new_line = f"links: {_json.dumps(links)}"
    if _LINKS_LINE_RE.search(fm):
        new_fm = _LINKS_LINE_RE.sub(new_line, fm, count=1)
    else:
        new_fm = fm.rstrip() + "\n" + new_line
    new_raw = f"---\n{new_fm}\n---\n" + raw[m.end():]
    path.write_text(new_raw, encoding="utf-8")
