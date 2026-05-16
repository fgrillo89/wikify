"""Validated navigation hierarchy for rendered wiki sites."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...api import Bundle
from .page import load_bundle
from .page_naming import url_slug

SCHEMA_VERSION = 1
MAX_DEPTH = 4


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
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow(),
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
    }


def write_navigation(bundle: Bundle, payload: dict[str, Any]) -> Path:
    normalized = validate_navigation(bundle, payload)
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
