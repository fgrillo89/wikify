"""Three-tier wiki maintenance: additive, revisionary, and structural.

Tier 1 (additive): new source adds evidence -> append citation + sentence.
Tier 2 (revisionary): new source contradicts claim -> flag warning, keep both.
Tier 3 (structural): article scope wrong -> split/merge/deprecate candidates.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from scholarforge.store.embeddings import _store

logger = logging.getLogger(__name__)


# ── Data contracts ────────────────────────────────────────────────────────────


@dataclass
class StructuralReport:
    """Result of a structural audit of the wiki."""

    domain: str
    split_candidates: list[str] = field(default_factory=list)
    merge_candidates: list[tuple[str, str]] = field(default_factory=list)
    deprecation_candidates: list[str] = field(default_factory=list)
    orphan_sources: list[str] = field(default_factory=list)
    contradiction_flags: list[str] = field(default_factory=list)
    graph_drift: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_established_section(body: str) -> str:
    """Extract the 'established' zone text from an article body.

    Looks for headings matching: What Is Known, Practitioner Consensus,
    Established (any case). Returns text between that heading and the next ##.
    Falls back to the first 500 chars of body if no heading found.
    """
    pattern = re.compile(
        r"##\s+(?:What Is Known|Practitioner Consensus|Established(?:\s+\w+)?)"
        r"\s*\n(.*?)(?=\n##\s|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(body)
    if match:
        return match.group(1).strip()
    # Fallback: first 500 characters (best effort)
    return body[:500]


def _strip_frontmatter(text: str) -> str:
    """Strip YAML frontmatter (---...---) and return just the body."""
    parts = text.split("---\n", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text.strip()


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two 1-D vectors."""
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ── Public API ────────────────────────────────────────────────────────────────


def detect_contradiction(existing_body: str, new_extraction: str) -> bool:
    """Cheap embedding-based contradiction check.

    Extracts the established section from existing_body, encodes both texts
    using the ONNX embedding model, and returns True if cosine similarity
    is below 0.30 (very dissimilar texts on the same topic = possible
    contradiction) and new_extraction is longer than 50 characters.

    Args:
        existing_body: Full article body (frontmatter already stripped).
        new_extraction: Haiku-extracted sentence(s) for the new source.

    Returns:
        True if a contradiction is likely; False otherwise.
    """
    if len(new_extraction) <= 50:
        return False

    established = _extract_established_section(existing_body)
    if not established:
        return False

    embeddings = _store.model.encode([established, new_extraction])
    e1 = np.array(embeddings[0])
    e2 = np.array(embeddings[1])
    similarity = _cosine_similarity(e1, e2)

    logger.debug("detect_contradiction: cosine_similarity=%.4f", similarity)
    return similarity < 0.30


def additive_update(
    article_path: Path,
    new_extractions: list,
    persona: str,
    model: str | None = None,
) -> str:
    """Extend an existing article with new supporting evidence.

    Called when new sources confirm or extend existing claims without
    contradicting them. Returns the updated article body (no frontmatter).

    Args:
        article_path: Path to the existing article .md file.
        new_extractions: list[SourceExtraction] with is_relevant=True items.
        persona: Domain persona text (prepended to system prompt).
        model: litellm model string.

    Returns:
        Updated article body markdown (no frontmatter).
    """
    from scholarforge.llm.client import complete
    from scholarforge.wiki.mapreduce import _build_evidence_block

    text = article_path.read_text(encoding="utf-8", errors="replace")
    body = _strip_frontmatter(text)

    relevant = [e for e in new_extractions if e.is_relevant]
    evidence_block = _build_evidence_block(relevant)

    system_prompt = (
        f"{persona}\n\n"
        "You are extending an existing wiki article with new evidence.\n"
        "Add new findings as additional sentences or citations within the appropriate sections.\n"
        "Do NOT restructure the article. Do NOT remove existing content.\n"
        "Return the complete updated article body (no frontmatter)."
    )

    user_msg = (
        f"Existing article:\n\n{body}\n\n"
        "New evidence to incorporate:\n\n"
        f"--- EVIDENCE ---\n{evidence_block}\n--- END EVIDENCE ---\n\n"
        "Return the complete updated article body (no frontmatter). "
        "Do NOT restructure the article. Do NOT remove existing content."
    )

    updated_body = complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.2,
        max_tokens=3000,
        use_cache=False,
    )

    logger.info("additive_update: %s -> %d chars", article_path.name, len(updated_body))
    return updated_body


def revisionary_update(
    article_path: Path,
    new_extractions: list,
    persona: str,
    model: str | None = None,
) -> str:
    """Revise an article where new evidence contradicts an existing claim.

    Instructs the LLM to mark the contradicted claim with a warning flag,
    present both positions with citations, and move the claim to the
    contested zone. Does NOT resolve the contradiction.

    Args:
        article_path: Path to the existing article .md file.
        new_extractions: list[SourceExtraction] with contradicting evidence.
        persona: Domain persona text (prepended to system prompt).
        model: litellm model string.

    Returns:
        Updated article body markdown (no frontmatter).
    """
    from scholarforge.llm.client import complete
    from scholarforge.wiki.mapreduce import _build_evidence_block

    text = article_path.read_text(encoding="utf-8", errors="replace")
    body = _strip_frontmatter(text)

    relevant = [e for e in new_extractions if e.is_relevant]
    evidence_block = _build_evidence_block(relevant)

    system_prompt = (
        f"{persona}\n\n"
        "You are revising a wiki article where new evidence contradicts an existing claim.\n\n"
        "Rules:\n"
        "- Find the contradicted claim in the article\n"
        "- Mark it with a warning immediately after the original citation: "
        '"...original claim [REF:X] WARNING"\n'
        "- Add the contradicting evidence in the same sentence or the next:\n"
        '  "However, [REF:Y] reports the opposite: [contradicting claim]."\n'
        '- Move the claim from the "established" zone to the "contested" zone'
        " if not already there\n"
        "- Do NOT resolve the contradiction -- surface it for the human reader\n"
        "- Return the complete updated article body (no frontmatter)\n"
        "\nIMPORTANT: Use the text WARNING (not a Unicode symbol) to flag contradicted claims."
    )

    user_msg = (
        f"Existing article:\n\n{body}\n\n"
        "Contradicting evidence:\n\n"
        f"--- EVIDENCE ---\n{evidence_block}\n--- END EVIDENCE ---\n\n"
        "Return the complete updated article body (no frontmatter) with the contradiction flagged."
    )

    updated_body = complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.2,
        max_tokens=3000,
        use_cache=False,
    )

    logger.info("revisionary_update: %s -> %d chars", article_path.name, len(updated_body))
    return updated_body


def structural_audit(
    wiki_dir: Path,
    domain: str,
    model: str | None = None,
) -> StructuralReport:
    """Identify structural issues in the wiki for the given domain.

    Checks:
    - Split candidates: articles with >15 SourceCoverage rows
    - Merge candidates: article pairs with >80% overlapping source_ids
    - Deprecation candidates: articles with 0 SourceCoverage AND <3 source_ids
    - Orphan sources: Paper rows with zero SourceCoverage rows
    - Contradiction flags: articles containing a warning marker in body
    - Graph drift: hub/bridge papers not referenced in any wiki article

    Args:
        wiki_dir: Root of the wiki directory.
        domain: Domain name to filter by (empty string = all domains).
        model: Unused; reserved for future LLM-assisted heuristics.

    Returns:
        StructuralReport with populated candidate lists.
    """
    from sqlmodel import func, select

    from scholarforge.agent.tools import get_graph_metrics
    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper, SourceCoverage, WikiArticle
    from scholarforge.wiki.mapreduce import _parse_graph_metrics

    report = StructuralReport(domain=domain)

    # ── Load wiki articles (filtered by domain if provided) ───────────────────
    with get_session() as session:
        stmt = select(WikiArticle)
        if domain:
            stmt = stmt.where(WikiArticle.domain == domain)
        all_articles: list[WikiArticle] = list(session.exec(stmt).all())

        article_slugs = [a.id for a in all_articles]

        # ── Split candidates: >15 SourceCoverage rows per slug ────────────────
        coverage_count_rows = session.exec(
            select(SourceCoverage.article_slug, func.count(SourceCoverage.id).label("cnt"))
            .where(SourceCoverage.article_slug.in_(article_slugs))
            .group_by(SourceCoverage.article_slug)
        ).all()
        coverage_counts: dict[str, int] = {row[0]: row[1] for row in coverage_count_rows}
        report.split_candidates = [slug for slug, cnt in coverage_counts.items() if cnt > 15]

        # ── Merge candidates: >80% Jaccard overlap of source_ids ─────────────
        slug_to_sources: dict[str, set[str]] = {}
        for art in all_articles:
            try:
                ids = json.loads(art.source_ids or "[]")
            except (json.JSONDecodeError, ValueError):
                ids = []
            slug_to_sources[art.id] = set(ids)

        merge_pairs: list[tuple[str, str]] = []
        slugs_list = list(slug_to_sources.keys())
        for i in range(len(slugs_list)):
            for j in range(i + 1, len(slugs_list)):
                s1 = slug_to_sources[slugs_list[i]]
                s2 = slug_to_sources[slugs_list[j]]
                if not s1 or not s2:
                    continue
                intersection = len(s1 & s2)
                union = len(s1 | s2)
                jaccard = intersection / union if union > 0 else 0.0
                if jaccard > 0.80:
                    merge_pairs.append((slugs_list[i], slugs_list[j]))
        report.merge_candidates = merge_pairs

        # ── Deprecation candidates: 0 coverage AND <3 source_ids ─────────────
        covered_slugs = set(coverage_counts.keys())
        report.deprecation_candidates = [
            art.id
            for art in all_articles
            if art.id not in covered_slugs and len(slug_to_sources.get(art.id, set())) < 3
        ]

        # ── Orphan sources: Paper rows with zero SourceCoverage rows ─────────
        covered_source_ids_rows = session.exec(select(SourceCoverage.source_id).distinct()).all()
        covered_source_ids: set[str] = set(covered_source_ids_rows)

        paper_stmt = select(Paper)
        all_papers = list(session.exec(paper_stmt).all())
        report.orphan_sources = [p.id for p in all_papers if p.id not in covered_source_ids]

    # ── Contradiction flags: scan article files for warning markers ───────────
    warning_pattern = re.compile(r"WARNING|\u26a0")
    for art in all_articles:
        art_path = Path(art.file_path)
        if not art_path.is_absolute():
            art_path = Path("data") / art.file_path
        if not art_path.exists():
            continue
        try:
            body = art_path.read_text(encoding="utf-8", errors="replace")
            if warning_pattern.search(body):
                report.contradiction_flags.append(art.id)
        except OSError as exc:
            logger.warning("structural_audit: could not read %s: %s", art_path, exc)

    # ── Graph drift: hub/bridge papers not in any article source_ids ─────────
    try:
        graph_raw = get_graph_metrics()
        graph_lookup = _parse_graph_metrics(graph_raw)

        all_source_ids: set[str] = set()
        for src_set in slug_to_sources.values():
            all_source_ids.update(src_set)

        for pid, info in graph_lookup.items():
            if info.get("role") in ("hub", "bridge") and pid not in all_source_ids:
                display = info.get("display_name") or pid
                report.graph_drift.append(display)
    except Exception as exc:
        logger.warning("structural_audit: graph drift check failed: %s", exc)

    logger.info(
        "structural_audit(domain=%r): split=%d, merge=%d, deprecate=%d, "
        "orphan=%d, contradiction=%d, drift=%d",
        domain,
        len(report.split_candidates),
        len(report.merge_candidates),
        len(report.deprecation_candidates),
        len(report.orphan_sources),
        len(report.contradiction_flags),
        len(report.graph_drift),
    )
    return report
