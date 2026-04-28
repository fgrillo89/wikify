"""Read-only wiki query helpers — the surface ``cli/wiki.py`` calls.

Walks the on-disk wiki tree (``wiki/articles/`` + ``wiki/people/``).
Heavy graph + page-vector queries (``wiki/graph.py``) stay accessible
through the underlying fluent KG; this module wires up the file-walk
queries the CLI uses by default.
"""

from __future__ import annotations

from pathlib import Path

from ...api import Bundle
from .graph import load_wiki_graph


def page_path(bundle: Bundle, *, slug: str, kind: str) -> Path:
    sub = bundle.wiki_articles_dir if kind == "article" else bundle.wiki_people_dir
    return sub / f"{slug}.md"


def list_articles(bundle: Bundle) -> list[str]:
    if not bundle.wiki_articles_dir.is_dir():
        return []
    return sorted(p.stem for p in bundle.wiki_articles_dir.glob("*.md"))


def list_people(bundle: Bundle) -> list[str]:
    if not bundle.wiki_people_dir.is_dir():
        return []
    return sorted(p.stem for p in bundle.wiki_people_dir.glob("*.md"))


def list_files(bundle: Bundle) -> list[str]:
    if not bundle.wiki_dir.is_dir():
        return []
    out: list[str] = []
    for p in sorted(bundle.wiki_dir.rglob("*")):
        if p.is_file():
            out.append(str(p.relative_to(bundle.root)).replace("\\", "/"))
    return out


def find_text(bundle: Bundle, needle: str, *, top_k: int = 50) -> list[dict]:
    """Literal substring grep over committed page bodies."""
    out: list[dict] = []
    needle_lower = needle.lower()
    for kind, sub in (("article", bundle.wiki_articles_dir), ("person", bundle.wiki_people_dir)):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            if needle_lower in text.lower():
                snippet = ""
                idx = text.lower().find(needle_lower)
                if idx >= 0:
                    snippet = text[max(0, idx - 40) : idx + 120].replace("\n", " ")
                out.append(
                    {
                        "slug": p.stem,
                        "kind": kind,
                        "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
                        "snippet": snippet,
                    }
                )
                if len(out) >= top_k:
                    return out
    return out


class AmbiguousSlugError(LookupError):
    """The given short slug matches more than one committed page."""

    def __init__(self, short: str, matches: list[str]) -> None:
        super().__init__(
            f"slug {short!r} is ambiguous; matches: {', '.join(matches[:5])}"
            + (f" (+{len(matches) - 5} more)" if len(matches) > 5 else "")
        )
        self.short = short
        self.matches = matches


def resolve_slug(bundle: Bundle, short: str) -> tuple[str, str] | None:
    """Resolve a short slug to ``(slug, kind)`` against committed pages.

    Tier 1: exact match in articles/ then people/.
    Tier 2: case-insensitive prefix match if unique.
    Returns ``None`` if no candidate matches; raises
    ``AmbiguousSlugError`` on multiple prefix matches.
    """
    for kind in ("article", "person"):
        p = page_path(bundle, slug=short, kind=kind)
        if p.is_file():
            return (short, kind)
    short_l = short.lower()
    matches: list[tuple[str, str]] = []
    for kind, sub in (("article", bundle.wiki_articles_dir), ("person", bundle.wiki_people_dir)):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            if p.stem.lower().startswith(short_l):
                matches.append((p.stem, kind))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousSlugError(short, [s for s, _ in matches])
    return None


_WIKI_RELATIONS = {"links", "linked-by", "co-evidence", "evidence"}


def traverse_page(
    bundle: Bundle,
    *,
    slug: str,
    relation: str,
    rank: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Traverse one hop from a wiki page slug.

    Page-typed relations (``links``, ``linked-by``, ``co-evidence``)
    return ``{slug, kind, title, n_evidence, n_links}`` rows. The
    ``evidence`` relation returns ``{chunk_id, doc_id, quote, page_id}``
    rows so callers can pipe ``chunk_id`` into corpus traversals.
    """
    if relation not in _WIKI_RELATIONS:
        raise ValueError(
            f"unknown wiki relation {relation!r}; expected "
            f"{' | '.join(sorted(_WIKI_RELATIONS))}"
        )
    graph_path = bundle.derived_graph_path
    if not graph_path.is_file():
        return []
    wkg = load_wiki_graph(graph_path)
    backend = wkg._backend
    if not backend.has_node(slug):
        return []
    qb = wkg.page(slug)
    if relation == "links":
        result = qb.links()
    elif relation == "linked-by":
        result = qb.linked_by()
    elif relation == "co-evidence":
        result = qb.co_evidence()
    else:  # evidence
        result = qb.evidence()
    rows: list[dict] = []
    for nid in result.ids():
        if not backend.has_node(nid):
            continue
        attrs = backend.G.nodes[nid]
        ntype = attrs.get("type", "")
        if ntype == "page":
            rows.append({
                "id": nid,
                "type": "page",
                "slug": nid,
                "kind": attrs.get("kind", ""),
                "title": attrs.get("title", ""),
                "n_links": int(attrs.get("n_links", 0) or 0),
                "n_evidence": int(attrs.get("n_evidence", 0) or 0),
            })
        elif ntype == "evidence":
            rows.append({
                "id": nid,
                "type": "evidence",
                "page_id": attrs.get("page_id", ""),
                "chunk_id": attrs.get("chunk_id", ""),
                "doc_id": attrs.get("doc_id", ""),
                "quote": attrs.get("quote", ""),
            })
    if rank in {"n_links", "n_evidence"}:
        rows.sort(key=lambda r: (-int(r.get(rank, 0) or 0), str(r.get("id", ""))))
    elif rank is not None:
        raise ValueError(
            f"unknown wiki rank {rank!r}; expected n_links | n_evidence"
        )
    if top_k is not None:
        rows = rows[:top_k]
    return rows


def show_page(bundle: Bundle, *, handle: str) -> dict | None:
    """Return ``{"path", "kind", "slug", "text"}`` for one wiki page handle.

    The handle may be:

    - a relative file path within the bundle root,
    - an exact slug (article first, then person),
    - a unique case-insensitive prefix of a slug.

    Raises ``AmbiguousSlugError`` when a prefix match is ambiguous.
    """
    # Direct path?
    candidate = bundle.root / handle
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8")
        kind = "article" if "wiki/articles" in handle.replace("\\", "/") else "person"
        return {
            "path": str(candidate.relative_to(bundle.root)).replace("\\", "/"),
            "kind": kind,
            "slug": candidate.stem,
            "text": text,
        }
    resolved = resolve_slug(bundle, handle)
    if resolved is None:
        return None
    slug, kind = resolved
    p = page_path(bundle, slug=slug, kind=kind)
    return {
        "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
        "kind": kind,
        "slug": slug,
        "text": p.read_text(encoding="utf-8"),
    }
