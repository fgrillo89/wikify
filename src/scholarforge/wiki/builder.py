"""Wiki article file management: slugify, write, read, and staleness detection."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug.

    Example: "Hafnium Oxide in ALD" -> "hafnium_oxide_in_ald"
    """
    slug = title.lower()
    # Replace spaces and non-word chars with underscores
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug)
    slug = slug.strip("_")
    return slug


def article_path(wiki_dir: Path, category: str, slug: str) -> Path:
    """Return the full path for a wiki article file.

    Args:
        wiki_dir: Root of the wiki directory (e.g. data/wiki/).
        category: Subdirectory name (e.g. "concepts", "syntheses", "gaps").
        slug: Filename slug (without .md extension).

    Returns:
        Full Path object: wiki_dir / category / slug.md
    """
    return wiki_dir / category / f"{slug}.md"


def write_article(
    path: Path,
    title: str,
    content: str,
    sources: list[str],
    topics: list[str],
    status: str = "full",
    model: str = "",
) -> None:
    """Write a wiki article markdown file with YAML frontmatter.

    Creates parent directories if needed. Overwrites any existing file.

    Args:
        path: Absolute path to write the article to.
        title: Human-readable article title.
        content: LLM-authored article body (markdown, without frontmatter).
        sources: List of Paper.id values that informed this article.
        topics: List of topic/concept tags.
        status: "stub", "draft", or "full".
        model: Model identifier used to write the article.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).date().isoformat()
    slug = path.stem

    # Format YAML lists
    sources_yaml = "\n".join(f"  - {s}" for s in sources) if sources else "  []"
    topics_yaml = "[" + ", ".join(topics) + "]" if topics else "[]"

    frontmatter = f"""\
---
title: {title}
wiki_id: {slug}
status: {status}
created: {now}
updated: {now}
sources:
{sources_yaml if sources else "  []"}
topics: {topics_yaml}
model: {model}
---

"""
    path.write_text(frontmatter + content, encoding="utf-8")


def read_article_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a wiki article file.

    Returns an empty dict if the file has no frontmatter or does not exist.
    Uses python-frontmatter if available, else simple regex.
    """
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8", errors="replace")

    try:
        import frontmatter as fm

        post = fm.loads(text)
        return dict(post.metadata)
    except ImportError:
        pass

    # Regex fallback
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}

    meta: dict = {}
    for line in m.group(1).splitlines():
        kv = line.split(":", 1)
        if len(kv) == 2:
            key = kv[0].strip()
            val = kv[1].strip().strip('"').strip("'")
            meta[key] = val

    return meta


def find_stale_articles(
    wiki_articles: list,
    cutoff: datetime,
) -> list:
    """Return WikiArticle rows whose updated_at is older than cutoff.

    Args:
        wiki_articles: List of WikiArticle model instances.
        cutoff: Datetime threshold; articles updated before this are stale.

    Returns:
        Filtered list of WikiArticle instances.
    """
    stale = []
    for article in wiki_articles:
        updated = article.updated_at
        # Ensure both are timezone-aware for comparison
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        if updated < cutoff:
            stale.append(article)
    return stale


def generate_wiki_index(wiki_dir: Path) -> str:
    """Scan wiki directory and generate _index.md content.

    Groups articles by their frontmatter ``category`` field (theme, concept,
    synthesis, query).  Falls back to the subdirectory name when the field is
    absent.  Produces a structured Markdown index and writes it to
    ``wiki_dir/_index.md``.

    Returns the generated index as a string.
    """
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    # Collect all non-index articles and their metadata.
    category_order = ["theme", "concept", "synthesis", "query"]
    buckets: dict[str, list[dict]] = {cat: [] for cat in category_order}
    all_source_ids: set[str] = set()

    for md_file in sorted(wiki_dir.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        meta = read_article_frontmatter(md_file)
        title = str(meta.get("title") or md_file.stem)
        scope = str(meta.get("scope", ""))
        updated_raw = meta.get("updated") or meta.get("updated_at") or ""
        parent_raw = meta.get("parent") or meta.get("parent_slug") or ""
        sources_raw = meta.get("sources", [])
        if isinstance(sources_raw, list):
            for sid in sources_raw:
                all_source_ids.add(str(sid))
        elif isinstance(sources_raw, str) and sources_raw.strip("[]"):
            for sid in sources_raw.strip("[]").split(","):
                sid = sid.strip().strip("'\"")
                if sid:
                    all_source_ids.add(sid)

        # Determine category.
        category = str(meta.get("category", "")).lower()
        if category not in category_order:
            # Fall back to subdirectory name mapping.
            subdir_name = md_file.parent.name.lower()
            _dir_map = {
                "themes": "theme",
                "concepts": "concept",
                "syntheses": "synthesis",
                "queries": "query",
                "gaps": "synthesis",
            }
            category = _dir_map.get(subdir_name, "concept")

        entry = {
            "title": title,
            "slug": md_file.stem,
            "scope": scope,
            "updated": str(updated_raw),
            "parent": str(parent_raw),
        }
        buckets.setdefault(category, []).append(entry)

    article_count = sum(len(v) for v in buckets.values())

    lines: list[str] = [
        "# Knowledge Base Index",
        "",
        f"_Last updated: {now_str}_",
        f"_Articles: {article_count} | Sources indexed: {len(all_source_ids)}_",
    ]

    section_labels = {
        "theme": "Themes",
        "concept": "Concepts",
        "synthesis": "Syntheses",
        "query": "Queries",
    }

    for cat in category_order:
        entries = buckets.get(cat, [])
        if not entries:
            continue
        lines.append("")
        lines.append(f"## {section_labels[cat]}")
        lines.append("")
        for e in entries:
            scope_part = f" — {e['scope']}" if e["scope"] else ""
            parent_part = f" _(parent: [[{e['parent']}]])_" if e["parent"] else ""
            lines.append(f"- [[{e['title']}]]{scope_part}{parent_part}")

    # Recent updates: top 5 by updated field (string sort; ISO dates compare lexically).
    all_entries = [e for cat_entries in buckets.values() for e in cat_entries]
    recent = sorted(
        (e for e in all_entries if e["updated"]),
        key=lambda e: e["updated"],
        reverse=True,
    )[:5]

    if recent:
        lines.append("")
        lines.append("## Recent Updates")
        lines.append("")
        for e in recent:
            lines.append(f"- {e['title']} — {e['updated']}")

    content = "\n".join(lines) + "\n"
    index_path = wiki_dir / "_index.md"
    index_path.write_text(content, encoding="utf-8")
    return content
