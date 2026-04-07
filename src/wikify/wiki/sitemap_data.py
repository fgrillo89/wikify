"""Pure-data sitemap entry / sitemap classes used by index generation.

Originally part of the legacy sitemap-first wiki build flow. Only the
data structures survive: ``SitemapEntry`` and ``WikiSitemap`` describe
the shape that ``wiki/builder`` index generators consume. The LLM-driven
``generate_sitemap`` / ``build_wiki_from_sitemap`` flow has been deleted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ArticleDepth = Literal["stub", "draft", "full"]
ArticleCategory = Literal["theme", "concept", "synthesis", "query"]


@dataclass
class SitemapEntry:
    """One planned wiki article."""

    title: str
    slug: str
    category: ArticleCategory
    scope: str
    parent_slug: str | None
    key_source_ids: list[str]
    related_slugs: list[str]
    depth: ArticleDepth
    source_types: list[str]
    notes: str = ""
    domain: str = ""


@dataclass
class WikiSitemap:
    """Structured plan for a wiki built from a corpus."""

    entries: list[SitemapEntry]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    corpus_summary: str = ""
    model: str = ""

    def themes(self) -> list[SitemapEntry]:
        return [e for e in self.entries if e.category == "theme"]

    def concepts(self) -> list[SitemapEntry]:
        return [e for e in self.entries if e.category == "concept"]

    def by_slug(self) -> dict[str, SitemapEntry]:
        return {e.slug: e for e in self.entries}

    def ordered_for_writing(self) -> list[SitemapEntry]:
        themes = self.themes()
        rest = [e for e in self.entries if e.category != "theme"]
        return themes + rest

    def save(self, wiki_dir: Path) -> Path:
        path = wiki_dir / "_sitemap.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "generated_at": self.generated_at,
            "corpus_summary": self.corpus_summary,
            "model": self.model,
            "entries": [
                {
                    "title": e.title,
                    "slug": e.slug,
                    "category": e.category,
                    "scope": e.scope,
                    "parent_slug": e.parent_slug,
                    "key_source_ids": e.key_source_ids,
                    "related_slugs": e.related_slugs,
                    "depth": e.depth,
                    "source_types": e.source_types,
                    "notes": e.notes,
                    "domain": e.domain,
                }
                for e in self.entries
            ],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Saved sitemap with %d entries to %s", len(self.entries), path)
        return path

    @classmethod
    def load(cls, wiki_dir: Path) -> WikiSitemap | None:
        path = wiki_dir / "_sitemap.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            SitemapEntry(
                title=e["title"],
                slug=e["slug"],
                category=e["category"],
                scope=e["scope"],
                parent_slug=e.get("parent_slug"),
                key_source_ids=e.get("key_source_ids", []),
                related_slugs=e.get("related_slugs", []),
                depth=e.get("depth", "draft"),
                source_types=e.get("source_types", []),
                notes=e.get("notes", ""),
                domain=e.get("domain", ""),
            )
            for e in data.get("entries", [])
        ]
        return cls(
            entries=entries,
            generated_at=data.get("generated_at", ""),
            corpus_summary=data.get("corpus_summary", ""),
            model=data.get("model", ""),
        )


__all__ = ["ArticleCategory", "ArticleDepth", "SitemapEntry", "WikiSitemap"]
