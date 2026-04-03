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

    Walks all .md files under wiki_dir (excluding _*.md files) and builds
    a compact index grouped by subdirectory.

    Returns the index markdown as a string.
    """
    from datetime import date

    lines: list[str] = [
        "# Wiki Index",
        "",
        f"_Generated: {date.today().isoformat()}_",
        "",
    ]

    for subdir in sorted(wiki_dir.iterdir()):
        if not subdir.is_dir():
            continue
        articles = sorted(subdir.glob("*.md"))
        if not articles:
            continue
        lines.append(f"## {subdir.name.title()}")
        lines.append("")
        for article in articles:
            meta = read_article_frontmatter(article)
            title = meta.get("title") or article.stem
            status = meta.get("status", "")
            topics_raw = meta.get("topics", "")
            topic_str = ""
            if isinstance(topics_raw, list):
                topic_str = ", ".join(topics_raw[:3])
            elif isinstance(topics_raw, str) and topics_raw:
                # Strip brackets
                topic_str = topics_raw.strip("[]")
            status_badge = f" `{status}`" if status else ""
            topic_badge = f" — {topic_str}" if topic_str else ""
            lines.append(f"- [[{article.stem}]] {title}{status_badge}{topic_badge}")
        lines.append("")

    return "\n".join(lines)
