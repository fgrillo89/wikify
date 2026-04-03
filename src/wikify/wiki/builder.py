"""Wiki article file management: slugify, write, read, and staleness detection.

Also provides hierarchical index generation:
  - generate_theme_index   -- per-theme index (concept table + open Qs + graph highlights)
  - generate_domain_index  -- per-domain master index (themes table)
  - generate_library_catalog -- top-level library catalog (_index.md)
  - append_unanswered_question -- append to _unanswered.jsonl
  - generate_wiki_index    -- backward-compat wrapper (single unnamed domain)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Article provenance: evidence enrichment + source resolution
# ---------------------------------------------------------------------------


def build_evidence_brief(concept_id: str, max_evidence: int = 10) -> list[dict]:
    """Build a list of evidence entries for an article writing brief.

    Queries ConceptEvidence for the concept and returns enriched entries
    with paper display names for the writing agent to cite.

    Args:
        concept_id: ConceptRecord.id (slug).
        max_evidence: Maximum evidence entries to include.

    Returns:
        List of dicts with: paper_id, paper_display, quote, chunk_id
    """
    from sqlmodel import select

    from wikify.store.db import get_session
    from wikify.store.models import ConceptEvidence, Paper

    with get_session() as session:
        evidence_rows: list[ConceptEvidence] = list(
            session.exec(
                select(ConceptEvidence)
                .where(ConceptEvidence.concept_id == concept_id)
                .where(ConceptEvidence.verified == True)  # noqa: E712
                .limit(max_evidence)
            ).all()
        )

    if not evidence_rows:
        return []

    # Build paper display name lookup
    paper_ids = list({e.paper_id for e in evidence_rows})
    with get_session() as session:
        paper_display: dict[str, str] = {}
        for pid in paper_ids:
            p = session.get(Paper, pid)
            if p is not None:
                paper_display[pid] = p.display_name()

    return [
        {
            "paper_id": e.paper_id,
            "paper_display": paper_display.get(e.paper_id, e.paper_id[:16]),
            "quote": e.evidence_quote,
            "chunk_id": e.chunk_id,
        }
        for e in evidence_rows
    ]


def resolve_article_sources(article_path_obj: Path) -> list[str]:
    """Scan an article for [REF:display_name] markers and resolve to paper IDs.

    Reads the article body, finds all [REF:...] markers, looks up each
    display name against the Paper table, and returns the matching paper IDs.

    Also updates the article's YAML frontmatter `sources:` field with
    the resolved paper IDs.

    Args:
        article_path_obj: Path to the .md article file.

    Returns:
        List of resolved paper IDs.
    """
    from sqlmodel import select

    from wikify.store.db import get_session
    from wikify.store.models import Paper

    if not article_path_obj.exists():
        return []

    text = article_path_obj.read_text(encoding="utf-8", errors="replace")

    # Find all [REF:...] markers
    ref_pattern = re.compile(r"\[REF:([^\]]+)\]")
    ref_names = ref_pattern.findall(text)

    if not ref_names:
        return []

    # Build display_name -> paper_id lookup
    with get_session() as session:
        all_papers: list[Paper] = list(session.exec(select(Paper)).all())

    display_to_id: dict[str, str] = {}
    for p in all_papers:
        display_to_id[p.display_name()] = p.id
        # Also index by partial matches (first author + year)
        authors = p.parsed_authors
        first_author = authors[0].split()[-1] if authors else ""
        if first_author and p.year:
            display_to_id[f"{first_author} {p.year}"] = p.id

    # Resolve each REF marker
    resolved_ids: list[str] = []
    for ref_name in ref_names:
        ref_clean = ref_name.strip()
        # Try exact match first
        pid = display_to_id.get(ref_clean)
        if pid is None:
            # Try fuzzy: check if any display name starts with the ref
            for display, paper_id in display_to_id.items():
                if display.startswith(ref_clean) or ref_clean.startswith(display.split(" - ")[0]):
                    pid = paper_id
                    break
        if pid and pid not in resolved_ids:
            resolved_ids.append(pid)

    if not resolved_ids:
        return []

    # Build paper_id -> display info for readable sources
    id_to_display: dict[str, str] = {}
    for p in all_papers:
        authors = p.parsed_authors
        first_author = authors[0].split()[-1] if authors else "Unknown"
        year = p.year or "?"
        title_short = (p.title or "Untitled")[:80]
        id_to_display[p.id] = f"{first_author} {year} - {title_short}"

    # Update frontmatter sources
    sources_yaml = "\n".join(f"  - {sid}" for sid in resolved_ids)
    text = re.sub(
        r"sources:\n  \[\]",
        f"sources:\n{sources_yaml}",
        text,
    )

    # Append or replace ## Sources section (human-readable + machine hash)
    sources_section = "\n## Sources\n\n"
    for pid in resolved_ids:
        display = id_to_display.get(pid, pid[:16])
        sources_section += f"- {display} `{pid[:12]}`\n"

    # Remove existing ## Sources section if present
    text = re.sub(
        r"\n## Sources\n.*",
        "",
        text,
        flags=re.DOTALL,
    )
    text = text.rstrip() + "\n" + sources_section

    article_path_obj.write_text(text, encoding="utf-8")

    logger.info(
        "resolve_article_sources: %s -> %d sources resolved",
        article_path_obj.name,
        len(resolved_ids),
    )
    return resolved_ids


def resolve_all_article_sources(wiki_dir: Path) -> int:
    """Resolve [REF:] markers to paper IDs for all wiki articles.

    Args:
        wiki_dir: Root wiki directory.

    Returns:
        Total number of source links resolved across all articles.
    """
    total = 0
    for md_file in wiki_dir.rglob("*.md"):
        if md_file.name.startswith("_"):
            continue
        resolved = resolve_article_sources(md_file)
        total += len(resolved)

    logger.info(
        "resolve_all_article_sources: %d total source links resolved",
        total,
    )
    return total


# ---------------------------------------------------------------------------
# Parameter table generation
# ---------------------------------------------------------------------------


def generate_parameter_table(concept_id: str) -> str:
    """Generate a markdown parameter table for a concept.

    Queries ParameterExtraction rows for the given concept and builds
    a formatted markdown table. Returns empty string if no parameters.

    Args:
        concept_id: ConceptRecord.id (slug) to look up parameters for.

    Returns:
        Markdown string with ## Parameters heading and table, or "".
    """
    from sqlmodel import select

    from wikify.store.db import get_session
    from wikify.store.models import Paper, ParameterExtraction

    with get_session() as session:
        params: list[ParameterExtraction] = list(
            session.exec(
                select(ParameterExtraction).where(ParameterExtraction.concept_id == concept_id)
            ).all()
        )

    if not params:
        return ""

    # Build paper display name lookup for source column
    paper_ids = list({p.paper_id for p in params})
    with get_session() as session:
        papers = {
            p.id: p.display_name()
            for pid in paper_ids
            if (p := session.get(Paper, pid)) is not None
        }

    lines = [
        "## Parameters",
        "",
        "| Parameter | Value | Unit | Conditions | Source |",
        "|-----------|-------|------|------------|--------|",
    ]

    for p in params:
        source = papers.get(p.paper_id, p.paper_id[:16])
        # Truncate long source names
        if len(source) > 30:
            source = source[:27] + "..."
        lines.append(f"| {p.parameter_name} | {p.value} | {p.unit} | {p.conditions} | {source} |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Index condensation
# ---------------------------------------------------------------------------


def generate_domain_condensation(
    wiki_dir: Path,
    domain_label: str,
    concept_ids: list[str],
) -> Path:
    """Generate a condensed _index.md for a domain directory.

    Creates a compact summary of all concepts in the domain, suitable for
    injecting into LLM context without loading every article. Each concept
    gets a one-line entry with its type, importance, status, and definition.

    The condensation follows the pattern of summary files at each
    hierarchy level, adapted for our domain/concept structure.

    Args:
        wiki_dir: Root wiki directory (e.g. data/wiki/).
        domain_label: Domain name (used for directory and heading).
        concept_ids: List of ConceptRecord.id values in this domain.

    Returns:
        Path to the generated _index.md file.
    """
    from sqlmodel import select

    from wikify.store.db import get_session
    from wikify.store.models import ConceptRecord, ParameterExtraction

    domain_slug = slugify(domain_label)
    domain_dir = wiki_dir / "domains" / domain_slug
    domain_dir.mkdir(parents=True, exist_ok=True)

    # Load concepts
    with get_session() as session:
        concepts: list[ConceptRecord] = []
        for cid in concept_ids:
            c = session.get(ConceptRecord, cid)
            if c is not None:
                concepts.append(c)

    # Sort by importance descending
    concepts.sort(key=lambda c: c.importance, reverse=True)

    # Count parameters per concept
    param_counts: dict[str, int] = {}
    if concepts:
        with get_session() as session:
            for c in concepts:
                count = len(
                    list(
                        session.exec(
                            select(ParameterExtraction).where(
                                ParameterExtraction.concept_id == c.id
                            )
                        ).all()
                    )
                )
                if count > 0:
                    param_counts[c.id] = count

    # Build condensed index
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {domain_label}",
        "",
        f"_Condensed index -- {len(concepts)} concepts -- {now}_",
        "",
    ]

    # Stats summary
    status_counts = {"none": 0, "stub": 0, "draft": 0, "full": 0}
    for c in concepts:
        key = c.article_status if c.article_status in status_counts else "none"
        status_counts[key] += 1

    type_counts: dict[str, int] = {}
    for c in concepts:
        if c.concept_type:
            type_counts[c.concept_type] = type_counts.get(c.concept_type, 0) + 1

    lines.append("## Overview")
    lines.append("")
    lines.append("| Stat | Value |")
    lines.append("|------|-------|")
    lines.append(f"| Total concepts | {len(concepts)} |")
    for status, count in status_counts.items():
        if count > 0:
            lines.append(f"| {status} articles | {count} |")
    if param_counts:
        lines.append(f"| Concepts with parameters | {len(param_counts)} |")
    lines.append("")

    if type_counts:
        lines.append(
            "**Concept types:** "
            + ", ".join(
                f"{t} ({n})"
                for t, n in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
            )
        )
        lines.append("")

    # Concept table (compact: one line per concept)
    lines.append("## Concepts")
    lines.append("")
    lines.append("| Concept | Type | Importance | Status | Definition |")
    lines.append("|---------|------|------------|--------|------------|")

    for c in concepts:
        imp = f"{c.importance:.2f}" if c.importance > 0 else "-"
        defn = (c.definition or "")[:60]
        if len(c.definition or "") > 60:
            defn += "..."
        ctype = c.concept_type or "-"
        status = c.article_status or "none"
        lines.append(f"| [[{c.name}]] | {ctype} | {imp} | {status} | {defn} |")

    lines.append("")

    # Top parameters (if any)
    if param_counts:
        lines.append("## Key Parameters")
        lines.append("")
        top_param_concepts = sorted(
            param_counts.keys(), key=lambda k: param_counts[k], reverse=True
        )[:10]

        with get_session() as session:
            for cid in top_param_concepts:
                params = list(
                    session.exec(
                        select(ParameterExtraction)
                        .where(ParameterExtraction.concept_id == cid)
                        .limit(3)
                    ).all()
                )
                concept = session.get(ConceptRecord, cid)
                cname = concept.name if concept else cid
                for p in params:
                    val = f"{p.value} {p.unit}".strip()
                    lines.append(f"- **{cname}**: {p.parameter_name} = {val}")
        lines.append("")

    content = "\n".join(lines)
    index_path = domain_dir / "_index.md"
    index_path.write_text(content, encoding="utf-8")

    logger.info(
        "generate_domain_condensation: wrote %s (%d concepts, %d chars)",
        index_path,
        len(concepts),
        len(content),
    )
    return index_path


def generate_all_domain_condensations(wiki_dir: Path) -> int:
    """Generate condensed _index.md for all discovered domains.

    Queries DomainCluster table for all domains and generates a
    condensation file for each.

    Args:
        wiki_dir: Root wiki directory.

    Returns:
        Number of domain index files generated.
    """
    from sqlmodel import select

    from wikify.store.db import get_session
    from wikify.store.models import DomainCluster

    with get_session() as session:
        clusters: list[DomainCluster] = list(session.exec(select(DomainCluster)).all())

    if not clusters:
        logger.info("generate_all_domain_condensations: no domains found")
        return 0

    count = 0
    for cluster in clusters:
        concept_ids = cluster.parsed_core_concepts + cluster.parsed_bridge_concepts
        if concept_ids:
            generate_domain_condensation(wiki_dir, cluster.label, concept_ids)
            count += 1

    logger.info(
        "generate_all_domain_condensations: generated %d domain indexes",
        count,
    )
    return count


# ---------------------------------------------------------------------------
# Graph metrics helper (used by index generation)
# ---------------------------------------------------------------------------


def _load_graph_metrics() -> dict:
    """Return parsed graph metrics dict, or empty dict on failure."""
    try:
        from wikify.agent.tools import get_graph_metrics

        raw = get_graph_metrics()
        data = json.loads(raw)
        if data.get("error"):
            logger.warning("Graph metrics error: %s", data["error"])
            return {}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load graph metrics for index generation: %s", exc)
        return {}


def _graph_display_names(entries: list[dict]) -> list[str]:
    return [e.get("display_name", e.get("id", "")) for e in entries if e]


# ---------------------------------------------------------------------------
# Hierarchical index: theme level
# ---------------------------------------------------------------------------


def generate_theme_index(
    wiki_dir: Path,
    domain: str,
    theme_slug: str,
    theme_entry,  # SitemapEntry or dict-like with .title, .scope, .key_source_ids
    concept_entries: list,  # list of SitemapEntry or dict-like
) -> Path:
    """Write the per-theme index file.

    Writes: wiki_dir/domains/{domain}/_index_{theme_slug}.md

    Args:
        wiki_dir: Root wiki directory.
        domain: Domain name (e.g. "material_science").
        theme_slug: Slug of the parent theme (used in filename).
        theme_entry: The theme SitemapEntry (must have .title, .scope, .key_source_ids).
        concept_entries: List of concept SitemapEntries belonging to this theme.

    Returns:
        Path to the written file.
    """
    out_path = wiki_dir / "domains" / domain / f"_index_{theme_slug}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve theme attributes (support both object and dict)
    def _get(obj, attr: str, default=""):
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    theme_title = _get(theme_entry, "title", theme_slug)
    theme_scope = _get(theme_entry, "scope", "")
    theme_sources = _get(theme_entry, "key_source_ids", [])

    n_concepts = len(concept_entries)
    all_sources: set[str] = set(theme_sources)
    for ce in concept_entries:
        all_sources.update(_get(ce, "key_source_ids", []))

    # Collect open questions from concept article frontmatter (if articles exist)
    open_questions: list[str] = []
    domain_dir = wiki_dir / "domains" / domain
    for ce in concept_entries:
        slug = _get(ce, "slug", "")
        if not slug:
            continue
        for subdir in ("concepts", "themes", "."):
            art_path = domain_dir / subdir / f"{slug}.md"
            if art_path.exists():
                meta = read_article_frontmatter(art_path)
                oq = meta.get("open_questions", [])
                if isinstance(oq, list):
                    open_questions.extend(str(q) for q in oq)
                elif isinstance(oq, str) and oq:
                    open_questions.append(oq)
                break

    # Graph highlights
    graph_data = _load_graph_metrics()
    hub_names = _graph_display_names(graph_data.get("hub_papers", []))
    bridge_names = _graph_display_names(graph_data.get("bridge_papers", []))
    frontier_names = _graph_display_names(graph_data.get("frontier_papers", []))

    # Filter to sources relevant to this theme
    all_src_lower = {s.lower() for s in all_sources}

    def _relevant(names: list[str]) -> list[str]:
        return [n for n in names if any(n.lower() in s or s in n.lower() for s in all_src_lower)]

    hub_relevant = _relevant(hub_names) or hub_names[:1]
    bridge_relevant = _relevant(bridge_names) or bridge_names[:1]
    frontier_relevant = _relevant(frontier_names) or frontier_names[:1]

    # Concepts table rows
    concept_rows: list[str] = []
    for ce in concept_entries:
        art_title = _get(ce, "title", _get(ce, "slug", "Untitled"))
        art_scope = _get(ce, "scope", "")
        art_depth = _get(ce, "depth", "draft")
        art_sources = _get(ce, "key_source_ids", [])
        n_src = len(art_sources)
        # Count open questions for this concept
        slug = _get(ce, "slug", "")
        n_oq = 0
        for subdir in ("concepts", "themes", "."):
            art_path = domain_dir / subdir / f"{slug}.md"
            if art_path.exists():
                meta = read_article_frontmatter(art_path)
                oq = meta.get("open_questions", [])
                if isinstance(oq, list):
                    n_oq = len(oq)
                break
        concept_rows.append(
            f"| [[{art_title}]] | {art_scope[:60]} | {art_depth} | {n_src} | {n_oq} |"
        )

    lines: list[str] = [
        f"# Theme: {theme_title}",
        f"_{n_concepts} concept articles | {len(all_sources)} sources | Domain: {domain}_",
        "",
        "## Overview",
        theme_scope or f"Theme covering {theme_title}.",
        "",
        "## Concepts",
        "| Article | Scope | Depth | Sources | Open Questions |",
        "|---------|-------|-------|---------|---------------|",
    ]
    lines.extend(concept_rows)

    if open_questions:
        lines += ["", "## Open Questions in This Theme", ""]
        for q in open_questions[:10]:
            lines.append(f"- {q}")

    # Graph Highlights section (only if we have data)
    if hub_relevant or bridge_relevant or frontier_relevant:
        lines += ["", "## Graph Highlights", ""]
        if hub_relevant:
            lines.append(f"Hub: {hub_relevant[0]}")
        if bridge_relevant:
            lines.append(f"Bridge: {bridge_relevant[0]}")
        if frontier_relevant:
            lines.append(f"Frontier: {frontier_relevant[0]}")

    content = "\n".join(lines) + "\n"
    out_path.write_text(content, encoding="utf-8")
    logger.info("Wrote theme index: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Hierarchical index: domain level
# ---------------------------------------------------------------------------


def generate_domain_index(
    wiki_dir: Path,
    domain: str,
    sitemap,  # WikiSitemap
) -> Path:
    """Write the per-domain master index.

    Writes: wiki_dir/domains/{domain}/_index.md

    Args:
        wiki_dir: Root wiki directory.
        domain: Domain name (e.g. "material_science").
        sitemap: WikiSitemap for this domain.

    Returns:
        Path to the written file.
    """
    out_path = wiki_dir / "domains" / domain / "_index.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    domain_title = domain.replace("_", " ").title()

    themes = sitemap.themes() if hasattr(sitemap, "themes") else []
    concepts = sitemap.concepts() if hasattr(sitemap, "concepts") else []

    all_source_ids: set[str] = set()
    for entry in sitemap.entries:
        src = (
            entry.key_source_ids
            if hasattr(entry, "key_source_ids")
            else entry.get("key_source_ids", [])
        )
        all_source_ids.update(src)

    # Count concepts per theme
    theme_concept_count: dict[str, int] = {}
    for ce in concepts:
        ps = ce.parent_slug if hasattr(ce, "parent_slug") else ce.get("parent_slug")
        if ps:
            theme_concept_count[ps] = theme_concept_count.get(ps, 0) + 1

    # Theme table rows
    domain_dir = wiki_dir / "domains" / domain
    theme_rows: list[str] = []
    for te in themes:
        t_title = te.title if hasattr(te, "title") else te.get("title", "")
        t_slug = te.slug if hasattr(te, "slug") else te.get("slug", "")
        t_scope = (te.scope if hasattr(te, "scope") else te.get("scope", ""))[:80]
        n_arts = theme_concept_count.get(t_slug, 0)
        index_link = f"_index_{t_slug}.md"
        theme_rows.append(f"| [{t_title}]({index_link}) | {n_arts} | {t_scope} | -> |")

    # Collect open questions from all article files in this domain directory
    open_questions: list[str] = []
    seen_qs: set[str] = set()
    for md_file in sorted(domain_dir.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        meta = read_article_frontmatter(md_file)
        oq = meta.get("open_questions", [])
        qs: list[str] = []
        if isinstance(oq, list):
            qs = [str(q) for q in oq]
        elif isinstance(oq, str) and oq:
            qs = [oq]
        for q in qs:
            if q not in seen_qs:
                seen_qs.add(q)
                open_questions.append(q)

    # Graph summary
    graph_data = _load_graph_metrics()
    hub_papers = graph_data.get("hub_papers", [])
    bridge_papers = graph_data.get("bridge_papers", [])
    frontier_papers = graph_data.get("frontier_papers", [])

    hub_names = _graph_display_names(hub_papers)
    bridge_names = _graph_display_names(bridge_papers)

    lines: list[str] = [
        f"# {domain_title} Knowledge Base",
        f"_{len(themes)} themes | {len(concepts)} concepts | {len(all_source_ids)} sources_",
        "",
        "## Themes",
        "| Theme | Articles | Scope | Index |",
        "|-------|----------|-------|-------|",
    ]
    lines.extend(theme_rows)

    # Domain graph summary
    lines += ["", "## Domain Graph Summary", ""]
    lines.append(
        f"{len(hub_papers)} hub papers, {len(bridge_papers)} bridge papers, "
        f"{len(frontier_papers)} frontier papers"
    )
    if hub_names:
        lines.append(f"Top hub: {hub_names[0]}")
    if bridge_names:
        lines.append(f"Most connected bridge: {bridge_names[0]}")

    if open_questions:
        lines += ["", "## Open Questions Across Domain", ""]
        for q in open_questions[:5]:
            lines.append(f"- {q}")

    content = "\n".join(lines) + "\n"
    out_path.write_text(content, encoding="utf-8")
    logger.info("Wrote domain index: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Hierarchical index: library catalog (top-level _index.md)
# ---------------------------------------------------------------------------


def generate_library_catalog(
    wiki_dir: Path,
    all_domain_info: list[dict],
) -> Path:
    """Write the top-level library catalog _index.md.

    Args:
        wiki_dir: Root wiki directory.
        all_domain_info: List of dicts with keys:
            - domain: str
            - article_count: int
            - source_count: int
            - last_updated: str (ISO date)
            - themes_summary: str (comma-separated theme names)

    Returns:
        Path to the written file.
    """
    out_path = wiki_dir / "_index.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    now_str = datetime.now(timezone.utc).date().isoformat()
    total_articles = sum(d.get("article_count", 0) for d in all_domain_info)
    total_sources = sum(d.get("source_count", 0) for d in all_domain_info)
    n_domains = len(all_domain_info)

    # Domains table
    domain_rows: list[str] = []
    for info in all_domain_info:
        domain = info.get("domain", "")
        n_arts = info.get("article_count", 0)
        n_srcs = info.get("source_count", 0)
        updated = info.get("last_updated", "")
        domain_title = domain.replace("_", " ").title()
        link = f"domains/{domain}/_index.md"
        domain_rows.append(f"| [{domain_title}]({link}) | {n_arts} | {n_srcs} | {updated} |")

    # Cross-domain connections: scan synthesis articles
    synth_entries: list[str] = []
    synth_dirs = [wiki_dir / "syntheses"] + [
        wiki_dir / "domains" / info.get("domain", "") / "syntheses" for info in all_domain_info
    ]
    seen_synths: set[str] = set()
    for synth_dir in synth_dirs:
        if not synth_dir.exists():
            continue
        for md_file in sorted(synth_dir.glob("*.md")):
            if md_file.name.startswith("_") or md_file.stem in seen_synths:
                continue
            seen_synths.add(md_file.stem)
            meta = read_article_frontmatter(md_file)
            title = str(meta.get("title") or md_file.stem)
            synth_entries.append(f"- [[{title}]]")

    # Unanswered questions from _unanswered.jsonl
    unanswered: list[dict] = []
    unanswered_path = wiki_dir / "_unanswered.jsonl"
    if unanswered_path.exists():
        try:
            lines_raw = unanswered_path.read_text(encoding="utf-8").splitlines()
            for line in lines_raw:
                line = line.strip()
                if not line:
                    continue
                try:
                    unanswered.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed _unanswered.jsonl line: %s", exc)
        except OSError as exc:
            logger.warning("Could not read _unanswered.jsonl: %s", exc)
    unanswered_last10 = unanswered[-10:]

    # Recent additions: scan all article files across domains, sort by updated
    all_articles: list[dict] = []
    for md_file in sorted(wiki_dir.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        meta = read_article_frontmatter(md_file)
        updated_raw = str(meta.get("updated") or meta.get("updated_at") or "")
        title = str(meta.get("title") or md_file.stem)
        if updated_raw:
            all_articles.append({"title": title, "updated": updated_raw, "path": str(md_file)})

    recent = sorted(all_articles, key=lambda a: a["updated"], reverse=True)[:5]

    # Build output
    output_lines: list[str] = [
        "# Personal Knowledge Base",
        (
            f"_{n_domains} domains | {total_articles} articles"
            f" | {total_sources} sources | Updated {now_str}_"
        ),
        "",
        "## Domains",
        "| Domain | Articles | Sources | Last Updated |",
        "|--------|----------|---------|-------------|",
    ]
    output_lines.extend(domain_rows)

    if synth_entries:
        output_lines += ["", "## Cross-Domain Connections", ""]
        output_lines.extend(synth_entries[:10])

    output_lines += ["", "## Unanswered Questions", ""]
    output_lines.append(
        "_Appended by wiki query when the wiki could not fully answer -- these drive wiki expand_"
    )
    if unanswered_last10:
        output_lines.append("")
        for item in unanswered_last10:
            q = item.get("question", "")
            domain = item.get("domain", "")
            date = item.get("date", "")
            tag = f" [{domain}]" if domain else ""
            date_tag = f" ({date})" if date else ""
            output_lines.append(f"- {q}{tag}{date_tag}")

    if recent:
        output_lines += ["", "## Recent Additions", ""]
        for art in recent:
            output_lines.append(f"- {art['title']} -- {art['updated']}")

    content = "\n".join(output_lines) + "\n"
    out_path.write_text(content, encoding="utf-8")
    logger.info("Wrote library catalog: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Unanswered question log
# ---------------------------------------------------------------------------


def append_unanswered_question(
    wiki_dir: Path,
    question: str,
    domain: str,
) -> None:
    """Append one JSON line to wiki_dir/_unanswered.jsonl.

    Creates the file if it does not exist.

    Args:
        wiki_dir: Root wiki directory.
        question: The question text that could not be answered.
        domain: The domain context for the question.
    """
    unanswered_path = wiki_dir / "_unanswered.jsonl"
    unanswered_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "question": question,
        "domain": domain,
        "date": datetime.now(timezone.utc).date().isoformat(),
    }
    line = json.dumps(record, ensure_ascii=False)

    with unanswered_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    logger.info("Appended unanswered question to %s", unanswered_path)


# ---------------------------------------------------------------------------
# Legacy index generation (backward compatibility)
# ---------------------------------------------------------------------------


def generate_wiki_index(wiki_dir: Path) -> str:
    """Scan wiki directory and generate _index.md content.

    Groups articles by their frontmatter ``category`` field (theme, concept,
    synthesis, query).  Falls back to the subdirectory name when the field is
    absent.  Produces a structured Markdown index and writes it to
    ``wiki_dir/_index.md``.

    This is the backward-compatibility wrapper for existing CLI commands.
    It calls generate_domain_index (for domain="general") then
    generate_library_catalog with that single domain.

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
            scope_part = f" -- {e['scope']}" if e["scope"] else ""
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
            lines.append(f"- {e['title']} -- {e['updated']}")

    content = "\n".join(lines) + "\n"
    index_path = wiki_dir / "_index.md"
    index_path.write_text(content, encoding="utf-8")
    return content
