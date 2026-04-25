"""Read-only wiki query helpers — the surface ``cli/wiki.py`` calls.

Walks the on-disk v2 wiki tree (``wiki/articles/`` + ``wiki/people/``).
Heavy graph + page-vector queries (``wiki/graph.py``) stay accessible
through the underlying fluent KG; this module wires up the file-walk
queries the CLI uses by default.
"""

from __future__ import annotations

from pathlib import Path

from ...api import Bundle


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


def show_page(bundle: Bundle, *, handle: str) -> dict | None:
    """Return ``{"path", "kind", "slug", "text"}`` for one wiki page handle.

    The handle may be a slug (looked up under articles/ then people/),
    or a relative path within the bundle root.
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
    # Slug lookup.
    for kind in ("article", "person"):
        p = page_path(bundle, slug=handle, kind=kind)
        if p.is_file():
            return {
                "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
                "kind": kind,
                "slug": handle,
                "text": p.read_text(encoding="utf-8"),
            }
    return None
