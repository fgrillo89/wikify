"""Canonical visible/operational wiki layout helpers.

The visible wiki stays intentionally small:
    - index.md
    - log.md
    - articles/
    - sources/
    - _meta/

The operational layer lives under ``_meta`` and in structured storage.
"""

from __future__ import annotations

from pathlib import Path

VISIBLE_PAGE_TYPES = frozenset(
    {
        "entity",
        "concept",
        "overview",
        "comparison",
        "query",
        "source-note",
    }
)

LEGACY_VISIBLE_DIRS = frozenset(
    {"concepts", "themes", "syntheses", "queries", "people", "entities", "gaps"}
)

LEGACY_CATEGORY_TO_PAGE_TYPE: dict[str, str] = {
    "concepts": "concept",
    "concept": "concept",
    "themes": "overview",
    "theme": "overview",
    "syntheses": "overview",
    "synthesis": "overview",
    "queries": "query",
    "query": "query",
    "comparisons": "comparison",
    "comparison": "comparison",
    "people": "entity",
    "entities": "entity",
    "entity": "entity",
    "sources": "source-note",
    "source": "source-note",
    "source-note": "source-note",
}


def articles_dir(wiki_dir: Path) -> Path:
    return wiki_dir / "articles"


def sources_dir(wiki_dir: Path) -> Path:
    return wiki_dir / "sources"


def meta_dir(wiki_dir: Path) -> Path:
    return wiki_dir / "_meta"


def runs_dir(wiki_dir: Path) -> Path:
    return meta_dir(wiki_dir) / "runs"


def metrics_dir(wiki_dir: Path) -> Path:
    return meta_dir(wiki_dir) / "metrics"


def index_path(wiki_dir: Path) -> Path:
    return wiki_dir / "index.md"


def log_path(wiki_dir: Path) -> Path:
    return wiki_dir / "log.md"


def ensure_layout(wiki_dir: Path) -> None:
    """Create the canonical visible and operational directories."""
    for path in (
        articles_dir(wiki_dir),
        sources_dir(wiki_dir),
        runs_dir(wiki_dir),
        metrics_dir(wiki_dir),
    ):
        path.mkdir(parents=True, exist_ok=True)


def normalize_page_type(page_type: str | None, fallback_category: str = "") -> str:
    """Normalize a page role to the canonical visible page-type vocabulary."""
    requested = (page_type or "").strip().lower()
    if requested in VISIBLE_PAGE_TYPES:
        return requested
    fallback = (fallback_category or "").strip().lower()
    return LEGACY_CATEGORY_TO_PAGE_TYPE.get(fallback, "concept")


def visible_page_path(
    wiki_dir: Path,
    *,
    slug: str,
    page_type: str = "concept",
) -> Path:
    """Return the canonical markdown path for a visible wiki page."""
    normalized = normalize_page_type(page_type)
    if normalized == "source-note":
        return sources_dir(wiki_dir) / f"{slug}.md"
    return articles_dir(wiki_dir) / f"{slug}.md"


def article_path_for_category(wiki_dir: Path, category: str, slug: str) -> Path:
    """Backward-compatible path resolver for legacy callers."""
    return visible_page_path(
        wiki_dir,
        slug=slug,
        page_type=normalize_page_type(None, fallback_category=category),
    )


def is_visible_page_file(wiki_dir: Path, path: Path) -> bool:
    """Return True when ``path`` is a human-visible wiki page."""
    try:
        rel = path.relative_to(wiki_dir)
    except ValueError:
        return False

    if path.suffix.lower() != ".md":
        return False
    if path.name.startswith("_"):
        return False
    if rel.parts and rel.parts[0] == "_meta":
        return False
    if rel == Path("index.md") or rel == Path("log.md"):
        return False
    if rel.parts and rel.parts[0] in {"articles", "sources"}:
        return True
    if rel.parts and rel.parts[0] in LEGACY_VISIBLE_DIRS:
        return True
    return False


def iter_visible_page_files(wiki_dir: Path) -> list[Path]:
    """Return all visible wiki pages in a stable order."""
    return sorted(
        [path for path in wiki_dir.rglob("*.md") if is_visible_page_file(wiki_dir, path)]
    )
