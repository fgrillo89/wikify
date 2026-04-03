"""Wiki layer: LLM-authored concept articles built from the corpus."""

from scholarforge.wiki.agent import build_article_from_entry, build_wiki_from_sitemap
from scholarforge.wiki.builder import (
    article_path,
    find_stale_articles,
    read_article_frontmatter,
    slugify,
    write_article,
)
from scholarforge.wiki.mapreduce import (
    SourceExtraction,
    map_chunks_to_topic,
    record_coverage,
    reduce_to_article,
)
from scholarforge.wiki.persona import (
    generate_domain_persona,
    get_or_create_persona,
    invalidate_persona,
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
    # persona
    "generate_domain_persona",
    "get_or_create_persona",
    "invalidate_persona",
    # mapreduce
    "SourceExtraction",
    "map_chunks_to_topic",
    "record_coverage",
    "reduce_to_article",
]
