"""Wiki sitemap: corpus exploration + structured article plan.

Two-phase process:
  Phase 1 -- Broad shallow exploration (agent loop, adapts reading depth by source type)
  Phase 2 -- Sitemap generation (single LLM call producing structured JSON plan)

The sitemap is saved to data/wiki/_sitemap.json and drives all subsequent
wiki building. Theme articles are written before concept articles; every
corpus source ends up referenced in at least one concept article.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wikify.papers.agent.run_context import RunContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core data contracts (stable -- both sitemap logic and agent.py import these)
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
    domain: str = ""  # domain this entry belongs to (e.g. "material_science")


@dataclass
class WikiSitemap:
    """Full structured plan for the wiki, produced in one LLM call after exploration."""

    entries: list[SitemapEntry]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
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


# ---------------------------------------------------------------------------
# Phase 1: Corpus exploration agent loop
# ---------------------------------------------------------------------------

_EXPLORATION_SYSTEM_PROMPT = """\
You are a corpus structure analyst. Your goal is to understand the shape of this
corpus -- what themes exist, how many sources cover each theme, which sources are
central vs peripheral, and where the natural article boundaries are. Read broadly
and shallowly. Do not drill into sections. Stop reading when you have enough to
plan a wiki structure.

## Source reading strategy
- For papers, reports, proposals, and theses: call read_paper_digest (digest first,
  no sections -- progressive disclosure). These are long; digest is sufficient.
- For web_article, markdown, note, and other: call deep_read directly -- these are
  short and deep_read returns the full content quickly.
- For image and repo_readme: skip detailed reading. The summary and title are enough.

## Workflow
1. Call get_corpus_summary() to see the overall shape of the corpus.
2. Call find_synthesis_opportunities() to identify inter-topic connections.
3. Call find_corpus_gaps() to identify sparse areas.
4. Call get_frontier_exploration_order(max_papers=<limit>) to get the recommended
   coverage order.
5. For each source in the exploration order: read at the appropriate depth as above.

## When to stop
Stop when you have read enough to identify:
- The 3-8 major thematic domains in the corpus.
- The key concepts within each theme (2-5 per theme).
- Which sources are central to each theme vs peripheral.
- Any significant gaps where evidence is sparse.

End your response with a structured summary covering themes, concept candidates,
source assignments, and gap observations. This summary will feed directly into
sitemap planning.
"""

# Short doc types: read fully with deep_read
_SHORT_DOC_TYPES = {"web_article", "markdown", "note", "other"}

# Types where title/summary is enough -- skip detailed reading
_SKIP_DOC_TYPES = {"image", "repo_readme"}

# Academic types: use read_paper_digest
_ACADEMIC_DOC_TYPES = {"paper", "report", "proposal", "thesis"}


def _build_graph_context_block(graph_json: str) -> str:
    """Parse get_graph_metrics() JSON and return a graph context block for the prompt.

    Returns an empty string if the data is missing or malformed.
    """
    try:
        data = json.loads(graph_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Could not parse graph metrics JSON; skipping graph context")
        return ""

    if data.get("error"):
        logger.warning("Graph metrics returned error: %s", data["error"])
        return ""

    def display_names(entries: list[dict], max_n: int = 10) -> list[str]:
        return [e.get("display_name", e.get("id", "unknown")) for e in entries[:max_n]]

    hub_names = display_names(data.get("hub_papers", []))
    bridge_names = display_names(data.get("bridge_papers", []))
    frontier_names = display_names(data.get("frontier_papers", []))

    if not hub_names and not bridge_names and not frontier_names:
        return ""

    lines = [
        "Graph structure of this corpus:",
        "",
        "HUB papers (highest PageRank -- define field consensus):",
    ]
    for name in hub_names:
        lines.append(f"  {name}")

    lines += [
        "",
        "BRIDGE papers (connect different topic clusters -- cross-community insights):",
    ]
    for name in bridge_names:
        lines.append(f"  {name}")

    lines += [
        "",
        "FRONTIER papers (sparse embedding regions -- leading edge):",
    ]
    for name in frontier_names:
        lines.append(f"  {name}")

    lines += [
        "",
        "When planning the wiki structure:",
        "- HUB papers belong as key_source_ids in THEME articles",
        "- BRIDGE papers belong in SYNTHESIS articles or concept articles where two sub-fields meet",  # noqa: E501
        "- FRONTIER papers inform Open Questions sections and stub/draft articles at the edges",
        "",
    ]
    return "\n".join(lines)


def explore_corpus_for_sitemap(
    topic_hint: str,
    model: str | None,
    max_papers: int,
    run_context: "RunContext | None",
) -> tuple[str, list[str]]:
    """Run a shallow agent loop to understand corpus shape before planning.

    Reads broadly with source-type-aware depth (digest for academic, deep_read
    for short sources, skip for images/readmes). Runs for at most 20 turns.

    Also injects graph-awareness (hub/bridge/frontier) into the system prompt and
    provides corpus gap/synthesis context in the first user message.

    Args:
        topic_hint: Optional topic focus (e.g. "ALD materials"). Empty string = whole corpus.
        model: litellm model string. Uses settings.llm_model if None.
        max_papers: Maximum number of sources to read during exploration.
        run_context: Existing run context to bind, or None to create a fresh one.

    Returns:
        (agent_result_content, list_of_explored_source_ids)
    """
    from wikify.papers.agent.core import ScholarForgeAgent
    from wikify.papers.agent.defaults import get_default_hooks, get_explorer_tools
    from wikify.papers.agent.run_context import create_run_context, use_run_context
    from wikify.papers.agent.tools import (
        find_corpus_gaps,
        find_synthesis_opportunities,
        get_graph_metrics,
    )

    # ------------------------------------------------------------------
    # Attempt graph enrichment of the system prompt
    # ------------------------------------------------------------------
    graph_context_block = ""
    try:
        graph_json = get_graph_metrics()
        graph_context_block = _build_graph_context_block(graph_json)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_graph_metrics() failed during sitemap exploration: %s", exc)

    system_prompt = _EXPLORATION_SYSTEM_PROMPT
    if graph_context_block:
        system_prompt = graph_context_block + "\n" + system_prompt

    # ------------------------------------------------------------------
    # Attempt gap/synthesis context for the first user message
    # ------------------------------------------------------------------
    gap_context = ""
    synthesis_context = ""
    try:
        gap_context = find_corpus_gaps()
    except Exception as exc:  # noqa: BLE001
        logger.warning("find_corpus_gaps() failed: %s", exc)

    try:
        synthesis_context = find_synthesis_opportunities()
    except Exception as exc:  # noqa: BLE001
        logger.warning("find_synthesis_opportunities() failed: %s", exc)

    context = run_context or create_run_context(
        topic=topic_hint or "corpus exploration for wiki sitemap",
        strategy="wiki_sitemap_explore",
    )

    # Give a focused exploration budget -- shallow reads are cheap
    token_budget = 80_000
    hooks = get_default_hooks(token_budget)

    agent = ScholarForgeAgent(
        model=model,
        tools=get_explorer_tools(),
        hooks=hooks,
        system_prompt=system_prompt,
        run_context=context,
    )

    hint_clause = f" Focus on: {topic_hint}." if topic_hint else ""

    extra_context_parts: list[str] = []
    if gap_context:
        extra_context_parts.append(f"## Corpus Gaps\n\n{gap_context}")
    if synthesis_context:
        extra_context_parts.append(f"## Synthesis Opportunities\n\n{synthesis_context}")

    extra_context = ("\n\n" + "\n\n".join(extra_context_parts)) if extra_context_parts else ""

    prompt = (
        "Explore the corpus broadly and shallowly to discover its thematic"
        f" structure.{hint_clause}\n"
        f"Read at most {max_papers} sources. Use the workflow described in your system prompt.\n"
        "End with a structured summary of themes, concept candidates, and source assignments."
        f"{extra_context}"
    )

    with use_run_context(context):
        result = agent.run(prompt, max_turns=20)

    # Collect source IDs that were actually read during exploration
    explored_ids: list[str] = []
    seen_ids: set[str] = set()
    for tc in result.tool_calls:
        if tc.tool_name in {"read_paper_digest", "deep_read", "read_section"}:
            pid = tc.arguments.get("paper_id") or tc.arguments.get("paper_id_or_name", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                explored_ids.append(pid)

    return result.content, explored_ids


# ---------------------------------------------------------------------------
# Phase 2: Sitemap generation (single LLM call)
# ---------------------------------------------------------------------------

_SITEMAP_SCHEMA_DESCRIPTION = """\
Produce a JSON sitemap with this exact schema (no markdown fences, raw JSON only):

{
  "entries": [
    {
      "title": "Human-readable article title",
      "slug": "snake_case_filesystem_safe",
      "category": "theme",
      "scope": "One sentence describing what this article covers.",
      "parent_slug": null,
      "key_source_ids": ["display_name strings from the explored sources"],
      "related_slugs": ["slug_of_other_article"],
      "depth": "full",
      "source_types": ["paper", "web_article"],
      "notes": "Reasoning about scope, coverage, or gaps for this article."
    }
  ]
}

Field rules:
- slug: snake_case, lowercase, no spaces, filesystem-safe (ASCII only).
- category: "theme" for top-level domain articles; "concept" for specific
  methods/materials/mechanisms within a theme; "synthesis" for cross-theme articles.
- parent_slug: null for theme articles; set to the parent theme's slug for concepts.
- key_source_ids: use the display_name values (e.g. "Smith 2023 - Title") from the
  exploration summary. Every explored source must appear in at least one entry.
- depth: "stub" if fewer than 2 sources cover this concept; "draft" for 2-4 sources;
  "full" for 5+ sources.
- source_types: list of doc_type values (paper, report, web_article, markdown, etc.)
  from the key sources for this entry.

Quantity targets:
- 3-8 theme articles (the major thematic domains in the corpus).
- 2-5 concept articles per theme.
- Concepts from web_article/markdown sources are fully valid -- do not penalise them.
- Include a "synthesis" entry when two or more themes have significant overlap.

Respond ONLY with valid JSON. No preamble, no explanation, no markdown code fences.
"""


def generate_sitemap(
    topic_hint: str,
    model: str | None,
    wiki_dir: Path,
    max_explore_papers: int,
    run_context: "RunContext | None",
    domain: str = "",
) -> WikiSitemap:
    """Orchestrate two-phase sitemap generation.

    Phase 1: Agent loop explores the corpus broadly and shallowly.
    Phase 2: Single LLM call converts the exploration summary into a JSON sitemap.

    The sitemap is saved to wiki_dir/_sitemap.json and returned.

    Args:
        topic_hint: Optional topic focus for exploration. Empty = whole corpus.
        model: litellm model string. Uses settings.llm_model if None.
        wiki_dir: Root wiki directory (e.g. Path("data/wiki")).
        max_explore_papers: Max sources to read during Phase 1 exploration.
        run_context: Existing RunContext to reuse, or None to create fresh.
        domain: Optional domain label (e.g. "material_science"). When set, included
            in the sitemap JSON prompt and stored on each SitemapEntry.

    Returns:
        WikiSitemap populated from the LLM's JSON response.
    """
    from wikify.core.config import settings
    from wikify.core.llm.client import complete

    effective_model = model or settings.llm_model

    # ------------------------------------------------------------------
    # Phase 1: Explore
    # ------------------------------------------------------------------
    logger.info(
        "Sitemap Phase 1: exploring corpus (max_papers=%d, model=%s, domain=%s)",
        max_explore_papers,
        effective_model,
        domain or "<all>",
    )
    exploration_text, explored_ids = explore_corpus_for_sitemap(
        topic_hint=topic_hint,
        model=model,
        max_papers=max_explore_papers,
        run_context=run_context,
    )
    logger.info(
        "Exploration complete: %d source IDs recorded, %d chars of summary",
        len(explored_ids),
        len(exploration_text),
    )

    # ------------------------------------------------------------------
    # Phase 2: Generate sitemap JSON
    # ------------------------------------------------------------------
    logger.info("Sitemap Phase 2: generating structured JSON plan")

    domain_clause = f"\nDomain: {domain}\n" if domain else ""
    user_msg = (
        "Below is a structured exploration summary of the corpus. "
        "Use it to produce a wiki sitemap as specified.\n\n"
        f"{domain_clause}"
        "## Corpus Exploration Summary\n\n"
        f"{exploration_text}\n\n"
        "---\n\n"
        f"{_SITEMAP_SCHEMA_DESCRIPTION}"
    )

    raw_json = complete(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a knowledge architect. You receive a corpus exploration "
                    "summary and produce a structured wiki sitemap in JSON format. "
                    "Respond with valid JSON only -- no preamble, no markdown fences."
                ),
            },
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.2,
        max_tokens=4000,
        use_cache=False,
    )

    # Parse -- strip any accidental markdown fences
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        cleaned = cleaned[first_newline + 1 :] if first_newline != -1 else cleaned
    if cleaned.endswith("```"):
        cleaned = cleaned[: cleaned.rfind("```")]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Attempt boundary recovery (find outermost JSON object)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                raise ValueError(
                    f"Could not parse sitemap JSON from LLM response: {cleaned[:300]}"
                ) from exc
        else:
            raise ValueError(
                f"Could not parse sitemap JSON from LLM response: {cleaned[:300]}"
            ) from exc

    raw_entries = data.get("entries", [])
    entries: list[SitemapEntry] = []
    for e in raw_entries:
        # Validate depth -- fall back to "draft" if LLM emits something unexpected
        raw_depth = e.get("depth", "draft")
        depth: ArticleDepth = raw_depth if raw_depth in ("stub", "draft", "full") else "draft"

        raw_category = e.get("category", "concept")
        category: ArticleCategory = (
            raw_category
            if raw_category in ("theme", "concept", "synthesis", "query")
            else "concept"
        )

        entries.append(
            SitemapEntry(
                title=e.get("title", "Untitled"),
                slug=e.get("slug", "untitled"),
                category=category,
                scope=e.get("scope", ""),
                parent_slug=e.get("parent_slug"),
                key_source_ids=e.get("key_source_ids", []),
                related_slugs=e.get("related_slugs", []),
                depth=depth,
                source_types=e.get("source_types", []),
                notes=e.get("notes", ""),
                domain=domain,
            )
        )

    sitemap = WikiSitemap(
        entries=entries,
        corpus_summary=exploration_text[:2000],  # truncate for storage
        model=effective_model,
    )

    sitemap.save(wiki_dir)
    logger.info(
        "Sitemap saved: %d entries (%d themes, %d concepts)",
        len(entries),
        len(sitemap.themes()),
        len(sitemap.concepts()),
    )
    return sitemap


# ---------------------------------------------------------------------------
# Multi-domain sitemap generation
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "material_science": [
        "material_science",
        "materials",
        "ald",
        "deposition",
        "semiconductor",
        "oxide",
        "thin_film",
    ],
    "machine_learning": [
        "machine_learning",
        "ml",
        "deep_learning",
        "neural",
        "ai",
        "nlp",
        "computer_vision",
    ],
    "chemistry": ["chemistry", "chemical", "synthesis", "reaction", "catalyst"],
    "biology": ["biology", "biomedical", "genomics", "protein", "cell"],
    "physics": ["physics", "quantum", "photonics", "optics"],
}


def _classify_paper_domain(paper) -> str:  # type: ignore[no-untyped-def]
    """Classify a Paper into a domain string.

    Strategy (in order):
    1. Check source_path segments for known domain keywords.
    2. Use the paper's most frequent PaperTopic topic prefix.
    3. Fall back to "general".
    """
    source_path = (paper.source_path or "").lower().replace("\\", "/")
    path_segments = set(source_path.replace("/", " ").replace("_", " ").split())

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            kw_parts = kw.replace("_", " ").split()
            if all(part in path_segments for part in kw_parts):
                return domain
            # Also check as substring in full path
            if kw in source_path:
                return domain

    return ""  # caller will use topic-based fallback


def _topics_to_domain(topics: list[str]) -> str:
    """Map a list of topic strings to a domain, or 'general'."""
    if not topics:
        return "general"

    topic_text = " ".join(topics).lower()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw.replace("_", " ") in topic_text or kw in topic_text:
                return domain

    # Use the first topic as a domain name (normalised)
    first = topics[0].lower().strip()
    first = first.replace(" ", "_").replace("-", "_")
    return first or "general"


def generate_multi_domain_sitemap(
    wiki_dir: Path,
    model: str | None = None,
    max_explore_papers: int = 30,
) -> dict[str, WikiSitemap]:
    """Generate per-domain sitemaps for a multi-domain corpus.

    Steps:
    1. Load all Paper rows and classify into domains.
    2. For each domain with >=5 papers: call generate_sitemap(domain=...).
    3. Detect cross-domain synthesis via find_synthesis_opportunities().
    4. Add synthesis SitemapEntry objects to all relevant domain sitemaps.
    5. Return {domain: WikiSitemap} dict.

    Args:
        wiki_dir: Root wiki directory.
        model: LLM model string (uses settings default if None).
        max_explore_papers: Max papers to read during each domain's exploration.

    Returns:
        Mapping from domain name to its WikiSitemap.
    """
    from sqlmodel import select

    from wikify.papers.agent.tools import find_synthesis_opportunities
    from wikify.core.store.db import get_session
    from wikify.core.store.models import Paper, PaperTopic

    # ------------------------------------------------------------------
    # Step 1: Load papers + topics, classify domains
    # ------------------------------------------------------------------
    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        topics_rows = session.exec(select(PaperTopic)).all()

    # Build paper_id -> list[topic] mapping
    paper_topics: dict[str, list[str]] = {}
    for row in topics_rows:
        paper_topics.setdefault(row.paper_id, []).append(row.topic)

    domain_papers: dict[str, list] = {}
    for paper in papers:
        # Try path-based classification first
        domain = _classify_paper_domain(paper)
        if not domain:
            # Fall back to topic-based classification
            domain = _topics_to_domain(paper_topics.get(paper.id, []))
        domain_papers.setdefault(domain, []).append(paper)

    logger.info(
        "Multi-domain classification: %s",
        {d: len(ps) for d, ps in domain_papers.items()},
    )

    # ------------------------------------------------------------------
    # Step 2: Generate per-domain sitemaps for domains with >=5 papers
    # ------------------------------------------------------------------
    domain_sitemaps: dict[str, WikiSitemap] = {}
    for domain, domain_paper_list in domain_papers.items():
        if len(domain_paper_list) < 5:
            logger.info(
                "Skipping domain '%s' -- only %d papers (need >=5)",
                domain,
                len(domain_paper_list),
            )
            continue

        logger.info(
            "Generating sitemap for domain '%s' (%d papers)",
            domain,
            len(domain_paper_list),
        )
        sitemap = generate_sitemap(
            topic_hint="",
            model=model,
            wiki_dir=wiki_dir / "domains" / domain,
            max_explore_papers=max_explore_papers,
            run_context=None,
            domain=domain,
        )
        domain_sitemaps[domain] = sitemap

    # ------------------------------------------------------------------
    # Step 3: Detect cross-domain synthesis opportunities
    # ------------------------------------------------------------------
    if len(domain_sitemaps) < 2:
        return domain_sitemaps

    try:
        synthesis_text = find_synthesis_opportunities()
    except Exception as exc:  # noqa: BLE001
        logger.warning("find_synthesis_opportunities() failed for multi-domain: %s", exc)
        synthesis_text = ""

    # Build a set of paper display names appearing in synthesis text
    # (approximate: any source_id from one domain appearing in synthesis text
    # when sources from another domain also appear)
    domain_list = list(domain_sitemaps.keys())

    for i, domain_a in enumerate(domain_list):
        for domain_b in domain_list[i + 1 :]:
            # Collect source ids from both domains
            a_sources: set[str] = set()
            b_sources: set[str] = set()
            for entry in domain_sitemaps[domain_a].entries:
                a_sources.update(entry.key_source_ids)
            for entry in domain_sitemaps[domain_b].entries:
                b_sources.update(entry.key_source_ids)

            # Check if sources from both domains appear in synthesis output
            a_hits = [s for s in a_sources if s and s in synthesis_text]
            b_hits = [s for s in b_sources if s and s in synthesis_text]

            if not a_hits or not b_hits:
                continue

            # Create a cross-domain synthesis entry
            synth_slug = f"synthesis_{domain_a}_{domain_b}"
            a_label = domain_a.replace("_", " ").title()
            b_label = domain_b.replace("_", " ").title()
            synth_title = f"{a_label} x {b_label} Synthesis"
            synth_entry = SitemapEntry(
                title=synth_title,
                slug=synth_slug,
                category="synthesis",
                scope=f"Cross-domain synthesis connecting {domain_a} and {domain_b}.",
                parent_slug=None,
                key_source_ids=a_hits[:5] + b_hits[:5],
                related_slugs=[],
                depth="draft",
                source_types=["paper"],
                notes=f"Auto-detected cross-domain synthesis between {domain_a} and {domain_b}.",
                domain="",
            )

            # Add to all relevant domain sitemaps
            for domain in (domain_a, domain_b):
                domain_sitemaps[domain].entries.append(synth_entry)
            logger.info(
                "Created cross-domain synthesis entry: %s <-> %s",
                domain_a,
                domain_b,
            )

    return domain_sitemaps
