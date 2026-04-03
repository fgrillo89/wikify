"""Map-reduce corpus coverage for wiki article generation.

MAP phase (haiku): for each candidate source, extract relevant claims
for a given topic + scope query.

REDUCE phase (settings model): synthesise extracted evidence into a
structured wiki article body using the domain persona as system context.

Coverage recording: write SourceCoverage rows after each successful reduce.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from wikify.agent.tools import get_graph_metrics, read_paper_digest, search_papers
from wikify.llm.client import complete
from wikify.store.db import get_session
from wikify.store.models import Paper, SourceCoverage

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAP_SIMILARITY_THRESHOLD = 0.35  # cosine distance threshold (ChromaDB uses cosine distance)
MAP_MAX_SOURCES = 60  # max sources to map per article
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Zone labels by domain register
ZONE_LABELS: dict[str, tuple[str, str, str]] = {
    "academic": ("What Is Known", "Where the Field Disagrees", "Unresolved Questions"),
    "practice": ("Practitioner Consensus", "Ongoing Debates", "What Depends on Context"),
    "mixed": ("Established", "Points of Tension", "Open Territory"),
    "design": ("Established Principles", "Aesthetic Debates", "Context-Dependent"),
}


# ── Data contract ─────────────────────────────────────────────────────────────


@dataclass
class SourceExtraction:
    """Result of the haiku map call for a single source."""

    source_id: str
    display_name: str
    doc_type: str
    graph_role: str  # "hub" | "bridge" | "frontier" | "standard"
    pagerank_score: float
    extraction: str  # haiku output: 1-3 sentences or "NO"
    is_relevant: bool
    key_source_ids: list[str] = field(default_factory=list)  # extra context from entry


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_graph_metrics(raw: str) -> dict[str, dict]:
    """Parse the JSON string returned by get_graph_metrics().

    Returns a dict mapping paper_id -> {role, pagerank, betweenness}.
    Falls back to empty dict on any parse error.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse graph metrics JSON")
        return {}

    if "error" in data:
        logger.warning("get_graph_metrics returned error: %s", data["error"])
        return {}

    lookup: dict[str, dict] = {}

    for entry in data.get("hub_papers", []):
        pid = entry.get("id", "")
        if pid:
            lookup[pid] = {
                "role": "hub",
                "pagerank": entry.get("pagerank", 0.0),
                "betweenness": 0.0,
                "display_name": entry.get("display_name", ""),
            }

    for entry in data.get("bridge_papers", []):
        pid = entry.get("id", "")
        if pid:
            lookup[pid] = {
                "role": "bridge",
                "pagerank": 0.0,
                "betweenness": entry.get("betweenness", 0.0),
                "display_name": entry.get("display_name", ""),
            }

    for entry in data.get("frontier_papers", []):
        pid = entry.get("id", "")
        if pid and pid not in lookup:
            lookup[pid] = {
                "role": "frontier",
                "pagerank": 0.0,
                "betweenness": 0.0,
                "display_name": entry.get("display_name", ""),
            }

    # full_ranking fills in pagerank + role for standard papers
    for entry in data.get("full_ranking", []):
        pid = entry.get("id", "")
        if pid and pid not in lookup:
            lookup[pid] = {
                "role": entry.get("role", "standard"),
                "pagerank": entry.get("pagerank", 0.0),
                "betweenness": entry.get("betweenness", 0.0),
                "display_name": entry.get("display_name", ""),
            }

    return lookup


def _extract_paper_ids_from_search(search_result: str) -> list[str]:
    """Extract ordered, deduplicated paper IDs from a search_papers() result."""
    import re

    raw_ids: list[str] = re.findall(r"Paper:\s*([a-f0-9]{8,})", search_result)
    seen: set[str] = set()
    unique: list[str] = []
    for pid in raw_ids:
        if pid not in seen:
            seen.add(pid)
            unique.append(pid)
    return unique


def _determine_register(extractions: list[SourceExtraction]) -> str:
    """Determine the domain register from the doc_type mix of extractions."""
    relevant = [e for e in extractions if e.is_relevant]
    if not relevant:
        return "mixed"

    total = len(relevant)
    academic_types = {"paper", "report", "thesis", "proposal"}
    practice_types = {"web_article", "markdown", "note"}

    academic_count = sum(1 for e in relevant if e.doc_type in academic_types)
    practice_count = sum(1 for e in relevant if e.doc_type in practice_types)

    if academic_count / total > 0.6:
        return "academic"
    if practice_count / total > 0.6:
        return "practice"
    return "mixed"


def _build_evidence_block(extractions: list[SourceExtraction]) -> str:
    """Build the EVIDENCE block for the reduce prompt."""
    role_prefix = {
        "hub": "[HUB]",
        "bridge": "[BRIDGE]",
        "frontier": "[FRONTIER]",
        "standard": "[STANDARD]",
    }
    lines: list[str] = []
    for ext in extractions:
        if not ext.is_relevant:
            continue
        prefix = role_prefix.get(ext.graph_role, "[STANDARD]")
        lines.append(f"{prefix} {ext.display_name} ({ext.doc_type})")
        lines.append(ext.extraction)
        lines.append("")
    return "\n".join(lines)


def _length_hint(status: str) -> str:
    return {
        "stub": "200-300 words",
        "draft": "400-600 words",
        "full": "600-1200 words",
    }.get(status, "400-800 words")


# ── Public API ────────────────────────────────────────────────────────────────


def map_chunks_to_topic(
    topic_query: str,
    scope: str,
    domain: str = "",
    model: str | None = None,
    key_source_ids: list[str] | None = None,
) -> list[SourceExtraction]:
    """Map all candidate corpus sources to the given topic via haiku extraction.

    Steps:
    1. Graph enrichment: get hub/bridge/frontier paper roles.
    2. Pre-filter: search_papers() for top MAP_MAX_SOURCES candidates.
       Always include hub and bridge papers (regardless of similarity).
    3. Haiku map: for each candidate, call haiku to extract relevant claims.
    4. Return list of SourceExtraction objects.

    Args:
        topic_query: Natural language topic for the article being written.
        scope: One-sentence description of the article scope.
        domain: Domain name (for logging only).
        model: Model override for map calls. Defaults to HAIKU_MODEL.
        key_source_ids: Optional list of paper IDs from the sitemap entry to
            always include in the candidate set.

    Returns:
        List of SourceExtraction (both relevant and irrelevant) sorted so
        relevant ones come first, hubs before bridges before frontier.
    """
    from sqlmodel import select

    map_model = model or HAIKU_MODEL

    # ── Step 1: Graph enrichment ──────────────────────────────────────────────
    graph_raw = get_graph_metrics()
    graph_lookup = _parse_graph_metrics(graph_raw)

    hub_and_bridge_ids = {
        pid for pid, info in graph_lookup.items() if info["role"] in ("hub", "bridge")
    }

    # ── Step 2: Pre-filter via search_papers ──────────────────────────────────
    search_result = search_papers(
        topic_query,
        top_k=MAP_MAX_SOURCES,
        reason=f"map-reduce: {topic_query}",
    )
    candidate_ids = _extract_paper_ids_from_search(search_result)

    # Always include hub/bridge papers and key_source_ids from entry
    forced_ids = hub_and_bridge_ids.copy()
    if key_source_ids:
        forced_ids.update(key_source_ids)

    # Merge: candidates first, then any missing forced ones
    candidate_set: list[str] = list(candidate_ids)
    seen_candidates = set(candidate_ids)
    for pid in forced_ids:
        if pid not in seen_candidates:
            candidate_set.append(pid)
            seen_candidates.add(pid)

    # Limit to MAP_MAX_SOURCES total
    candidate_set = candidate_set[:MAP_MAX_SOURCES]

    # Load paper metadata for all candidates
    with get_session() as session:
        all_papers_list = session.exec(select(Paper)).all()
    paper_by_id: dict[str, Paper] = {p.id: p for p in all_papers_list}

    # ── Step 3: Haiku map ─────────────────────────────────────────────────────
    extractions: list[SourceExtraction] = []

    for pid in candidate_set:
        paper = paper_by_id.get(pid)
        if paper is None:
            logger.debug("map_chunks_to_topic: paper %s not found in DB, skipping", pid)
            continue

        # Get digest text
        digest_text = read_paper_digest(
            pid[:16],
            max_chars=800,
            reason=f"map for wiki: {topic_query}",
        )

        # Build haiku prompt
        map_prompt = (
            f"Source: {paper.display_name()} ({paper.doc_type})\n"
            f"Summary: {digest_text[:800]}\n\n"
            f"Topic: {topic_query}\n"
            f"Scope: {scope}\n\n"
            "Does this source contain information relevant to the topic and scope above?\n"
            "If YES: extract the key claim(s) in 1-3 sentences, including any specific\n"
            "numbers, mechanisms, or contested points. Prefix with YES:\n"
            "If NO: respond with exactly: NO"
        )

        response = complete(
            messages=[{"role": "user", "content": map_prompt}],
            model=map_model,
            temperature=0.1,
            max_tokens=200,
        )

        response_stripped = response.strip()
        is_relevant = response_stripped.upper().startswith("YES")

        # Determine graph role and pagerank
        graph_info = graph_lookup.get(pid, {})
        graph_role = graph_info.get("role", "standard")
        pagerank = graph_info.get("pagerank", 0.0)

        extractions.append(
            SourceExtraction(
                source_id=pid,
                display_name=paper.display_name(),
                doc_type=paper.doc_type,
                graph_role=graph_role,
                pagerank_score=pagerank,
                extraction=response_stripped,
                is_relevant=is_relevant,
                key_source_ids=key_source_ids or [],
            )
        )

    # Sort: relevant first, then by role priority, then by pagerank
    role_order = {"hub": 0, "bridge": 1, "frontier": 2, "standard": 3}

    def _sort_key(e: SourceExtraction) -> tuple[int, int, float]:
        return (
            0 if e.is_relevant else 1,
            role_order.get(e.graph_role, 3),
            -e.pagerank_score,
        )

    extractions.sort(key=_sort_key)

    logger.info(
        "map_chunks_to_topic(%r): %d candidates, %d relevant",
        topic_query,
        len(extractions),
        sum(1 for e in extractions if e.is_relevant),
    )
    return extractions


def reduce_to_article(
    topic: str,
    scope: str,
    domain: str,
    extractions: list[SourceExtraction],
    persona: str,
    status: str = "draft",
    model: str | None = None,
) -> str:
    """Synthesise extracted evidence into a wiki article body.

    Uses the domain persona as the first line of the system prompt. Determines
    article register from the doc_type mix and selects zone labels accordingly.

    Args:
        topic: Article title.
        scope: One-sentence scope description.
        domain: Domain name (for logging).
        extractions: Output from map_chunks_to_topic().
        persona: Domain persona text from get_or_create_persona().
        status: "stub" | "draft" | "full" -- controls target length.
        model: litellm model string. Defaults to settings.llm_model.

    Returns:
        Article body markdown (no frontmatter).
    """
    relevant = [e for e in extractions if e.is_relevant]

    # Determine register and zone labels
    register = _determine_register(extractions)
    if "design" in domain.lower():
        register = "design"
    established_label, contested_label, open_label = ZONE_LABELS.get(register, ZONE_LABELS["mixed"])

    # Count doc types for provenance line
    from collections import Counter

    doc_type_counts = Counter(e.doc_type for e in relevant)
    n_papers = (
        doc_type_counts.get("paper", 0)
        + doc_type_counts.get("report", 0)
        + doc_type_counts.get("thesis", 0)
    )
    n_web = doc_type_counts.get("web_article", 0) + doc_type_counts.get("markdown", 0)
    n_notes = doc_type_counts.get("note", 0)

    provenance_parts = []
    if n_papers:
        provenance_parts.append(f"{n_papers} paper{'s' if n_papers != 1 else ''}")
    if n_web:
        provenance_parts.append(f"{n_web} web article{'s' if n_web != 1 else ''}")
    if n_notes:
        provenance_parts.append(f"{n_notes} note{'s' if n_notes != 1 else ''}")
    provenance_str = ", ".join(provenance_parts) if provenance_parts else f"{len(relevant)} sources"

    primary_type = "mixed sources"
    if n_papers >= n_web and n_papers >= n_notes and n_papers > 0:
        primary_type = "Primary evidence from peer-reviewed papers."
    elif n_web >= n_papers and n_web >= n_notes and n_web > 0:
        primary_type = "Primary evidence from web articles and practitioner resources."
    elif n_notes > 0:
        primary_type = "Primary evidence from notes and internal documents."

    evidence_block = _build_evidence_block(relevant)
    if not evidence_block.strip():
        evidence_block = "(No relevant evidence extracted from corpus.)"

    length = _length_hint(status)

    system_prompt = (
        f"{persona}\n\n"
        "You are writing wiki articles for a personal knowledge base. "
        "Follow the domain persona above strictly."
    )

    user_msg = (
        f"You are writing a wiki article titled: {topic}\n"
        f"Scope: {scope}\n"
        f"Target: {length}\n\n"
        "The following evidence was extracted from the corpus.\n"
        "Sources marked [HUB] are highly cited field-defining papers -- treat their claims\n"
        "as representing established consensus. Sources marked [BRIDGE] connect different\n"
        "research communities -- their claims often belong in the Contested or Synthesis zone.\n"
        "Sources marked [FRONTIER] represent recent or peripheral work"
        " -- use for Open Questions.\n\n"
        "--- EVIDENCE ---\n"
        f"{evidence_block}\n"
        "--- END EVIDENCE ---\n\n"
        f"Write the article using this structure:\n\n"
        f"## {established_label}\n"
        "[Claims supported by multiple sources, especially HUBs. "
        "Inline citations [REF:display_name].]\n\n"
        f"## {contested_label}\n"
        "[Where sources disagree or practitioners diverge. Cite both sides.]\n\n"
        f"## {open_label}\n"
        "[What is unresolved. No citations -- this is the absence of evidence.]\n\n"
        "## Source Pointers\n"
        "[For each claim you had to compress: annotated pointer to exact source + location.]\n\n"
        f"_Provenance: {provenance_str}. {primary_type}_\n\n"
        "Rules:\n"
        "- One concept per sentence\n"
        "- Inline citations immediately after the claim they support\n"
        "- No em-dashes as separators\n"
        "- No meta-commentary ('this article covers...')\n"
        "- Do not invent claims not present in the evidence"
    )

    article_body = complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2500,
        use_cache=False,
    )

    logger.info(
        "reduce_to_article(%r): register=%r, %d relevant sources -> %d chars",
        topic,
        register,
        len(relevant),
        len(article_body),
    )
    return article_body


def record_coverage(
    article_slug: str,
    domain: str,
    extractions: list[SourceExtraction],
) -> int:
    """Write SourceCoverage rows for all is_relevant=True extractions.

    Uses a single DB session for all inserts (batch write).

    Args:
        article_slug: WikiArticle.id (slug) for the article.
        domain: Domain name for the coverage rows.
        extractions: Full list from map_chunks_to_topic().

    Returns:
        Number of rows written.
    """

    relevant = [e for e in extractions if e.is_relevant]
    if not relevant:
        return 0

    with get_session() as session:
        for ext in relevant:
            row = SourceCoverage(
                source_id=ext.source_id,
                article_slug=article_slug,
                domain=domain,
                extraction=ext.extraction,
            )
            session.add(row)
        session.commit()

    logger.info(
        "record_coverage: wrote %d SourceCoverage rows for article %r",
        len(relevant),
        article_slug,
    )
    return len(relevant)
