"""Epoch orchestrator for the Wikipedia/epoch pipeline.

Runs one complete epoch of the wiki-building loop (5 passes in order) and tracks
convergence via a scalar loss function L.

Pipeline:
    run_epoch()
        Pass 1 -- concept discovery  (discover_concepts)
        Pass 2 -- graph building      (build_concept_graph, score_importance, ...)
        Pass 3 -- article writing     (write_concept_article / upgrade_concept_article)
        Pass 4 -- cross-linking       (cross_link_articles)
        Pass 5 -- index rebuild       (generate_wiki_index, compute_loss)
    -> EpochLog (persisted)

Convergence:
    check_convergence() -- all 4 criteria must hold
    run_until_convergence() -- loop until convergence or max_epochs reached
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import select

from wikify.config import settings
from wikify.llm.client import complete
from wikify.store.db import get_session
from wikify.store.models import (
    ConceptOccurrence,
    ConceptRecord,
    DomainMembership,
    EpochLog,
    GraphEdge,
    PageProvenance,
    Paper,
    SourceCoverage,
)
from wikify.wiki.article import (
    should_write_full,
    upgrade_concept_article,
    write_concept_article,
)
from wikify.wiki.builder import (
    article_path,
    generate_all_domain_condensations,
    generate_wiki_index,
    write_article,
)
from wikify.wiki.layout import iter_visible_page_files
from wikify.wiki.telemetry import (
    begin_run,
    finish_run,
    rebuild_index_stub,
    record_experiment_tags,
    record_loss_components,
    record_page_delta,
    record_retrieval,
    snapshot_wiki_metrics,
    stage_timer,
    update_run_metadata,
)
from wikify.wiki.graph.build import (
    build_concept_graph,
    classify_node_roles,
    extract_relations,
    save_relations,
    score_importance,
    update_concept_importance,
)
from wikify.wiki.concepts import (
    clear_staged_extractions,
    discover_concepts,
    list_concepts,
    store_evidence,
    store_gaps,
    store_occurrences,
    store_parameters,
    store_relation_evidence,
)
from wikify.wiki.graph.domains import FAST_MODEL, discover_domains
from wikify.wiki.linker import cross_link_articles
from wikify.wiki.mapreduce import SourceExtraction, map_chunks_to_topic
from wikify.wiki.template import refine_template

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_WIKI_DIR = Path("data/wiki")

# Loss function weights (alpha + beta + gamma - delta need not sum to 1;
# the formula is a signed linear combination).
_ALPHA = 0.3  # stub_ratio weight
_BETA = 0.2  # orphan_concept_rate weight
_GAMMA = 0.3  # contradiction_density weight
_DELTA = 0.2  # cross_ref_density weight (negative contribution to loss)

# Convergence thresholds
_CONVERGENCE_NEW_CONCEPT_RATE = 0.02  # < 2% new concepts relative to total
_CONVERGENCE_STUB_RATIO = 0.10  # < 10% stubs
_CONVERGENCE_LOSS_DELTA = 0.01  # epsilon


# ── Private helpers ────────────────────────────────────────────────────────────


def _get_next_epoch_number() -> int:
    """Return the next epoch number to use (max existing + 1, or 1)."""
    with get_session() as session:
        all_logs: list[EpochLog] = list(session.exec(select(EpochLog)).all())

    if not all_logs:
        return 1
    return max(log.epoch for log in all_logs) + 1


def _get_all_paper_ids() -> list[str]:
    """Return ids of all corpus papers (origin == 'corpus')."""
    with get_session() as session:
        papers: list[Paper] = list(
            session.exec(select(Paper).where(Paper.origin == "corpus")).all()
        )
    return [p.id for p in papers]


def epoch_log_to_summary(log: EpochLog) -> dict[str, object]:
    """Convert a persisted EpochLog into a stable summary shape."""
    return {
        "epoch": log.epoch,
        "workflow_type": "epoch",
        "triggered_by": log.triggered_by,
        "concepts_discovered": log.concepts_discovered,
        "articles_written": log.articles_written,
        "stubs_upgraded": log.stubs_upgraded,
        "contradictions_flagged": log.contradictions_flagged,
        "cross_refs_added": log.cross_refs_added,
        "loss": log.loss_score,
        "loss_delta": log.loss_delta,
        "template_delta": log.template_delta,
        "converged": log.converged,
        "started_at": log.started_at.isoformat() if log.started_at else "",
        "completed_at": log.completed_at.isoformat() if log.completed_at else "",
    }


# ── Boolean gating agent ───────────────────────────────────────────────────────


def should_update_article(
    existing_article: str,
    new_extractions: list[SourceExtraction],
    model: str = FAST_MODEL,
) -> bool:
    """Two-gate check before spending a model rewrite on an existing article.

    Gate 1 (gradient pre-filter):
        Skip if new_evidence_tokens / existing_article_tokens < 0.05.

    Gate 2 (fast-tier semantic check):
        Ask the fast tier whether the new evidence adds facts not present in the article.
        Return True only when the response contains "YES".

    Args:
        existing_article: Current article body text.
        new_extractions:  Fresh SourceExtraction objects to evaluate.
        model:            Model for the semantic gate (default fast tier).

    Returns:
        True if the article should be rewritten with the new evidence.
    """
    if not new_extractions:
        return False

    new_evidence_text = "\n".join(
        e.extraction for e in new_extractions if e.is_relevant and e.extraction != "NO"
    ).strip()

    if not new_evidence_text:
        return False

    # Gate 1: rough token approximation (chars / 4)
    existing_tokens = max(len(existing_article) / 4, 1)
    new_tokens = len(new_evidence_text) / 4

    if new_tokens / existing_tokens < 0.05:
        logger.debug(
            "should_update_article: Gate 1 blocked (gradient %.3f < 0.05)",
            new_tokens / existing_tokens,
        )
        return False

    # Gate 2: fast-tier semantic check
    prompt = (
        "You are reviewing whether new evidence warrants rewriting an existing article.\n\n"
        "--- EXISTING ARTICLE ---\n"
        f"{existing_article[:3000]}\n"
        "--- END EXISTING ARTICLE ---\n\n"
        "--- NEW EVIDENCE ---\n"
        f"{new_evidence_text[:2000]}\n"
        "--- END NEW EVIDENCE ---\n\n"
        "Does this new evidence add facts, corrections, or context not already present in "
        "this article? Return YES or NO only."
    )

    try:
        response = complete(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=8,
            use_cache=False,
        )
    except Exception:
        logger.exception("should_update_article: fast-tier gate call failed, defaulting to False")
        return False

    result = "YES" in response.upper()
    logger.debug("should_update_article: Gate 2 response=%r -> %s", response.strip(), result)
    return result


# ── Loss computation ───────────────────────────────────────────────────────────


def _count_warning_markers(wiki_dir: Path) -> tuple[int, int]:
    """Scan all article files and count WARNING markers and total articles.

    Returns:
        (warning_count, total_article_count)
    """
    total_articles = 0
    warning_count = 0
    warning_pattern = re.compile(r"\bWARNING\b", re.IGNORECASE)

    for md_file in iter_visible_page_files(wiki_dir):
        total_articles += 1
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            warning_count += len(warning_pattern.findall(text))
        except OSError as exc:
            logger.warning("_count_warning_markers: could not read %s: %s", md_file, exc)

    return warning_count, total_articles


def _count_wikilinks(wiki_dir: Path, total_articles: int) -> float:
    """Count total [[wikilinks]] across all articles.

    Returns:
        Average wikilinks per article, or 0.0 if no articles.
    """
    if total_articles == 0:
        return 0.0

    wikilink_pattern = re.compile(r"\[\[[^\]]+\]\]")
    total_links = 0

    for md_file in iter_visible_page_files(wiki_dir):
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            total_links += len(wikilink_pattern.findall(text))
        except OSError as exc:
            logger.warning("_count_wikilinks: could not read %s: %s", md_file, exc)

    return total_links / total_articles


def compute_loss(epoch: int) -> tuple[float, float]:
    """Compute the epoch loss L and delta from the previous epoch.

    Formula:
        L = alpha * stub_ratio
          + beta  * orphan_concept_rate
          + gamma * contradiction_density
          - delta * cross_ref_density

    Clamped to [0, 1].

    Args:
        epoch: The epoch number just completed.

    Returns:
        (loss_score, loss_delta) tuple.
    """
    with get_session() as session:
        all_concepts: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())
        all_occurrences: list[ConceptOccurrence] = list(session.exec(select(ConceptOccurrence)).all())
        all_coverage: list[SourceCoverage] = list(session.exec(select(SourceCoverage)).all())

    total_concepts = len(all_concepts)

    if total_concepts == 0:
        logger.warning("compute_loss: no concepts found, returning 0.0")
        return 0.0, 0.0

    # stub_ratio
    stub_count = sum(1 for c in all_concepts if c.article_status in ("none", "stub"))
    stub_ratio = stub_count / total_concepts

    # orphan_concept_rate — concepts with no SourceCoverage rows
    covered_slugs: set[str]
    if all_occurrences:
        covered_slugs = {occ.concept_id for occ in all_occurrences}
    else:
        covered_slugs = {cov.article_slug for cov in all_coverage}
    orphan_count = sum(1 for c in all_concepts if c.id not in covered_slugs)
    orphan_concept_rate = orphan_count / total_concepts

    # contradiction_density — WARNING markers per article
    warning_count, total_articles = _count_warning_markers(_WIKI_DIR)
    contradiction_density = warning_count / total_articles if total_articles > 0 else 0.0

    # cross_ref_density — [[wikilinks]] per article
    cross_ref_density = _count_wikilinks(_WIKI_DIR, total_articles)

    # Convert link density into a smoother [0, 1) score without flattening too early.
    # 0 links/article -> 0.0, 1 link/article -> 0.25, 3 links/article -> 0.5.
    cross_ref_density_clamped = cross_ref_density / (cross_ref_density + 3.0)

    loss = (
        _ALPHA * stub_ratio
        + _BETA * orphan_concept_rate
        + _GAMMA * contradiction_density
        - _DELTA * cross_ref_density_clamped
    )
    loss = max(0.0, min(1.0, loss))

    # Get previous epoch's loss for delta
    prev_loss = 0.0
    with get_session() as session:
        prev_logs: list[EpochLog] = list(
            session.exec(select(EpochLog).where(EpochLog.epoch < epoch)).all()
        )
    if prev_logs:
        prev_log = max(prev_logs, key=lambda lg: lg.epoch)
        prev_loss = prev_log.loss_score

    loss_delta = abs(loss - prev_loss)

    logger.info(
        "compute_loss(epoch=%d): stub=%.3f orphan=%.3f contradiction=%.3f "
        "cross_ref=%.3f -> L=%.4f delta=%.4f",
        epoch,
        stub_ratio,
        orphan_concept_rate,
        contradiction_density,
        cross_ref_density,
        loss,
        loss_delta,
    )
    return loss, loss_delta


# ── Convergence ────────────────────────────────────────────────────────────────


def check_convergence(recent_logs: list[EpochLog]) -> bool:
    """Return True when all four convergence criteria are met.

    Criteria:
    1. New concepts / epoch  < 2% of total concept count.
    2. Stub ratio            < 10%.
    3. No new contradictions in the last epoch (contradictions_flagged == 0).
    4. loss_delta            < 0.01 (epsilon).

    Args:
        recent_logs: EpochLog rows from recent epochs (at least the last one).

    Returns:
        True if converged.
    """
    if not recent_logs:
        return False

    last_log = max(recent_logs, key=lambda lg: lg.epoch)

    with get_session() as session:
        total_concepts: int = len(list(session.exec(select(ConceptRecord)).all()))

    if total_concepts == 0:
        return False

    # Criterion 1: new concept rate
    new_concept_rate = last_log.concepts_discovered / total_concepts
    if new_concept_rate >= _CONVERGENCE_NEW_CONCEPT_RATE:
        logger.debug(
            "check_convergence: criterion 1 failed (new_concept_rate=%.4f)", new_concept_rate
        )
        return False

    # Criterion 2: stub ratio
    with get_session() as session:
        all_concepts: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())
    stub_count = sum(1 for c in all_concepts if c.article_status in ("none", "stub"))
    stub_ratio = stub_count / total_concepts
    if stub_ratio >= _CONVERGENCE_STUB_RATIO:
        logger.debug("check_convergence: criterion 2 failed (stub_ratio=%.4f)", stub_ratio)
        return False

    # Criterion 3: no new contradictions
    if last_log.contradictions_flagged != 0:
        logger.debug(
            "check_convergence: criterion 3 failed (contradictions=%d)",
            last_log.contradictions_flagged,
        )
        return False

    # Criterion 4: loss delta
    if last_log.loss_delta >= _CONVERGENCE_LOSS_DELTA:
        logger.debug("check_convergence: criterion 4 failed (loss_delta=%.4f)", last_log.loss_delta)
        return False

    logger.info("check_convergence: all 4 criteria met -> converged")
    return True


# ── Concept deduplication ──────────────────────────────────────────────────────


def _deduplicate_concepts(concepts: list[ConceptRecord]) -> list[ConceptRecord]:
    """Remove near-duplicate concepts before article writing.

    Groups concepts by embedding similarity of their names + definitions.
    For each group of near-duplicates (similarity > 0.85), keeps only the
    concept with the highest importance score.  The others get their
    article_status set to "merged:<kept_concept_id>" and are excluded from
    the returned list.

    Returns:
        Deduplicated list (kept concepts only).
    """
    import numpy as np

    if len(concepts) < 2:
        return concepts

    # Build structured text representations for embedding (Phase 6)
    from wikify.wiki.vectors import build_structured_texts  # noqa: PLC0415

    texts = build_structured_texts(concepts)

    # Encode all concepts at once
    try:
        from wikify.store.embeddings import _store  # noqa: PLC0415

        embeddings = _store.model.encode(texts)  # shape (N, D)
    except Exception:
        logger.exception("_deduplicate_concepts: could not encode concepts, skipping dedup")
        return concepts

    n = len(concepts)
    # Normalise rows so dot product == cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normed = embeddings / norms

    # Union-Find for grouping
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: int, y: int) -> None:
        parent[_find(x)] = _find(y)

    # Pairwise similarity — O(N^2) but N is typically < 500 concepts
    sim_matrix = normed @ normed.T  # (N, N)
    threshold = 0.85
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] > threshold:
                _union(i, j)

    # Build groups
    from collections import defaultdict

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[_find(idx)].append(idx)

    merged_groups = [g for g in groups.values() if len(g) > 1]

    if not merged_groups:
        logger.info("_deduplicate_concepts: no near-duplicate groups found (%d concepts)", n)
        return concepts

    kept_set: set[int] = set(range(n))  # indices to keep
    db_updates: list[tuple[str, str]] = []  # (merged_id, kept_id)

    for group in merged_groups:
        # Keep the concept with the highest importance
        best_idx = max(group, key=lambda i: concepts[i].importance)
        kept_id = concepts[best_idx].id
        for idx in group:
            if idx != best_idx:
                kept_set.discard(idx)
                db_updates.append((concepts[idx].id, kept_id))

    # Persist merged status to DB
    if db_updates:
        with get_session() as session:
            for merged_id, kept_id in db_updates:
                db_concept = session.get(ConceptRecord, merged_id)
                if db_concept is not None:
                    db_concept.article_status = f"merged:{kept_id}"
                    session.add(db_concept)
            session.commit()

    result = [concepts[i] for i in sorted(kept_set)]

    logger.info(
        "dedup: %d concepts -> %d after merging %d group(s) (%d concepts merged)",
        n,
        len(result),
        len(merged_groups),
        n - len(result),
    )
    return result


# ── Main epoch orchestrator ────────────────────────────────────────────────────


def run_epoch(
    triggered_by: str = "user",
    domain: str = "",
    model: Optional[str] = None,
) -> EpochLog:
    """Run one complete epoch of the Wikipedia pipeline (5 passes).

    Args:
        triggered_by: "user" | "ingest" | "schedule"
        domain:       Domain filter for graph building and article writing.
                      Pass "" to process all domains.
        model:        Override the article-writing model.  When None, model
                      selection follows the loss-based rule (fast vs balanced).

    Returns:
        Completed EpochLog row (persisted to DB).
    """
    epoch = _get_next_epoch_number()
    started_at = datetime.now(timezone.utc)

    log = EpochLog(
        epoch=epoch,
        triggered_by=triggered_by,
        started_at=started_at,
    )

    logger.info(
        "=== Epoch %d started (triggered_by=%r, domain=%r) ===",
        epoch,
        triggered_by,
        domain,
    )
    rebuild_index_stub(_WIKI_DIR)
    run_id = begin_run(
        workflow_type="epoch",
        status="pending",
        strategy_id="default_epoch",
        loss_definition_id="wiki_loss_v1",
        prompt_family="wiki_epoch_v1",
        model_tier="balanced",
        model_name=model or "",
    )
    record_experiment_tags(
        run_id,
        {
            "workflow": "epoch",
            "domain": domain or "all",
            "triggered_by": triggered_by,
        },
    )

    # ── Determine article model based on previous epoch's loss ────────────────
    if model is not None:
        article_model: str = model
    else:
        # Check previous epoch loss
        prev_loss = 0.0
        with get_session() as session:
            prev_logs: list[EpochLog] = list(
                session.exec(select(EpochLog).where(EpochLog.epoch < epoch)).all()
            )
        if prev_logs:
            prev_log = max(prev_logs, key=lambda lg: lg.epoch)
            prev_loss = prev_log.loss_score

        if prev_loss >= 0.3:
            article_model = FAST_MODEL
            logger.info(
                "run_epoch: prev_loss=%.3f >= 0.3 -> using fast tier for article writing",
                prev_loss,
            )
        else:
            article_model = settings.llm_model
            logger.info(
                "run_epoch: prev_loss=%.3f < 0.3 -> using %s for article writing",
                prev_loss,
                settings.llm_model,
            )
    update_run_metadata(run_id, model_name=article_model, model_tier="balanced")

    # ── Pass 1: Concept Discovery ─────────────────────────────────────────────
    pass1_stage = stage_timer(run_id, "concept_discovery")
    pass2_stage = stage_timer(run_id, "graph_building")
    pass3_stage = stage_timer(run_id, "article_writing")
    t0 = time.monotonic()
    logger.info("--- Pass 1: Concept Discovery (epoch=%d) ---", epoch)

    clear_staged_extractions(epoch)
    paper_ids = _get_all_paper_ids()
    logger.info("Pass 1: processing %d corpus papers", len(paper_ids))

    # Agent-native: the orchestrating agent supplies an extractor through
    # the runtime in production. Without one, EchoExtractor surfaces a
    # clean "no agent wired in" run that produces zero new concepts.
    discovery = discover_concepts(paper_ids, epoch)
    log.concepts_discovered = len(discovery.concepts)

    # Store evidence and gaps from rich extraction results
    rich = discovery.rich_extractions
    if rich:
        store_evidence(rich, epoch)
        store_gaps(rich, epoch)
        store_parameters(rich, epoch)
        store_occurrences(rich, epoch)
        store_relation_evidence(rich, epoch)

    logger.info(
        "Pass 1 complete in %.1fs: %d concepts discovered",
        time.monotonic() - t0,
        log.concepts_discovered,
    )
    pass1_stage.finish(paper_count=len(paper_ids), concepts_discovered=log.concepts_discovered)

    # ── Pass 2: Graph Building ─────────────────────────────────────────────────
    pass2_stage = stage_timer(run_id, "graph_building")
    t0 = time.monotonic()
    logger.info("--- Pass 2: Graph Building (epoch=%d) ---", epoch)

    graph = build_concept_graph(domain, epoch)
    scores = score_importance(graph)
    update_concept_importance(scores)
    classify_node_roles(graph, scores)  # side-effect: node role attributes stored in graph
    relations = extract_relations(graph, epoch)
    n_relations = save_relations(relations, epoch)
    with get_session() as session:
        for rel in relations:
            session.add(
                GraphEdge(
                    source_slug=rel.source_concept,
                    target_slug=rel.target_concept,
                    relation_type=rel.relation_type,
                    weight=rel.weight,
                    epoch=epoch,
                )
            )
        session.commit()

    logger.info(
        "Pass 2 complete in %.1fs: graph nodes=%d edges=%d relations_saved=%d",
        time.monotonic() - t0,
        graph.number_of_nodes(),
        graph.number_of_edges(),
        n_relations,
    )
    pass2_stage.finish(
        graph_nodes=graph.number_of_nodes(),
        graph_edges=graph.number_of_edges(),
        relations_saved=n_relations,
    )

    # ── Pass 2b: Domain Discovery ────────────────────────────────────────────
    pass2b_stage = stage_timer(run_id, "domain_discovery")
    t0 = time.monotonic()
    logger.info("--- Pass 2b: Domain Discovery (epoch=%d) ---", epoch)

    domain_clusters = discover_domains(graph, epoch, model=FAST_MODEL)

    # Build a concept -> primary domain label lookup for Pass 3 scoping
    _concept_domain: dict[str, str] = {}
    _concept_is_bridge: set[str] = set()
    for cluster in domain_clusters:
        for cid in cluster.parsed_core_concepts:
            _concept_domain[cid] = cluster.label
        for cid in cluster.parsed_bridge_concepts:
            _concept_is_bridge.add(cid)
            _concept_domain.setdefault(cid, cluster.label)
    with get_session() as session:
        for concept_id, concept_domain in _concept_domain.items():
            session.add(
                DomainMembership(
                    page_slug=concept_id,
                    domain=concept_domain,
                    confidence=1.0,
                    source="epoch_domain_discovery",
                )
            )
        session.commit()

    # Map cluster id -> cluster for persona lookup
    _cluster_by_id: dict[str, object] = {c.id: c for c in domain_clusters}

    logger.info(
        "Pass 2b complete in %.1fs: %d domains discovered",
        time.monotonic() - t0,
        len(domain_clusters),
    )
    pass2b_stage.finish(domains_discovered=len(domain_clusters))

    # ── Pass 3: Article Writing ────────────────────────────────────────────────
    pass3_stage = stage_timer(run_id, "article_writing")
    t0 = time.monotonic()
    logger.info("--- Pass 3: Article Writing (epoch=%d, model=%s) ---", epoch, article_model)

    # Reload concepts from DB after importance update, sorted descending
    all_concepts = list_concepts(domain=domain, min_importance=0.0)
    all_concepts.sort(key=lambda c: c.importance, reverse=True)

    # Deduplicate near-synonym concepts before article writing
    all_concepts = _deduplicate_concepts(all_concepts)

    articles_written = 0
    stubs_upgraded = 0
    contradictions_flagged = 0

    for concept in all_concepts:
        if concept.article_status == "none":
            # ── Write new article ──────────────────────────────────────────────
            try:
                neighbor_ids = list(graph.neighbors(concept.id)) if concept.id in graph else []
                with get_session() as session:
                    neighbors: list[ConceptRecord] = [
                        session.get(ConceptRecord, nid)
                        for nid in neighbor_ids
                        if session.get(ConceptRecord, nid) is not None
                    ]

                # Use domain-scoped label if available, fall back to epoch domain
                concept_domain = _concept_domain.get(concept.id, domain)
                extractions: list[SourceExtraction] = map_chunks_to_topic(
                    topic_query=concept.name,
                    scope=concept.definition or concept.name,
                    domain=concept_domain,
                    model=FAST_MODEL,
                )
                relevant_extractions = [e for e in extractions if e.is_relevant]
                record_retrieval(
                    run_id,
                    stage_name="article_writing",
                    query=concept.name,
                    candidates_considered=len(extractions),
                    chunks_selected=len(relevant_extractions),
                    raw_fallback_used=False,
                    domains=[concept_domain] if concept_domain else [],
                )
                body = write_concept_article(
                    concept,
                    neighbors,
                    concept_domain,
                    article_model,
                    extractions=extractions,
                )

                status = "full" if should_write_full(concept, extractions) else "stub"

                source_ids: list[str] = list({e.source_id for e in relevant_extractions})

                fpath = article_path(_WIKI_DIR, "concepts", concept.id)
                write_article(
                    fpath,
                    concept.name,
                    body,
                    source_ids,
                    [concept.concept_type] if concept.concept_type else [],
                    status,
                    article_model,
                    page_type="concept",
                    domains=[concept_domain] if concept_domain else [],
                )

                with get_session() as session:
                    db_concept = session.get(ConceptRecord, concept.id)
                    if db_concept is not None:
                        db_concept.article_status = status
                        db_concept.article_path = str(fpath)
                        db_concept.epoch_last_updated = epoch
                        session.add(db_concept)
                    for ext in relevant_extractions:
                        session.add(
                            PageProvenance(
                                page_slug=concept.id,
                                paper_id=ext.source_id,
                                section_name="article",
                                evidence_quote=ext.extraction,
                            )
                        )
                    session.commit()

                articles_written += 1
                record_page_delta(
                    run_id,
                    page_slug=concept.id,
                    action="create",
                    page_type="concept",
                    source_count=len(source_ids),
                )
                logger.debug("Pass 3: wrote new article for %r (status=%s)", concept.name, status)

            except Exception:
                logger.exception("Pass 3: failed to write article for %r", concept.name)

        elif concept.article_status in ("stub", "draft"):
            # ── Consider upgrading existing article ────────────────────────────
            if not concept.article_path:
                logger.debug(
                    "Pass 3: concept %r has status=%s but no article_path, skipping",
                    concept.name,
                    concept.article_status,
                )
                continue

            article_file = Path(concept.article_path)
            if not article_file.exists():
                logger.warning(
                    "Pass 3: article file missing for %r at %s, skipping",
                    concept.name,
                    article_file,
                )
                continue

            try:
                concept_domain = _concept_domain.get(concept.id, domain)
                new_extractions: list[SourceExtraction] = map_chunks_to_topic(
                    topic_query=concept.name,
                    scope=concept.definition or concept.name,
                    domain=concept_domain,
                    model=FAST_MODEL,
                )

                # Bridge concepts: also pull evidence from adjacent domains
                if concept.id in _concept_is_bridge:
                    for d_label in concept.parsed_domains:
                        if d_label != concept_domain:
                            cross_ext = map_chunks_to_topic(
                                topic_query=concept.name,
                                scope=concept.definition or concept.name,
                                domain=d_label,
                                model=FAST_MODEL,
                            )
                            new_extractions.extend(cross_ext)

                relevant_extractions = [e for e in new_extractions if e.is_relevant]
                record_retrieval(
                    run_id,
                    stage_name="article_writing",
                    query=concept.name,
                    candidates_considered=len(new_extractions),
                    chunks_selected=len(relevant_extractions),
                    raw_fallback_used=False,
                    domains=[concept_domain] if concept_domain else [],
                )
                if not relevant_extractions:
                    continue

                existing_text = article_file.read_text(encoding="utf-8", errors="replace")

                if not should_update_article(existing_text, relevant_extractions):
                    logger.debug("Pass 3: boolean gate blocked upgrade for %r", concept.name)
                    continue

                updated_body = upgrade_concept_article(
                    concept, article_file, relevant_extractions, concept_domain, article_model
                )

                # Determine new status
                new_status = (
                    "full"
                    if should_write_full(concept, new_extractions)
                    else concept.article_status
                )

                source_ids = list({e.source_id for e in new_extractions if e.is_relevant})
                write_article(
                    article_file,
                    concept.name,
                    updated_body,
                    source_ids,
                    [concept.concept_type] if concept.concept_type else [],
                    new_status,
                    article_model,
                    page_type="concept",
                    domains=[concept_domain] if concept_domain else [],
                )

                with get_session() as session:
                    db_concept = session.get(ConceptRecord, concept.id)
                    if db_concept is not None:
                        db_concept.article_status = new_status
                        db_concept.epoch_last_updated = epoch
                        session.add(db_concept)
                    for ext in relevant_extractions:
                        session.add(
                            PageProvenance(
                                page_slug=concept.id,
                                paper_id=ext.source_id,
                                section_name="article",
                                evidence_quote=ext.extraction,
                            )
                        )
                    session.commit()

                stubs_upgraded += 1
                record_page_delta(
                    run_id,
                    page_slug=concept.id,
                    action="update",
                    page_type="concept",
                    source_count=len(source_ids),
                )
                if new_status != concept.article_status:
                    logger.debug(
                        "Pass 3: upgraded %r: %s -> %s",
                        concept.name,
                        concept.article_status,
                        new_status,
                    )

                # Count contradiction markers in the updated body
                warning_hits = len(re.findall(r"\bWARNING\b", updated_body, re.IGNORECASE))
                contradictions_flagged += warning_hits

            except Exception:
                logger.exception("Pass 3: failed to upgrade article for %r", concept.name)

    log.articles_written = articles_written
    log.stubs_upgraded = stubs_upgraded
    log.contradictions_flagged = contradictions_flagged

    logger.info(
        "Pass 3 complete in %.1fs: articles_written=%d stubs_upgraded=%d contradictions=%d",
        time.monotonic() - t0,
        articles_written,
        stubs_upgraded,
        contradictions_flagged,
    )
    pass3_stage.finish(
        articles_written=articles_written,
        stubs_upgraded=stubs_upgraded,
        contradictions_flagged=contradictions_flagged,
    )

    # ── Pass 4: Cross-linking ──────────────────────────────────────────────────
    pass4_stage = stage_timer(run_id, "cross_linking")
    t0 = time.monotonic()
    logger.info("--- Pass 4: Cross-linking (epoch=%d) ---", epoch)

    cross_refs = cross_link_articles(_WIKI_DIR, sitemap=None)
    log.cross_refs_added = cross_refs

    logger.info(
        "Pass 4 complete in %.1fs: cross_refs_added=%d",
        time.monotonic() - t0,
        cross_refs,
    )
    pass4_stage.finish(cross_refs_added=cross_refs)

    # ── Pass 5: Index Rebuild + Loss ──────────────────────────────────────────
    pass5_stage = stage_timer(run_id, "index_and_loss")
    t0 = time.monotonic()
    logger.info("--- Pass 5: Index Rebuild + Loss (epoch=%d) ---", epoch)

    generate_wiki_index(_WIKI_DIR)
    generate_all_domain_condensations(_WIKI_DIR)

    # Template refinement: evolve the extraction template based on gap feedback
    _, template_delta = refine_template(_WIKI_DIR, epoch, model=FAST_MODEL)
    log.template_delta = template_delta

    loss_score, loss_delta = compute_loss(epoch)
    log.loss_score = loss_score
    log.loss_delta = loss_delta
    with get_session() as session:
        occurrence_rows: list[ConceptOccurrence] = list(session.exec(select(ConceptOccurrence)).all())
        coverage_rows: list[SourceCoverage] = list(session.exec(select(SourceCoverage)).all())
    covered_slugs = (
        {row.concept_id for row in occurrence_rows}
        if occurrence_rows
        else {cov.article_slug for cov in coverage_rows}
    )
    visible_pages = iter_visible_page_files(_WIKI_DIR)
    visible_page_count = len(visible_pages)
    cross_ref_density = _count_wikilinks(_WIKI_DIR, max(visible_page_count, 1))
    loss_components = {
        "stub_ratio": (
            sum(1 for c in all_concepts if c.article_status in ("none", "stub")) / max(len(all_concepts), 1),
            _ALPHA,
        ),
        "orphan_concept_rate": (
            sum(1 for c in all_concepts if c.id not in covered_slugs) / max(len(all_concepts), 1),
            _BETA,
        ),
        "contradiction_density": (
            (contradictions_flagged / max(visible_page_count, 1)),
            _GAMMA,
        ),
        "cross_ref_score": (
            cross_ref_density / (cross_ref_density + 3.0) if visible_page_count else 0.0,
            _DELTA,
        ),
    }
    record_loss_components(run_id, loss_name="wiki_loss_v1", components=loss_components)

    # Gather recent logs for convergence check (include the current one)
    with get_session() as session:
        existing_logs: list[EpochLog] = list(session.exec(select(EpochLog)).all())

    # Create a temporary complete log for convergence check
    temp_log = EpochLog(
        epoch=epoch,
        triggered_by=triggered_by,
        started_at=started_at,
        concepts_discovered=log.concepts_discovered,
        stubs_upgraded=log.stubs_upgraded,
        articles_written=log.articles_written,
        contradictions_flagged=log.contradictions_flagged,
        cross_refs_added=log.cross_refs_added,
        loss_score=loss_score,
        loss_delta=loss_delta,
    )
    recent_logs = existing_logs + [temp_log]
    log.converged = check_convergence(recent_logs)

    log.completed_at = datetime.now(timezone.utc)

    # Persist EpochLog
    with get_session() as session:
        session.add(log)
        session.commit()
        session.refresh(log)

    metrics = snapshot_wiki_metrics(_WIKI_DIR, run_id)
    pass5_stage.finish(
        loss_score=loss_score,
        loss_delta=loss_delta,
        template_delta=template_delta,
        converged=log.converged,
        metric_count=len(metrics),
    )
    summary = epoch_log_to_summary(log)
    summary.update(
        {
            "run_id": run_id,
            "workflow_type": "epoch",
            "total_visible_pages": visible_page_count,
            "metric_count": len(metrics),
        }
    )
    finish_run(
        _WIKI_DIR,
        run_id,
        status="applied",
        headline=f"Epoch {epoch}",
        summary=summary,
    )

    logger.info(
        "Pass 5 complete in %.1fs: loss=%.4f delta=%.4f converged=%s",
        time.monotonic() - t0,
        loss_score,
        loss_delta,
        log.converged,
    )
    logger.info(
        "=== Epoch %d complete: %d articles, %d upgrades, L=%.4f, converged=%s ===",
        epoch,
        articles_written,
        stubs_upgraded,
        loss_score,
        log.converged,
    )

    return log


# ── Loop until convergence ─────────────────────────────────────────────────────


def run_until_convergence(
    domain: str = "",
    max_epochs: int = 10,
    model: Optional[str] = None,
) -> list[EpochLog]:
    """Run epochs until convergence or max_epochs is reached.

    Args:
        domain:     Domain filter passed to each run_epoch() call.
        max_epochs: Hard ceiling on the number of epochs to run.
        model:      Optional model override passed to run_epoch().

    Returns:
        List of all EpochLog rows produced.
    """
    logs: list[EpochLog] = []

    for i in range(max_epochs):
        logger.info("run_until_convergence: starting epoch %d of %d max", i + 1, max_epochs)
        log = run_epoch(triggered_by="schedule", domain=domain, model=model)
        logs.append(log)

        if log.converged:
            logger.info("run_until_convergence: converged after %d epoch(s)", len(logs))
            break
    else:
        logger.warning(
            "run_until_convergence: reached max_epochs=%d without convergence", max_epochs
        )

    return logs


# ── Status query ───────────────────────────────────────────────────────────────


def get_epoch_status() -> dict:
    """Return a summary of the current wiki epoch state.

    Reads from the latest EpochLog and current DB concept counts.

    Returns:
        Dict with keys:
            current_epoch, latest_loss, loss_delta, converged,
            total_concepts, stub_count, draft_count, full_count, none_count
    """
    with get_session() as session:
        all_logs: list[EpochLog] = list(session.exec(select(EpochLog)).all())
        all_concepts: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())

    current_epoch = 0
    latest_loss = 0.0
    loss_delta = 0.0
    converged = False

    if all_logs:
        latest_log = max(all_logs, key=lambda lg: lg.epoch)
        current_epoch = latest_log.epoch
        latest_loss = latest_log.loss_score
        loss_delta = latest_log.loss_delta
        converged = latest_log.converged

    total_concepts = len(all_concepts)
    status_counts: dict[str, int] = {"none": 0, "stub": 0, "draft": 0, "full": 0}
    for c in all_concepts:
        key = c.article_status if c.article_status in status_counts else "none"
        status_counts[key] += 1

    return {
        "current_epoch": current_epoch,
        "epochs_completed": current_epoch,
        "latest_loss": latest_loss,
        "loss": latest_loss,
        "loss_delta": loss_delta,
        "converged": converged,
        "last_run": current_epoch if current_epoch else "never",
        "total_concepts": total_concepts,
        "stub_count": status_counts["stub"],
        "draft_count": status_counts["draft"],
        "full_count": status_counts["full"],
        "none_count": status_counts["none"],
    }
