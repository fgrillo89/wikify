"""Validated navigation hierarchy for rendered wiki sites."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...api import Bundle
from .page import load_bundle
from .page_naming import url_slug

SCHEMA_VERSION = 1
MAX_DEPTH = 4
MAX_RELATED = 5
MAX_SHARED_DOCS = 3
MAX_OVERLAP_TERMS = 5

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]{2,}")
_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "are",
    "because",
    "between",
    "can",
    "from",
    "has",
    "have",
    "into",
    "its",
    "may",
    "more",
    "not",
    "over",
    "page",
    "that",
    "the",
    "their",
    "these",
    "this",
    "through",
    "use",
    "used",
    "uses",
    "using",
    "was",
    "were",
    "with",
    "within",
}


class NavigationError(ValueError):
    """Raised when a navigation hierarchy is structurally invalid."""


def navigation_path(bundle: Bundle) -> Path:
    return bundle.derived_dir / "navigation.json"


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _excerpt(text: str, limit: int = 240) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("|"):
            return " ".join(s.split())[:limit]
    return ""


def _tokens(*values: str) -> set[str]:
    out: set[str] = set()
    for value in values:
        for token in _TOKEN_RE.findall(value.lower()):
            if token not in _STOPWORDS:
                out.add(token)
    return out


def _page_fingerprints(bundle: Bundle) -> dict[str, dict[str, Any]]:
    page_bundle = load_bundle(bundle.wiki_dir)
    fingerprints: dict[str, dict[str, Any]] = {}
    for page in page_bundle.pages:
        try:
            rel_path = str(page.path.relative_to(bundle.root)).replace("\\", "/")
        except ValueError:
            rel_path = str(page.path).replace("\\", "/")
        stat = page.path.stat()
        fingerprints[page.id] = {
            "path": rel_path,
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    return fingerprints


def _group_page_ids(groups: list[Any]) -> set[str]:
    ids: set[str] = set()

    def visit(group: Any) -> None:
        if not isinstance(group, dict):
            return
        for page_id in group.get("page_ids") or []:
            ids.add(str(page_id))
        for child in group.get("children") or []:
            visit(child)

    for group in groups:
        visit(group)
    return ids


def _compact_existing_navigation(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    return {
        key: data[key]
        for key in ("schema_version", "generated_at", "strategy", "groups", "ungrouped_page_ids")
        if key in data
    }


def _freshness_context(
    bundle: Bundle,
    existing_navigation: dict[str, Any] | None,
    current_fingerprints: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not existing_navigation:
        return {
            "has_navigation": False,
            "is_fresh": False,
            "new_page_ids": sorted(current_fingerprints),
            "changed_page_ids": [],
            "removed_page_ids": [],
        }

    stored = existing_navigation.get("page_fingerprints")
    if isinstance(stored, dict):
        stored_ids = set(stored)
        current_ids = set(current_fingerprints)
        changed = [
            page_id
            for page_id in sorted(current_ids & stored_ids)
            if stored.get(page_id) != current_fingerprints.get(page_id)
        ]
        return {
            "has_navigation": True,
            "is_fresh": not changed and current_ids == stored_ids,
            "new_page_ids": sorted(current_ids - stored_ids),
            "changed_page_ids": changed,
            "removed_page_ids": sorted(stored_ids - current_ids),
        }

    known_ids = _group_page_ids(existing_navigation.get("groups") or [])
    known_ids.update(str(pid) for pid in existing_navigation.get("ungrouped_page_ids") or [])
    nav_path = navigation_path(bundle)
    nav_mtime_ns = nav_path.stat().st_mtime_ns if nav_path.is_file() else 0
    changed = [
        page_id
        for page_id, fp in sorted(current_fingerprints.items())
        if page_id in known_ids and int(fp.get("mtime_ns") or 0) > nav_mtime_ns
    ]
    new = sorted(set(current_fingerprints) - known_ids)
    return {
        "has_navigation": True,
        "is_fresh": not changed and not new,
        "new_page_ids": new,
        "changed_page_ids": changed,
        "removed_page_ids": sorted(known_ids - set(current_fingerprints)),
    }


def _cluster_hints(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {p["id"]: p for p in pages}
    backlinks: dict[str, set[str]] = {p["id"]: set() for p in pages}
    token_sets: dict[str, set[str]] = {}
    doc_sets: dict[str, set[str]] = {}
    for page in pages:
        page_id = page["id"]
        token_sets[page_id] = _tokens(page["title"], page.get("excerpt", ""))
        doc_sets[page_id] = set(page.get("evidence_doc_ids") or [])
        for link in page.get("links") or []:
            if link in backlinks:
                backlinks[link].add(page_id)

    hints: list[dict[str, Any]] = []
    for page in pages:
        page_id = page["id"]
        related: list[dict[str, Any]] = []
        outgoing = {link for link in page.get("links") or [] if link in by_id}
        for other_id in sorted(by_id):
            if other_id == page_id:
                continue
            shared_docs = sorted(doc_sets[page_id] & doc_sets[other_id])[:MAX_SHARED_DOCS]
            overlap_terms = sorted(token_sets[page_id] & token_sets[other_id])[
                :MAX_OVERLAP_TERMS
            ]
            linked = other_id in outgoing
            linked_by = other_id in backlinks[page_id]
            score = 0
            if linked:
                score += 6
            if linked_by:
                score += 5
            score += min(len(shared_docs), MAX_SHARED_DOCS) * 3
            score += min(len(overlap_terms), MAX_OVERLAP_TERMS)
            if score <= 0:
                continue
            reasons: dict[str, Any] = {}
            if linked:
                reasons["links_to"] = True
            if linked_by:
                reasons["linked_by"] = True
            if shared_docs:
                reasons["shared_evidence_doc_ids"] = shared_docs
            if overlap_terms:
                reasons["overlap_terms"] = overlap_terms
            related.append(
                {
                    "page_id": other_id,
                    "score": score,
                    "reasons": reasons,
                }
            )
        related.sort(key=lambda r: (-int(r["score"]), str(r["page_id"]).lower()))
        hints.append({"page_id": page_id, "related": related[:MAX_RELATED]})
    return hints


def build_navigation_context(bundle: Bundle) -> dict[str, Any]:
    """Return the page metadata an organizer agent needs."""
    page_bundle = load_bundle(bundle.wiki_dir)
    pages: list[dict[str, Any]] = []
    for page in sorted(page_bundle.pages, key=lambda p: (p.kind, p.title.lower())):
        evidence_doc_ids = sorted({ev.doc_id for ev in page.evidence if ev.doc_id})
        subdir = "articles" if page.kind == "article" else "people"
        pages.append(
            {
                "id": page.id,
                "title": page.title,
                "kind": page.kind,
                "slug": page.path.stem,
                "url": f"{subdir}/{url_slug(page.id)}.html",
                "aliases": list(page.aliases or []),
                "links": list(page.links or []),
                "excerpt": _excerpt(page.body_clean),
                "evidence_count": len(page.evidence or []),
                "source_count": len(evidence_doc_ids),
                "evidence_doc_ids": evidence_doc_ids,
            }
        )
    existing_navigation = read_navigation(bundle.root)
    fingerprints = _page_fingerprints(bundle)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow(),
        "freshness": _freshness_context(bundle, existing_navigation, fingerprints),
        "existing_navigation": _compact_existing_navigation(existing_navigation),
        "cluster_hints": _cluster_hints(pages),
        "pages": pages,
    }


def validate_navigation(bundle: Bundle, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an agent-authored navigation hierarchy."""
    page_bundle = load_bundle(bundle.wiki_dir)
    valid_ids = {p.id for p in page_bundle.pages}
    seen: set[str] = set()

    if not isinstance(payload, dict):
        raise NavigationError("navigation payload must be an object")
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise NavigationError("navigation payload must contain a groups list")

    def normalize_group(group: Any, depth: int) -> dict[str, Any]:
        if depth > MAX_DEPTH:
            raise NavigationError(f"navigation depth exceeds {MAX_DEPTH}")
        if not isinstance(group, dict):
            raise NavigationError("navigation group must be an object")
        group_id = str(group.get("id") or "").strip()
        title = str(group.get("title") or "").strip()
        description = str(group.get("description") or "").strip()
        if not group_id:
            raise NavigationError("navigation group missing id")
        if not title:
            raise NavigationError(f"navigation group {group_id!r} missing title")
        page_ids_raw = group.get("page_ids") or []
        children_raw = group.get("children") or []
        if not isinstance(page_ids_raw, list):
            raise NavigationError(f"navigation group {group_id!r} page_ids must be a list")
        if not isinstance(children_raw, list):
            raise NavigationError(f"navigation group {group_id!r} children must be a list")
        page_ids: list[str] = []
        for raw_page_id in page_ids_raw:
            page_id = str(raw_page_id).strip()
            if page_id not in valid_ids:
                raise NavigationError(
                    f"navigation group {group_id!r} references unknown page {page_id!r}"
                )
            if page_id in seen:
                raise NavigationError(f"page {page_id!r} appears in more than one group")
            seen.add(page_id)
            page_ids.append(page_id)
        children = [normalize_group(child, depth + 1) for child in children_raw]
        if not page_ids and not children:
            raise NavigationError(f"navigation group {group_id!r} is empty")
        return {
            "id": group_id,
            "title": title,
            "description": description,
            "page_ids": page_ids,
            "children": children,
        }

    groups = [normalize_group(group, 1) for group in groups_raw]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": str(payload.get("generated_at") or _utcnow()),
        "strategy": str(payload.get("strategy") or ""),
        "groups": groups,
        "ungrouped_page_ids": sorted(valid_ids - seen),
        "page_fingerprints": _page_fingerprints(bundle),
    }


def _store_navigation_export(bundle: Bundle, normalized: dict[str, Any]) -> dict[str, Any] | None:
    """Persist/export through optional wiki.db category helpers when present."""
    try:
        from . import store
    except ImportError:
        return None

    apply_categories = getattr(store, "apply_navigation_categories", None)
    export_navigation = getattr(store, "export_navigation_json", None)
    open_store = getattr(store, "open_wiki_store", None)
    if (
        not callable(apply_categories)
        or not callable(export_navigation)
        or not callable(open_store)
    ):
        return None

    try:
        from .derived import rebuild_graph

        rebuild_graph(bundle)
    except (OSError, ValueError):
        return None

    con = open_store(bundle.sqlite_path)
    try:
        apply_categories(con, normalized)
        exported = export_navigation(con)
    finally:
        con.close()
    if isinstance(exported, dict):
        exported["strategy"] = normalized.get("strategy", exported.get("strategy", ""))
        return validate_navigation(bundle, exported)
    return None


def write_navigation(bundle: Bundle, payload: dict[str, Any]) -> Path:
    normalized = validate_navigation(bundle, payload)
    exported = _store_navigation_export(bundle, normalized)
    if exported is not None:
        normalized = exported
    path = navigation_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return path


def read_navigation(bundle_root: Path) -> dict[str, Any] | None:
    path = bundle_root / "derived" / "navigation.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def navigation_is_fresh(bundle: Bundle) -> bool:
    path = navigation_path(bundle)
    if not path.is_file():
        return False
    nav_mtime = path.stat().st_mtime
    for sub in (bundle.wiki_articles_dir, bundle.wiki_people_dir):
        if not sub.is_dir():
            continue
        for page in sub.glob("*.md"):
            if page.stat().st_mtime > nav_mtime:
                return False
    return True
