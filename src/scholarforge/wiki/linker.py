"""Wiki cross-linker: add backlinks and See Also sections after all articles are written."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from scholarforge.wiki.builder import read_article_frontmatter
from scholarforge.wiki.sitemap import WikiSitemap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SEE_ALSO_HEADER = "## See Also"
REFERENCES_HEADER = "## References"
CONCEPTS_HEADERS = ("## Concepts", "## Topics Covered")


def _slug_to_title(wiki_dir: Path) -> dict[str, str]:
    """Scan all .md files under wiki_dir and return {slug: title} from frontmatter.

    Skips files whose names start with '_' (index, sitemap, etc.).
    """
    result: dict[str, str] = {}
    for md_file in wiki_dir.rglob("*.md"):
        if md_file.name.startswith("_"):
            continue
        meta = read_article_frontmatter(md_file)
        title = meta.get("title") or md_file.stem
        slug = md_file.stem
        result[slug] = str(title)
    return result


def cross_link_articles(wiki_dir: Path, sitemap: WikiSitemap | None) -> int:
    """Add backlinks and See Also sections to all wiki articles.

    If *sitemap* is provided, uses ``SitemapEntry.related_slugs`` to determine
    which articles should link to each other.  If sitemap is None, falls back to
    slug-matching: an article gains a See-Also entry for every other article
    whose *title* appears verbatim in its body text.

    Returns the count of articles that were updated.
    """
    slug_title = _slug_to_title(wiki_dir)

    # Build a mapping of slug -> set[title] to add as see-also links.
    links: dict[str, set[str]] = {slug: set() for slug in slug_title}

    if sitemap is not None:
        title_by_slug: dict[str, str] = {}
        for entry in sitemap.entries:
            title_by_slug[entry.slug] = entry.title

        for entry in sitemap.entries:
            for related_slug in entry.related_slugs:
                if related_slug in title_by_slug:
                    links.setdefault(entry.slug, set()).add(title_by_slug[related_slug])
    else:
        # Slug-matching fallback: check for verbatim title occurrences in body text.
        # Build a map of all article file paths for body reading.
        slug_to_path: dict[str, Path] = {}
        for md_file in wiki_dir.rglob("*.md"):
            if not md_file.name.startswith("_"):
                slug_to_path[md_file.stem] = md_file

        for slug, path in slug_to_path.items():
            body = _read_body(path)
            for other_slug, other_title in slug_title.items():
                if other_slug == slug:
                    continue
                if other_title and other_title in body:
                    links.setdefault(slug, set()).add(other_title)

    updated = 0
    for md_file in wiki_dir.rglob("*.md"):
        if md_file.name.startswith("_"):
            continue
        slug = md_file.stem
        titles_to_add = links.get(slug, set())
        if not titles_to_add:
            continue
        if _update_see_also(md_file, titles_to_add):
            updated += 1

    return updated


def ensure_parent_backlinks(wiki_dir: Path, sitemap: WikiSitemap) -> None:
    """Ensure each concept article is listed in its parent theme's Concepts section.

    For each concept entry in the sitemap, opens the parent theme article and
    ensures the concept's title appears in a ``## Concepts`` or
    ``## Topics Covered`` section.  If the section is absent, appends it.
    If the concept is already listed, skips silently.
    """
    by_slug = sitemap.by_slug()

    for entry in sitemap.concepts():
        if not entry.parent_slug:
            continue
        parent_entry = by_slug.get(entry.parent_slug)
        if parent_entry is None:
            logger.debug("Parent slug %r not found in sitemap; skipping", entry.parent_slug)
            continue

        # Find the parent file on disk.
        parent_path = _find_article_path(wiki_dir, entry.parent_slug)
        if parent_path is None:
            logger.debug("Parent article file for %r not found; skipping", entry.parent_slug)
            continue

        _ensure_listed_in_section(parent_path, entry.title, CONCEPTS_HEADERS)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_body(path: Path) -> str:
    """Read the article file and return the body text (after the frontmatter)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    # Strip frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :]
    return text


def _find_article_path(wiki_dir: Path, slug: str) -> Path | None:
    """Search wiki_dir recursively for a .md file matching slug."""
    for md_file in wiki_dir.rglob(f"{slug}.md"):
        if not md_file.name.startswith("_"):
            return md_file
    return None


def _update_see_also(path: Path, titles: set[str]) -> bool:
    """Add or update the See Also section in the given article file.

    Returns True if the file was modified, False otherwise.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    bullet_lines = sorted(f"- [[{t}]]" for t in titles)

    # Check if a See Also section already exists.
    see_also_re = re.compile(
        r"(##\s+See\s+Also\s*\n)((?:- \[\[.*?\]\]\n?)*)",
        re.IGNORECASE,
    )
    match = see_also_re.search(text)
    if match:
        existing_block = match.group(2)
        existing_links = set(re.findall(r"\[\[(.*?)\]\]", existing_block))
        new_titles = {t for t in titles if t not in existing_links}
        if not new_titles:
            return False
        extra_bullets = sorted(f"- [[{t}]]" for t in new_titles)
        new_block = existing_block.rstrip("\n") + "\n" + "\n".join(extra_bullets) + "\n"
        new_text = text[: match.start(2)] + new_block + text[match.end(2) :]
        path.write_text(new_text, encoding="utf-8")
        return True

    # No See Also section — insert one.
    section_content = SEE_ALSO_HEADER + "\n\n" + "\n".join(bullet_lines) + "\n"

    # Insert before ## References if present, else append at end.
    refs_re = re.compile(r"^##\s+References\s*$", re.MULTILINE | re.IGNORECASE)
    refs_match = refs_re.search(text)
    if refs_match:
        insert_pos = refs_match.start()
        new_text = text[:insert_pos] + section_content + "\n" + text[insert_pos:]
    else:
        new_text = text.rstrip("\n") + "\n\n" + section_content

    path.write_text(new_text, encoding="utf-8")
    return True


def _ensure_listed_in_section(path: Path, title: str, section_headers: tuple[str, ...]) -> None:
    """Ensure *title* appears as a bullet in one of the named sections.

    If none of the sections exist, appends the first header from *section_headers*
    with the bullet.  If the article is already listed, does nothing.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    bullet = f"- [[{title}]]"

    # Check if already listed anywhere in the file.
    if f"[[{title}]]" in text:
        return

    # Try to find an existing matching section.
    for header in section_headers:
        pattern = re.compile(
            r"(" + re.escape(header) + r"\s*\n)((?:.*\n)*?)(?=^##|\Z)",
            re.MULTILINE,
        )
        match = pattern.search(text)
        if match:
            section_end = match.end(2)
            insertion = match.group(2).rstrip("\n") + "\n" + bullet + "\n"
            new_text = text[: match.start(2)] + insertion + text[section_end:]
            path.write_text(new_text, encoding="utf-8")
            return

    # No matching section found — append it.
    new_section = f"\n{section_headers[0]}\n\n{bullet}\n"
    path.write_text(text.rstrip("\n") + new_section, encoding="utf-8")
