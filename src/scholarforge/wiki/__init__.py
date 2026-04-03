"""Wiki layer: LLM-authored concept articles built from the corpus."""

from scholarforge.wiki.agent import build_article_from_entry, build_wiki_from_sitemap
from scholarforge.wiki.builder import (
    article_path,
    find_stale_articles,
    read_article_frontmatter,
    slugify,
    write_article,
)
from scholarforge.wiki.sitemap import SitemapEntry, WikiSitemap, generate_sitemap

__all__ = [
    # builder
    "article_path",
    "find_stale_articles",
    "read_article_frontmatter",
    "slugify",
    "write_article",
    # sitemap
    "SitemapEntry",
    "WikiSitemap",
    "generate_sitemap",
    # agent (sitemap-driven)
    "build_article_from_entry",
    "build_wiki_from_sitemap",
]
