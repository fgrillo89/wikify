"""Wiki layer: LLM-authored concept articles built from the corpus."""

from wikify.wiki.agent import build_article_from_entry, build_wiki_from_sitemap
from wikify.wiki.builder import (
    article_path,
    find_stale_articles,
    read_article_frontmatter,
    slugify,
    write_article,
)
from wikify.wiki.mapreduce import (
    SourceExtraction,
    map_chunks_to_topic,
    record_coverage,
    reduce_to_article,
)
from wikify.wiki.persona import (
    generate_domain_persona,
    get_or_create_persona,
    invalidate_persona,
)
from wikify.wiki.sitemap import SitemapEntry, WikiSitemap, generate_sitemap

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
