"""Wiki article authoring agent.

Provides direct-LLM functions (not agent loops) for writing and updating
wiki articles from corpus evidence. Uses the same LLM client as pi_review.py.

Also provides sitemap-driven bulk building:
  build_article_from_entry -- write one article from a SitemapEntry
  build_wiki_from_sitemap  -- write all articles in a WikiSitemap
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ARTICLE_SYSTEM_PROMPT = """\
You are a technical knowledge-base writer. Your task is to write a focused,
well-structured wiki article on a given concept, based on evidence from a
research corpus.

Guidelines:
- 400-800 words for stubs/drafts; 600-1200 for full articles.
- Write in clear, declarative prose. One concept per sentence.
- Use inline citations in the format [REF:paper_id] immediately after the
  claim they support. Never cluster citations.
- Structure with ## headings: Overview, Key Properties/Methods, Applications,
  Open Questions (or similar — adapt to the topic).
- Do not include a title heading (the frontmatter title field handles that).
- Do not use em-dashes as separators. Do not start sentences with "However," or
  "Moreover," as filler transitions.
- Ground every non-trivial claim in the provided evidence. If a claim is not
  supported, omit it or explicitly flag it as speculative.
"""

_UPDATE_SYSTEM_PROMPT = """\
You are updating an existing wiki article with new evidence from the research
corpus. Revise the article to incorporate new findings, correct outdated
claims, and add new citations where appropriate.

Rules:
- Return the complete revised article body (no frontmatter).
- Mark revised passages clearly with new [REF:...] citations.
- Remove or qualify claims that contradict newer evidence.
- Keep the same section structure unless a new section is clearly needed.
- Do not change the word count by more than 30%.
"""


def build_wiki_article(
    title: str,
    topic_query: str,
    status: str = "draft",
    model: str | None = None,
    top_k: int = 8,
) -> tuple[str, list[str]]:
    """Use the LLM to write a wiki article on `title`.

    Steps:
      1. search_papers(topic_query, top_k) to get relevant sources.
      2. For top 3 sources: read_paper_digest to get evidence.
      3. Call LLM to write a focused concept article (400-800 words)
         with inline [REF:...] citations.

    Args:
        title: Article title (also used as concept for article writing).
        topic_query: Query string for corpus search.
        status: "stub", "draft", or "full" — controls target length hint.
        model: litellm model string. Defaults to settings.llm_model.
        top_k: Number of papers to retrieve for evidence.

    Returns:
        (article_markdown_content, list_of_source_paper_ids)
    """
    from scholarforge.agent.tools import read_paper_digest, search_papers
    from scholarforge.llm.client import complete

    # Step 1: search for relevant papers
    search_result = search_papers(topic_query, top_k=top_k, reason=f"wiki article: {title}")

    # Extract paper IDs from the search result (format: "Paper: <id> | ...")
    import re

    source_ids: list[str] = re.findall(r"Paper:\s*([a-f0-9]{8,})", search_result)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_ids: list[str] = []
    for pid in source_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    # Step 2: deep-read top 3 sources
    digests: list[str] = []
    for paper_id in unique_ids[:3]:
        digest = read_paper_digest(paper_id[:16], reason=f"evidence for wiki: {title}")
        if digest:
            digests.append(digest)

    # Build evidence block
    evidence = "\n\n---\n\n".join(digests) if digests else search_result

    length_hint = {
        "stub": "200-300 words",
        "draft": "400-600 words",
        "full": "600-1200 words",
    }.get(status, "400-800 words")

    user_msg = (
        f"Write a wiki article titled '{title}'.\n"
        f"Target length: {length_hint}.\n\n"
        f"Evidence from the corpus:\n\n{evidence}"
    )

    content = complete(
        messages=[
            {"role": "system", "content": _ARTICLE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        use_cache=False,
    )

    return content, unique_ids


def update_wiki_article(
    existing_content: str,
    new_source_digests: list[str],
    model: str | None = None,
) -> str:
    """Update an existing article with new evidence.

    The LLM receives the current article and new digests, and returns a
    revised version incorporating new findings.

    Args:
        existing_content: Full current article body (without frontmatter).
        new_source_digests: List of read_paper_digest results for new sources.
        model: litellm model string. Defaults to settings.llm_model.

    Returns:
        Revised article body (without frontmatter).
    """
    from scholarforge.llm.client import complete

    new_evidence = "\n\n---\n\n".join(new_source_digests) if new_source_digests else ""

    user_msg = (
        "Here is the current wiki article:\n\n"
        f"{existing_content}\n\n"
        "---\n\n"
        "New evidence to incorporate:\n\n"
        f"{new_evidence}"
    )

    return complete(
        messages=[
            {"role": "system", "content": _UPDATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.2,
        max_tokens=2500,
        use_cache=False,
    )


# ---------------------------------------------------------------------------
# Sitemap-driven article building
# ---------------------------------------------------------------------------

# Doc types that are short -- deep_read returns full content quickly
_SHORT_DOC_TYPES = {"web_article", "markdown", "note", "other"}

# Academic long-form types -- use read_paper_digest (progressive disclosure)
_ACADEMIC_DOC_TYPES = {"paper", "report", "proposal", "thesis"}


def _fetch_evidence_for_entry(
    entry: "SitemapEntry",
    model: str | None = None,
) -> tuple[str, list[str]]:
    """Fetch evidence appropriate to source types in this entry.

    Reading strategy per source type:
    - paper/report/proposal/thesis + depth="full": digest top 3, then read one section
    - paper/report/proposal/thesis + depth in {stub, draft}: digest only
    - web_article/markdown/note: deep_read (full content, it's short)
    - anything else: use whatever the search result returned

    Args:
        entry: The SitemapEntry being built.
        model: unused here but kept for future extension.

    Returns:
        (evidence_text, list_of_actual_source_ids)
    """
    import re

    from scholarforge.agent.tools import deep_read, read_paper_digest, read_section, search_papers

    query = f"{entry.scope} {entry.title}"
    search_result = search_papers(query, top_k=10, reason=f"wiki evidence: {entry.title}")

    # Extract paper IDs and their doc_types from the search result
    # search_papers returns lines like: "Paper: <id> | doc_type: <type> | ..."
    raw_ids: list[str] = re.findall(r"Paper:\s*([a-f0-9]{8,})", search_result)
    raw_doc_types: list[str] = re.findall(r"doc_type:\s*(\w+)", search_result)

    # Build ordered list of (id, doc_type) pairs
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, pid in enumerate(raw_ids):
        if pid not in seen:
            seen.add(pid)
            doc_type = raw_doc_types[i] if i < len(raw_doc_types) else "other"
            pairs.append((pid, doc_type))

    evidence_parts: list[str] = []
    actual_source_ids: list[str] = []

    for rank, (pid, doc_type) in enumerate(pairs[:10]):
        short_id = pid[:16]
        reason = f"wiki evidence for: {entry.title}"

        if doc_type in _SHORT_DOC_TYPES:
            text = deep_read(short_id, reason=reason)
            if text:
                evidence_parts.append(text)
                actual_source_ids.append(pid)

        elif doc_type in _ACADEMIC_DOC_TYPES:
            if entry.depth == "full" and rank < 3:
                # Digest for top 3 academic sources
                digest = read_paper_digest(short_id, reason=reason)
                if digest:
                    evidence_parts.append(digest)
                    actual_source_ids.append(pid)
                # For the very top source, also read the most relevant section
                if rank == 0 and entry.depth == "full":
                    section_text = read_section(
                        short_id,
                        section_query=entry.scope,
                        reason=f"targeted section for: {entry.title}",
                    )
                    if section_text and section_text not in digest:
                        evidence_parts.append(f"[Section detail]\n{section_text}")
            else:
                # Stub or draft: digest only
                digest = read_paper_digest(short_id, reason=reason)
                if digest:
                    evidence_parts.append(digest)
                    actual_source_ids.append(pid)

        else:
            # image, repo_readme, or unknown: use whatever is in the search result
            # We don't have a separate summary to add, so skip -- search_result
            # already contains the title/summary from the search index.
            actual_source_ids.append(pid)

    if not evidence_parts:
        # Fallback: use the raw search result as evidence
        evidence_parts.append(search_result)

    return "\n\n---\n\n".join(evidence_parts), actual_source_ids


def build_article_from_entry(
    entry: "SitemapEntry",
    wiki_dir: Path,
    model: str | None = None,
) -> tuple[str, list[str]]:
    """Write one wiki article from a SitemapEntry.

    Fetches evidence using source-type-aware reading strategy, then calls
    the LLM to author the article body with inline [REF:...] citations.

    Args:
        entry: The planned article to write.
        wiki_dir: Root wiki directory (used to locate parent theme for context).
        model: litellm model string. Uses settings.llm_model if None.

    Returns:
        (article_markdown_body, list_of_actual_source_ids_used)
    """
    from scholarforge.llm.client import complete
    from scholarforge.wiki.sitemap import WikiSitemap

    # Build system prompt -- inject parent theme context when available
    system_prompt = _ARTICLE_SYSTEM_PROMPT
    if entry.parent_slug:
        # Try to resolve parent title from the sitemap on disk
        sitemap = WikiSitemap.load(wiki_dir)
        parent_title = entry.parent_slug
        if sitemap:
            slug_map = sitemap.by_slug()
            parent_entry = slug_map.get(entry.parent_slug)
            if parent_entry:
                parent_title = parent_entry.title

        system_prompt = (
            _ARTICLE_SYSTEM_PROMPT + f"\n\nThis article is part of the '{parent_title}' theme. "
            f"Stay within the scope: {entry.scope}."
        )

    # Fetch evidence
    evidence, actual_source_ids = _fetch_evidence_for_entry(entry, model=model)

    length_hint = {
        "stub": "200-300 words",
        "draft": "400-600 words",
        "full": "600-1200 words",
    }.get(entry.depth, "400-800 words")

    user_msg = (
        f"Write a wiki article titled '{entry.title}'.\n"
        f"Scope: {entry.scope}\n"
        f"Target length: {length_hint}.\n\n"
        f"Evidence from the corpus:\n\n{evidence}"
    )

    content = complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        use_cache=False,
    )

    return content, actual_source_ids


def build_wiki_from_sitemap(
    sitemap: "WikiSitemap",
    wiki_dir: Path,
    model: str | None = None,
    resume: bool = True,
) -> list[Path]:
    """Write all articles in the sitemap to disk and update the DB.

    Writes themes first (ordered_for_writing), then concepts. If resume=True,
    skips entries whose output file already exists and is non-empty.

    Args:
        sitemap: The WikiSitemap to execute.
        wiki_dir: Root wiki directory (e.g. Path("data/wiki")).
        model: litellm model string. Uses settings.llm_model if None.
        resume: If True, skip entries whose file already exists and is non-empty.

    Returns:
        List of Path objects for the files that were written (may be empty if all
        articles were already present and resume=True).
    """
    from datetime import datetime, timezone

    from sqlmodel import Session, select

    from scholarforge.store.db import get_engine
    from scholarforge.store.models import WikiArticle
    from scholarforge.wiki.builder import article_path, write_article

    ordered = sitemap.ordered_for_writing()
    total = len(ordered)
    written_paths: list[Path] = []

    engine = get_engine()

    for i, entry in enumerate(ordered, start=1):
        # Map category to wiki directory name
        category_dir = {
            "theme": "concepts",
            "concept": "concepts",
            "synthesis": "syntheses",
            "query": "queries",
        }.get(entry.category, "concepts")

        out_path = article_path(wiki_dir, category_dir, entry.slug)

        if resume and out_path.exists() and out_path.stat().st_size > 0:
            logger.info(
                "Skipping [%d/%d] %s (%s, %s) -- file exists",
                i,
                total,
                entry.title,
                entry.category,
                entry.depth,
            )
            continue

        logger.info(
            "Writing [%d/%d] %s (%s, %s)",
            i,
            total,
            entry.title,
            entry.category,
            entry.depth,
        )

        article_body, actual_source_ids = build_article_from_entry(
            entry=entry,
            wiki_dir=wiki_dir,
            model=model,
        )

        write_article(
            path=out_path,
            title=entry.title,
            content=article_body,
            sources=actual_source_ids,
            topics=[entry.slug] + entry.related_slugs,
            status=entry.depth,
            model=model or "",
        )
        written_paths.append(out_path)

        # Create or update the WikiArticle DB row
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            existing = session.exec(select(WikiArticle).where(WikiArticle.id == entry.slug)).first()

            if existing is None:
                row = WikiArticle(
                    id=entry.slug,
                    title=entry.title,
                    status=entry.depth,
                    file_path=str(out_path.relative_to(wiki_dir.parent)),
                    source_ids=json.dumps(actual_source_ids),
                    topic_keys=json.dumps([entry.slug]),
                    created_at=now,
                    updated_at=now,
                    model=model or "",
                    needs_update=False,
                )
                session.add(row)
            else:
                existing.status = entry.depth
                existing.source_ids = json.dumps(actual_source_ids)
                existing.topic_keys = json.dumps([entry.slug])
                existing.updated_at = now
                existing.model = model or ""
                existing.needs_update = False
                session.add(existing)

            session.commit()

    logger.info(
        "build_wiki_from_sitemap complete: %d/%d articles written",
        len(written_paths),
        total,
    )
    return written_paths


# ---------------------------------------------------------------------------
# Type alias imports for annotations (avoids circular at module import time)
# ---------------------------------------------------------------------------

from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from scholarforge.wiki.sitemap import SitemapEntry, WikiSitemap
