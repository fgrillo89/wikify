"""Shared runtime services for the simplified wiki-first architecture."""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import select

from wikify.core.llm.client import complete
from wikify.core.store.db import get_session
from wikify.core.store.models import (
    Campaign,
    ConceptRecord,
    DomainMembership,
    MaintenanceFinding,
    PageDeltaTelemetry,
    PageProvenance,
    Paper,
    RetrievalTelemetry,
    RunLog,
    StageTelemetry,
    TokenUsageTelemetry,
    WikiPage,
    WikiSnapshotMetric,
)
from wikify.wiki.builder import (
    append_unanswered_question,
    read_article_frontmatter,
    slugify,
    write_article,
)
from wikify.wiki.discovery.extractors import AgentExtractor
from wikify.wiki.observability import (
    begin_run,
    finish_run,
    record_page_delta,
    record_retrieval,
    record_tool_call,
    snapshot_wiki_metrics,
    stage_timer,
)
from wikify.wiki.presentation.layout import (
    ensure_layout,
    iter_visible_page_files,
    metrics_dir,
    visible_page_path,
)

_WIKI_DIR = Path("data/wiki")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
_WARNING_RE = re.compile(r"\bWARNING\b", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_list_field(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if raw.startswith("[") and raw.endswith("]"):
        items = [part.strip().strip("'\"") for part in raw[1:-1].split(",")]
        return [item for item in items if item]
    return [raw]


def _parse_float_field(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _page_record_from_path(wiki_dir: Path, path: Path) -> dict[str, Any]:
    frontmatter = read_article_frontmatter(path)
    slug = str(frontmatter.get("slug") or path.stem).strip() or path.stem
    title = str(frontmatter.get("title") or slug.replace("_", " ").title()).strip()
    page_type = str(frontmatter.get("page_type") or frontmatter.get("type") or "concept").strip()
    status = str(frontmatter.get("status") or "draft").strip()
    domains = _parse_list_field(frontmatter.get("domains"))
    source_ids = _parse_list_field(frontmatter.get("source_ids") or frontmatter.get("sources"))
    confidence = _parse_float_field(frontmatter.get("confidence"), default=0.0)
    return {
        "slug": slug,
        "title": title,
        "page_type": page_type,
        "status": status,
        "domains": domains,
        "source_ids": source_ids,
        "confidence": confidence,
        "file_path": str(path.relative_to(wiki_dir)),
        "path": path,
    }


def _read_page_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text


def _search_visible_pages(
    wiki_dir: Path,
    question: str,
    *,
    domain: str = "",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    tokens = [tok for tok in re.findall(r"\w+", question.lower()) if len(tok) > 1]
    if not tokens:
        tokens = [question.lower().strip()]
    scored: list[tuple[float, dict[str, Any]]] = []

    for path in iter_visible_page_files(wiki_dir):
        record = _page_record_from_path(wiki_dir, path)
        if domain and domain not in record["domains"]:
            continue
        body = _read_page_body(path)
        haystack = f"{record['title']} {record['slug']} {record['page_type']} {body}".lower()
        score = 0.0
        for token in tokens:
            if token in record["title"].lower():
                score += 3.0
            if token in record["slug"].lower():
                score += 2.0
            if token in haystack:
                score += 1.0
        if score <= 0:
            continue
        record["body"] = body
        scored.append((score, record))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for _score, record in scored[:top_k]]


def _promote_answer_page(
    wiki_dir: Path,
    *,
    title: str,
    answer: str,
    source_ids: list[str],
    domains: list[str],
    model: str,
    page_type: str = "query",
) -> Path:
    slug = slugify(title[:80]) or "query"
    path = visible_page_path(wiki_dir, slug=slug, page_type=page_type)
    write_article(
        path=path,
        title=title,
        content=answer,
        sources=source_ids,
        topics=[slug],
        status="full",
        model=model,
        page_type=page_type,
        domains=domains,
        confidence=0.7,
    )
    return path


def _sync_visible_pages(
    wiki_dir: Path,
    *,
    run_id: str = "",
    page_action: str = "reconcile",
) -> dict[str, int]:
    ensure_layout(wiki_dir)
    page_files = iter_visible_page_files(wiki_dir)
    page_deltas: list[dict[str, Any]] = []

    with get_session() as session:
        existing_pages = {row.slug: row for row in session.exec(select(WikiPage)).all()}
        seen_slugs: set[str] = set()
        created = 0
        updated = 0

        for path in page_files:
            record = _page_record_from_path(wiki_dir, path)
            slug = record["slug"]
            seen_slugs.add(slug)

            row = existing_pages.get(slug)
            if row is None:
                row = WikiPage(slug=slug, title=record["title"])
                created += 1
            else:
                updated += 1

            row.title = record["title"]
            row.file_path = record["file_path"]
            row.page_type = record["page_type"]
            row.domains = json.dumps(record["domains"])
            row.source_ids = json.dumps(record["source_ids"])
            row.status = record["status"]
            row.confidence = record["confidence"]
            row.updated_at = _utcnow()
            session.add(row)

            # Keep concept records aligned with the visible page layer.
            concept = session.get(ConceptRecord, slug)
            if concept is not None:
                concept.article_path = str(path)
                concept.article_status = record["status"]
                session.add(concept)

            # Rebuild frontmatter-derived domain membership for this page.
            existing_memberships = list(
                session.exec(
                    select(DomainMembership).where(
                        DomainMembership.page_slug == slug,
                        DomainMembership.source == "frontmatter",
                    )
                ).all()
            )
            for membership in existing_memberships:
                session.delete(membership)
            for domain in record["domains"]:
                session.add(
                    DomainMembership(
                        page_slug=slug,
                        domain=domain,
                        confidence=1.0,
                        source="frontmatter",
                    )
                )

            if run_id:
                page_deltas.append(
                    {
                        "page_slug": slug,
                        "action": page_action if slug not in existing_pages else "reconcile",
                        "page_type": record["page_type"],
                        "source_count": len(record["source_ids"]),
                    }
                )

        deleted = 0
        for slug, row in existing_pages.items():
            if slug in seen_slugs:
                continue
            session.delete(row)
            deleted += 1
            memberships = list(
                session.exec(
                    select(DomainMembership).where(DomainMembership.page_slug == slug)
                ).all()
            )
            for membership in memberships:
                session.delete(membership)

        session.commit()

    if run_id:
        for delta in page_deltas:
            record_page_delta(run_id, **delta)

    return {
        "pages_seen": len(page_files),
        "pages_created": created,
        "pages_updated": updated,
        "pages_deleted": deleted,
    }


def reconcile_state(wiki_dir: Path = _WIKI_DIR) -> dict[str, Any]:
    """Rebuild the operational page layer from visible markdown files."""
    run_id = begin_run(
        workflow_type="reconcile",
        status="pending",
        strategy_id="visible_operational_sync",
        prompt_family="wiki_runtime_v1",
        model_tier="none",
    )
    sync_stage = stage_timer(run_id, "reconcile_visible_pages")
    counts = _sync_visible_pages(wiki_dir, run_id=run_id, page_action="reconcile")
    sync_stage.finish(**counts)
    metrics = snapshot_wiki_metrics(wiki_dir, run_id)
    summary = {
        "workflow_type": "reconcile",
        "run_id": run_id,
        **counts,
        "metric_count": len(metrics),
    }
    finish_run(wiki_dir, run_id, status="reconciled", headline="Reconcile state", summary=summary)
    return summary


def run_maintain(wiki_dir: Path = _WIKI_DIR) -> dict[str, Any]:
    """Run a maintenance sweep over visible pages and operational evidence."""
    ensure_layout(wiki_dir)
    run_id = begin_run(
        workflow_type="maintain",
        status="pending",
        strategy_id="visible_operational_maintenance",
        prompt_family="wiki_runtime_v1",
        model_tier="none",
    )
    reconcile_counts = _sync_visible_pages(wiki_dir, run_id=run_id, page_action="reconcile")
    stage = stage_timer(run_id, "maintenance_audit")

    page_files = iter_visible_page_files(wiki_dir)
    page_records = [_page_record_from_path(wiki_dir, path) for path in page_files]
    page_slugs = {record["slug"] for record in page_records}
    inbound_links: Counter[str] = Counter()
    findings: list[MaintenanceFinding] = []
    covered_sources: set[str] = set()

    with get_session() as session:
        # Treat maintenance findings as a rebuildable operational layer.
        for row in list(session.exec(select(MaintenanceFinding)).all()):
            session.delete(row)
        session.commit()

        provenance_rows = list(session.exec(select(PageProvenance)).all())
        provenance_by_slug: dict[str, list[PageProvenance]] = defaultdict(list)
        for row in provenance_rows:
            provenance_by_slug[row.page_slug].append(row)
            if row.paper_id:
                covered_sources.add(row.paper_id)

        for record in page_records:
            covered_sources.update(record["source_ids"])
            text = record["path"].read_text(encoding="utf-8", errors="replace")
            out_links = [slugify(match.strip()) for match in _WIKILINK_RE.findall(text)]
            for target in out_links:
                inbound_links[target] += 1
                if target not in page_slugs:
                    findings.append(
                        MaintenanceFinding(
                            page_slug=record["slug"],
                            finding_type="broken_link",
                            severity="warn",
                            details=f"Missing target: {target}",
                            status="open",
                        )
                    )

            if _WARNING_RE.search(text):
                findings.append(
                    MaintenanceFinding(
                        page_slug=record["slug"],
                        finding_type="contradiction",
                        severity="warn",
                        details="WARNING marker found in page body",
                        status="open",
                    )
                )

            provenance_count = len(provenance_by_slug.get(record["slug"], []))
            if provenance_count == 0 and len(record["source_ids"]) < 3:
                findings.append(
                    MaintenanceFinding(
                        page_slug=record["slug"],
                        finding_type="weak_support",
                        severity="warn",
                        details="No operational provenance and fewer than 3 listed sources",
                        status="open",
                    )
                )
            if provenance_count > 15 or len(record["source_ids"]) > 15:
                findings.append(
                    MaintenanceFinding(
                        page_slug=record["slug"],
                        finding_type="split_candidate",
                        severity="info",
                        details="Page may be too broad for a single article",
                        status="open",
                    )
                )

        for record in page_records:
            if inbound_links.get(record["slug"], 0) == 0:
                findings.append(
                    MaintenanceFinding(
                        page_slug=record["slug"],
                        finding_type="orphan_page",
                        severity="info",
                        details="No inbound wiki links detected",
                        status="open",
                    )
                )

        papers = list(session.exec(select(Paper)).all())
        orphan_source_ids = [paper.id for paper in papers if paper.id not in covered_sources]
        for source_id in orphan_source_ids:
            findings.append(
                MaintenanceFinding(
                    page_slug="__wiki__",
                    finding_type="orphan_source",
                    severity="info",
                    details=source_id,
                    status="open",
                )
            )

        type_counts = Counter(f.finding_type for f in findings)

        for finding in findings:
            session.add(finding)
        session.commit()

    stage.finish(
        pages_seen=len(page_records),
        findings=len(findings),
        broken_links=type_counts.get("broken_link", 0),
        weak_support=type_counts.get("weak_support", 0),
        orphan_pages=type_counts.get("orphan_page", 0),
        orphan_sources=type_counts.get("orphan_source", 0),
    )
    metrics = snapshot_wiki_metrics(wiki_dir, run_id)
    summary = {
        "workflow_type": "maintain",
        "run_id": run_id,
        **reconcile_counts,
        "pages_seen": len(page_records),
        "findings": len(findings),
        "finding_types": dict(type_counts),
        "metric_count": len(metrics),
    }
    finish_run(wiki_dir, run_id, status="applied", headline="Maintain wiki", summary=summary)
    return summary


def _aggregate_runs(*, workflow_type: str = "", limit: int = 20) -> list[dict[str, Any]]:
    with get_session() as session:
        run_rows = list(session.exec(select(RunLog)).all())
        stage_rows = list(session.exec(select(StageTelemetry)).all())
        token_rows = list(session.exec(select(TokenUsageTelemetry)).all())
        retrieval_rows = list(session.exec(select(RetrievalTelemetry)).all())
        page_rows = list(session.exec(select(PageDeltaTelemetry)).all())
        metric_rows = list(session.exec(select(WikiSnapshotMetric)).all())

    if workflow_type:
        run_rows = [row for row in run_rows if row.workflow_type == workflow_type]
    run_rows.sort(
        key=lambda row: row.started_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    run_rows = run_rows[:limit]

    by_run: dict[str, dict[str, Any]] = {}
    for row in run_rows:
        try:
            summary = json.loads(row.summary_json or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            summary = {}
        by_run[row.id] = {
            "run_id": row.id,
            "workflow_type": row.workflow_type,
            "status": row.status,
            "strategy_id": row.strategy_id,
            "loss_definition_id": row.loss_definition_id,
            "prompt_family": row.prompt_family,
            "model_tier": row.model_tier,
            "model_name": row.model_name,
            "started_at": row.started_at.isoformat() if row.started_at else "",
            "completed_at": row.completed_at.isoformat() if row.completed_at else "",
            "summary": summary,
            "stages": [],
            "metrics": {},
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "pages_touched": 0,
            "chunks_read": 0,
            "chunks_selected": 0,
            "raw_fallback_rate": 0.0,
        }

    for row in stage_rows:
        if row.run_id in by_run:
            by_run[row.run_id]["stages"].append(
                {
                    "stage_name": row.stage_name,
                    "duration_s": row.duration_s,
                    "counts": row.counts_json,
                }
            )

    fallback_counts: Counter[str] = Counter()
    retrieval_counts: Counter[str] = Counter()
    for row in retrieval_rows:
        if row.run_id not in by_run:
            continue
        by_run[row.run_id]["chunks_read"] += row.chunks_read
        by_run[row.run_id]["chunks_selected"] += row.chunks_selected
        retrieval_counts[row.run_id] += 1
        if row.raw_fallback_used:
            fallback_counts[row.run_id] += 1

    for row in token_rows:
        if row.run_id in by_run:
            by_run[row.run_id]["total_tokens"] += row.total_tokens
            by_run[row.run_id]["estimated_cost_usd"] += row.estimated_cost_usd

    for row in page_rows:
        if row.run_id in by_run:
            by_run[row.run_id]["pages_touched"] += 1

    for row in metric_rows:
        if row.run_id in by_run:
            by_run[row.run_id]["metrics"][row.metric_name] = row.metric_value

    for run_id, payload in by_run.items():
        total = retrieval_counts.get(run_id, 0)
        payload["raw_fallback_rate"] = fallback_counts.get(run_id, 0) / total if total else 0.0

    return [by_run[row.id] for row in run_rows if row.id in by_run]


def export_metrics(
    wiki_dir: Path = _WIKI_DIR,
    *,
    workflow_type: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Export aggregated run and metric data for later analysis."""
    ensure_layout(wiki_dir)
    runs = _aggregate_runs(workflow_type=workflow_type, limit=limit)
    payload = {
        "generated_at": _utcnow().isoformat(),
        "workflow_type": workflow_type,
        "run_count": len(runs),
        "runs": runs,
    }
    out_path = metrics_dir(wiki_dir) / "export.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"export_path": str(out_path), **payload}


def compare_runs(
    wiki_dir: Path = _WIKI_DIR,
    *,
    workflow_type: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Compare recent runs on cost, retrieval effort, and wiki outcome metrics."""
    ensure_layout(wiki_dir)
    runs = _aggregate_runs(workflow_type=workflow_type, limit=limit)
    comparison_rows: list[dict[str, Any]] = []
    for run in runs:
        metrics = run.get("metrics", {})
        comparison_rows.append(
            {
                "run_id": run["run_id"],
                "workflow_type": run["workflow_type"],
                "status": run["status"],
                "model_name": run["model_name"],
                "strategy_id": run["strategy_id"],
                "total_tokens": run["total_tokens"],
                "estimated_cost_usd": round(run["estimated_cost_usd"], 6),
                "pages_touched": run["pages_touched"],
                "chunks_read": run["chunks_read"],
                "chunks_selected": run["chunks_selected"],
                "raw_fallback_rate": run["raw_fallback_rate"],
                "article_count": metrics.get("article_count", 0.0),
                "link_count": metrics.get("link_count", 0.0),
                "orphan_count": metrics.get("orphan_count", 0.0),
                "evidence_density": metrics.get("evidence_density", 0.0),
                "weak_support_count": metrics.get("weak_support_count", 0.0),
                "contradiction_count": metrics.get("contradiction_count", 0.0),
                "summary": run.get("summary", {}),
            }
        )

    payload = {
        "generated_at": _utcnow().isoformat(),
        "workflow_type": workflow_type,
        "run_count": len(comparison_rows),
        "runs": comparison_rows,
    }
    out_path = metrics_dir(wiki_dir) / "comparison.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"comparison_path": str(out_path), **payload}


def query_wiki(
    question: str,
    *,
    wiki_dir: Path = _WIKI_DIR,
    domain: str = "",
    model: str | None = None,
    promote: bool = False,
    page_type: str = "query",
    promotion_wiki_dir: Path | None = None,
) -> dict[str, Any]:
    """Answer a question from the visible wiki with optional promotion."""
    ensure_layout(wiki_dir)
    target_wiki_dir = promotion_wiki_dir or wiki_dir
    ensure_layout(target_wiki_dir)
    artifact_wiki_dir = target_wiki_dir
    run_id = begin_run(
        workflow_type="query",
        status="pending",
        strategy_id="visible_query_v1",
        prompt_family="wiki_query_v1",
        model_tier="balanced",
        model_name=model or "",
    )
    reconcile_counts = _sync_visible_pages(wiki_dir, run_id=run_id, page_action="reconcile")
    retrieval_stage = stage_timer(run_id, "query_retrieval")
    candidates = _search_visible_pages(wiki_dir, question, domain=domain, top_k=5)
    page_count = len(candidates)
    unique_sources = sorted({sid for record in candidates for sid in record["source_ids"]})
    record_retrieval(
        run_id,
        stage_name="query_retrieval",
        query=question,
        candidates_considered=page_count,
        chunks_read=page_count,
        chunks_selected=page_count,
        pages_read=page_count,
        raw_fallback_used=False,
        domains=[domain] if domain else [],
    )
    retrieval_stage.finish(pages_read=page_count, source_count=len(unique_sources))

    if not candidates:
        append_unanswered_question(target_wiki_dir, question, domain)
        summary = {
            "workflow_type": "query",
            "run_id": run_id,
            **reconcile_counts,
            "question": question,
            "answered": False,
            "pages_read": 0,
            "promoted_path": "",
        }
        finish_run(
            artifact_wiki_dir,
            run_id,
            status="applied",
            headline=f"Query: {question[:60]}",
            summary=summary,
        )
        return summary

    synthesis_stage = stage_timer(run_id, "query_synthesis")
    context_blocks = []
    for record in candidates:
        context_blocks.append(
            "\n".join(
                [
                    f"Title: {record['title']}",
                    f"Slug: {record['slug']}",
                    f"Page type: {record['page_type']}",
                    f"Domains: {', '.join(record['domains']) or 'none'}",
                    f"Sources: {', '.join(record['source_ids']) or 'none'}",
                    "",
                    record["body"],
                ]
            )
        )

    started = time.monotonic()
    answer = complete(
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer the user's question using the provided wiki pages. "
                    "Prefer concise, synthesis-first answers and mention uncertainty when needed."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    "Wiki context:\n\n"
                    + "\n\n---\n\n".join(context_blocks)
                ),
            },
        ],
        model=model,
        temperature=0.1,
        max_tokens=1200,
        use_cache=False,
    ).strip()
    record_tool_call(
        run_id,
        tool_name="llm.complete",
        stage_name="query_synthesis",
        latency_ms=(time.monotonic() - started) * 1000.0,
        input_summary=f"question={question[:80]} pages={page_count}",
        output_summary=f"answer_chars={len(answer)}",
    )
    synthesis_stage.finish(answer_chars=len(answer), pages_read=page_count)

    promoted_path = ""
    if promote:
        promoted_domains = (
            [domain]
            if domain
            else sorted({d for record in candidates for d in record["domains"]})
        )
        promoted = _promote_answer_page(
            target_wiki_dir,
            title=question,
            answer=answer,
            source_ids=unique_sources,
            domains=promoted_domains,
            model=model or "",
            page_type=page_type,
        )
        promoted_path = str(promoted)
        _sync_visible_pages(target_wiki_dir, run_id=run_id, page_action="promote")

    metrics = snapshot_wiki_metrics(artifact_wiki_dir, run_id)
    summary = {
        "workflow_type": "query",
        "run_id": run_id,
        **reconcile_counts,
        "question": question,
        "answered": True,
        "pages_read": page_count,
        "source_count": len(unique_sources),
        "page_slugs": [record["slug"] for record in candidates],
        "promoted_path": promoted_path,
        "metric_count": len(metrics),
    }
    finish_run(
        artifact_wiki_dir,
        run_id,
        status="applied",
        headline=f"Query: {question[:60]}",
        summary=summary,
    )
    return {**summary, "answer": answer}


def run_campaign(
    thesis: str,
    *,
    wiki_dir: Path = _WIKI_DIR,
    name: str = "",
    domain: str = "",
    epochs: int = 1,
    model: str | None = None,
    promote: bool = True,
    extractor: AgentExtractor | None = None,
    allow_echo_extractor: bool = False,
) -> dict[str, Any]:
    """Run a focused thesis-driven campaign over the shared wiki substrate."""
    from wikify.wiki.epoch import run_epoch

    ensure_layout(wiki_dir)
    campaign_id = slugify(name or thesis[:80]) or "campaign"
    run_id = begin_run(
        workflow_type="campaign",
        status="pending",
        strategy_id="campaign_v1",
        prompt_family="wiki_campaign_v1",
        model_tier="balanced",
        model_name=model or "",
    )
    stage = stage_timer(run_id, "campaign_execution")

    epoch_logs = []
    for _ in range(max(epochs, 1)):
        epoch_logs.append(
            run_epoch(
                triggered_by="campaign",
                domain=domain,
                model=model,
                extractor=extractor,
                allow_echo_extractor=allow_echo_extractor,
            )
        )

    query_result = query_wiki(
        thesis,
        wiki_dir=wiki_dir,
        domain=domain,
        model=model,
        promote=promote,
        page_type="query",
    )

    with get_session() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            campaign = Campaign(id=campaign_id, name=name or thesis[:80], thesis=thesis)
        campaign.status = "concluded" if query_result.get("answered") else "investigating"
        campaign.confidence = 0.7 if query_result.get("answered") else 0.3
        campaign.epochs_run += len(epoch_logs)
        answer = str(query_result.get("answer", "")).strip()
        campaign.findings = json.dumps([answer[:500]] if answer else [])
        campaign.open_gaps = json.dumps([] if query_result.get("answered") else [thesis])
        campaign.concept_ids = json.dumps(query_result.get("page_slugs", []))
        campaign.paper_ids = json.dumps([])
        campaign.synthesis_path = str(query_result.get("promoted_path", ""))
        campaign.updated_at = _utcnow()
        session.add(campaign)
        session.commit()

    stage.finish(epochs_run=len(epoch_logs), answered=bool(query_result.get("answered")))
    metrics = snapshot_wiki_metrics(wiki_dir, run_id)
    summary = {
        "workflow_type": "campaign",
        "run_id": run_id,
        "campaign_id": campaign_id,
        "thesis": thesis,
        "epochs_run": len(epoch_logs),
        "answered": bool(query_result.get("answered")),
        "promoted_path": query_result.get("promoted_path", ""),
        "metric_count": len(metrics),
    }
    finish_run(
        wiki_dir,
        run_id,
        status="applied",
        headline=f"Campaign: {campaign_id}",
        summary=summary,
    )
    return {**summary, "answer": query_result.get("answer", "")}
