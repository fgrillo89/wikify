"""``derived/`` projections — rebuildable machine views of the committed wiki.

W6 MVP scope: ``derived/index.json`` listing every committed page.
``derived/graph.json`` and ``derived/vectors.npz`` are heavier
projections owned by ``post_commit.rebuild_wiki_graph``; the v2
``wiki build graph`` / ``wiki build vectors`` verbs invoke that
helper, so this module defers to it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...api import Bundle


def list_committed_pages(bundle: Bundle) -> list[dict]:
    """Walk wiki/articles/ + wiki/people/ and return per-page metadata."""
    out: list[dict] = []
    for kind, sub in (("article", bundle.wiki_articles_dir), ("person", bundle.wiki_people_dir)):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            out.append(
                {
                    "kind": kind,
                    "slug": p.stem,
                    "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
                }
            )
    return out


def rebuild_index(bundle: Bundle) -> Path:
    """Write ``derived/index.json`` with every committed page slug + path."""
    bundle.derived_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "pages": list_committed_pages(bundle),
    }
    bundle.derived_index_path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return bundle.derived_index_path


def read_index(bundle: Bundle) -> dict:
    if not bundle.derived_index_path.exists():
        return {"schema_version": 1, "pages": []}
    return json.loads(bundle.derived_index_path.read_text(encoding="utf-8"))
