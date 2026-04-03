"""Wiki sitemap: corpus exploration + structured article plan.

Two-phase process:
  Phase 1 — Broad shallow exploration (agent loop, adapts reading depth by source type)
  Phase 2 — Sitemap generation (single LLM call producing structured JSON plan)

The sitemap is saved to data/wiki/_sitemap.json and drives all subsequent
wiki building. Theme articles are written before concept articles; every
corpus source ends up referenced in at least one concept article.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core data contracts (stable — both sitemap logic and agent.py import these)
# ---------------------------------------------------------------------------

ArticleDepth = Literal["stub", "draft", "full"]
ArticleCategory = Literal["theme", "concept", "synthesis", "query"]


@dataclass
class SitemapEntry:
    """One planned wiki article, produced by the sitemap agent."""

    title: str
    slug: str
    category: ArticleCategory  # theme | concept | synthesis | query
    scope: str  # one-sentence description of what this article covers
    parent_slug: str | None  # slug of the parent theme article, or None for themes
    key_source_ids: list[str]  # Paper.id values most relevant to this article
    related_slugs: list[str]  # other articles to cross-link to
    depth: ArticleDepth  # stub | draft | full
    source_types: list[str]  # e.g. ["paper", "web_article", "markdown"]
    notes: str = ""  # LLM's reasoning about scope/gaps for this article


@dataclass
class WikiSitemap:
    """Full structured plan for the wiki, produced in one LLM call after exploration."""

    entries: list[SitemapEntry]
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    corpus_summary: str = ""  # snapshot of corpus shape at generation time
    model: str = ""

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def themes(self) -> list[SitemapEntry]:
        return [e for e in self.entries if e.category == "theme"]

    def concepts(self) -> list[SitemapEntry]:
        return [e for e in self.entries if e.category == "concept"]

    def by_slug(self) -> dict[str, SitemapEntry]:
        return {e.slug: e for e in self.entries}

    def ordered_for_writing(self) -> list[SitemapEntry]:
        """Return entries in dependency order: themes first, then concepts."""
        themes = self.themes()
        rest = [e for e in self.entries if e.category != "theme"]
        return themes + rest

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

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
            )
            for e in data.get("entries", [])
        ]
        return cls(
            entries=entries,
            generated_at=data.get("generated_at", ""),
            corpus_summary=data.get("corpus_summary", ""),
            model=data.get("model", ""),
        )
