"""Run-scoped telemetry helpers for wiki workflows."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlmodel import select

from wikify.store.db import get_session
from wikify.store.models import (
    ConceptOccurrence,
    DomainMembership,
    ExperimentTag,
    GraphEdge,
    LossDefinitionResult,
    MaintenanceFinding,
    PageDeltaTelemetry,
    PageProvenance,
    RetrievalTelemetry,
    RunLog,
    RunTelemetry,
    StageTelemetry,
    TokenUsageTelemetry,
    ToolCallTelemetry,
    WikiSnapshotMetric,
)
from wikify.wiki.layout import (
    ensure_layout,
    index_path,
    iter_visible_page_files,
    log_path,
    metrics_dir,
    runs_dir,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id(workflow_type: str) -> str:
    prefix = workflow_type.lower().strip() or "run"
    return f"{prefix}-{_utcnow().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"


@dataclass(slots=True)
class StageTimer:
    """Small in-memory timer for one run stage."""

    run_id: str
    stage_name: str
    started_at: datetime = field(default_factory=_utcnow)
    _t0: float = field(default_factory=time.monotonic)

    def finish(self, **counts: int | float | bool) -> StageTelemetry:
        completed_at = _utcnow()
        duration_s = time.monotonic() - self._t0
        row = StageTelemetry(
            run_id=self.run_id,
            stage_name=self.stage_name,
            started_at=self.started_at,
            completed_at=completed_at,
            duration_s=duration_s,
            counts_json=json.dumps(counts, ensure_ascii=False),
        )
        with get_session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row


def begin_run(
    *,
    workflow_type: str,
    status: str = "pending",
    strategy_id: str = "",
    loss_definition_id: str = "",
    prompt_family: str = "",
    model_tier: str = "",
    model_name: str = "",
) -> str:
    """Create a new run log and telemetry envelope."""
    run_id = new_run_id(workflow_type)
    started_at = _utcnow()
    run_log = RunLog(
        id=run_id,
        workflow_type=workflow_type,
        status=status,
        strategy_id=strategy_id,
        loss_definition_id=loss_definition_id,
        prompt_family=prompt_family,
        model_tier=model_tier,
        model_name=model_name,
        started_at=started_at,
    )
    run_telemetry = RunTelemetry(
        run_id=run_id,
        workflow_type=workflow_type,
        status=status,
        strategy_id=strategy_id,
        loss_definition_id=loss_definition_id,
        prompt_family=prompt_family,
        model_tier=model_tier,
        model_name=model_name,
        started_at=started_at,
    )
    with get_session() as session:
        session.add(run_log)
        session.add(run_telemetry)
        session.commit()
    return run_id


def update_run_metadata(run_id: str, **fields: str) -> None:
    """Update descriptive metadata on the run tables."""
    with get_session() as session:
        run_log = session.get(RunLog, run_id)
        run_telem = session.exec(select(RunTelemetry).where(RunTelemetry.run_id == run_id)).first()
        for row in (run_log, run_telem):
            if row is None:
                continue
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
                session.add(row)
        session.commit()


def stage_timer(run_id: str, stage_name: str) -> StageTimer:
    return StageTimer(run_id=run_id, stage_name=stage_name)


def record_tool_call(
    run_id: str,
    *,
    tool_name: str,
    stage_name: str = "",
    status: str = "ok",
    latency_ms: float = 0.0,
    input_summary: str = "",
    output_summary: str = "",
) -> None:
    with get_session() as session:
        session.add(
            ToolCallTelemetry(
                run_id=run_id,
                stage_name=stage_name,
                tool_name=tool_name,
                status=status,
                latency_ms=latency_ms,
                input_summary=input_summary,
                output_summary=output_summary,
            )
        )
        session.commit()


def record_retrieval(
    run_id: str,
    *,
    stage_name: str,
    query: str,
    candidates_considered: int = 0,
    chunks_read: int = 0,
    chunks_selected: int = 0,
    pages_read: int = 0,
    raw_fallback_used: bool = False,
    domains: list[str] | None = None,
) -> None:
    with get_session() as session:
        session.add(
            RetrievalTelemetry(
                run_id=run_id,
                stage_name=stage_name,
                query=query,
                candidates_considered=candidates_considered,
                chunks_read=chunks_read,
                chunks_selected=chunks_selected,
                pages_read=pages_read,
                raw_fallback_used=raw_fallback_used,
                domains_json=json.dumps(domains or [], ensure_ascii=False),
            )
        )
        session.commit()


def record_tokens(
    run_id: str,
    *,
    stage_name: str,
    model_name: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
) -> None:
    total_tokens = prompt_tokens + completion_tokens + cached_tokens + reasoning_tokens
    with get_session() as session:
        session.add(
            TokenUsageTelemetry(
                run_id=run_id,
                stage_name=stage_name,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
        )
        session.commit()


def record_page_delta(
    run_id: str,
    *,
    page_slug: str,
    action: str,
    page_type: str,
    source_count: int = 0,
    link_delta: int = 0,
) -> None:
    with get_session() as session:
        session.add(
            PageDeltaTelemetry(
                run_id=run_id,
                page_slug=page_slug,
                action=action,
                page_type=page_type,
                source_count=source_count,
                link_delta=link_delta,
            )
        )
        session.commit()


def record_experiment_tags(run_id: str, tags: dict[str, str]) -> None:
    if not tags:
        return
    with get_session() as session:
        for key, value in tags.items():
            session.add(ExperimentTag(run_id=run_id, tag_key=key, tag_value=value))
        session.commit()


def record_loss_components(
    run_id: str,
    *,
    loss_name: str,
    components: dict[str, tuple[float, float]],
) -> None:
    with get_session() as session:
        for component, (value, weight) in components.items():
            session.add(
                LossDefinitionResult(
                    run_id=run_id,
                    loss_name=loss_name,
                    component=component,
                    value=value,
                    weight=weight,
                )
            )
        session.commit()


def _count_wikilinks(text: str) -> int:
    return text.count("[[")


def snapshot_wiki_metrics(wiki_dir: Path, run_id: str) -> dict[str, float]:
    """Persist a lightweight wiki evolution snapshot for one run."""
    ensure_layout(wiki_dir)
    page_files = iter_visible_page_files(wiki_dir)
    page_type_counter: Counter[str] = Counter()
    total_links = 0
    orphan_count = 0
    source_note_count = 0

    link_targets: set[str] = set()
    slugs = {path.stem for path in page_files}

    for path in page_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter = _parse_frontmatter(path)
        page_type = str(frontmatter.get("page_type") or frontmatter.get("type") or "concept")
        page_type_counter[page_type] += 1
        if page_type == "source-note":
            source_note_count += 1
        total_links += _count_wikilinks(text)
        for candidate in slugs:
            if f"[[{candidate}]]" in text:
                link_targets.add(candidate)

    orphan_count = sum(1 for slug in slugs if slug not in link_targets)

    with get_session() as session:
        graph_edge_count = len(list(session.exec(select(GraphEdge)).all()))
        weak_support_count = len(
            list(session.exec(select(MaintenanceFinding).where(MaintenanceFinding.finding_type == "weak_support")).all())
        )
        contradiction_count = len(
            list(session.exec(select(MaintenanceFinding).where(MaintenanceFinding.finding_type == "contradiction")).all())
        )
        unresolved_gap_count = len(
            list(session.exec(select(MaintenanceFinding).where(MaintenanceFinding.finding_type == "gap")).all())
        )
        cross_domain_edge_count = len(
            list(
                session.exec(
                    select(GraphEdge).where(GraphEdge.is_cross_domain == True)  # noqa: E712
                ).all()
            )
        )
        provenance_rows = len(list(session.exec(select(PageProvenance)).all()))
        page_rows = len(page_files)

    metrics: dict[str, float] = {
        "article_count": float(page_rows - source_note_count),
        "source_note_count": float(source_note_count),
        "link_count": float(total_links),
        "orphan_count": float(orphan_count),
        "graph_edge_count": float(graph_edge_count),
        "cross_domain_edge_ratio": float(cross_domain_edge_count / graph_edge_count)
        if graph_edge_count
        else 0.0,
        "evidence_density": float(provenance_rows / page_rows) if page_rows else 0.0,
        "weak_support_count": float(weak_support_count),
        "contradiction_count": float(contradiction_count),
        "unresolved_gap_count": float(unresolved_gap_count),
    }
    for page_type, count in sorted(page_type_counter.items()):
        metrics[f"page_type:{page_type}"] = float(count)

    measured_at = _utcnow()
    with get_session() as session:
        for name, value in metrics.items():
            session.add(
                WikiSnapshotMetric(
                    run_id=run_id,
                    metric_name=name,
                    metric_value=value,
                    measured_at=measured_at,
                )
            )
        session.commit()

    metrics_path = metrics_dir(wiki_dir) / f"{run_id}.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    return metrics


def append_log_entry(
    wiki_dir: Path,
    *,
    workflow_type: str,
    headline: str,
    summary_lines: list[str],
) -> None:
    ensure_layout(wiki_dir)
    path = log_path(wiki_dir)
    date = _utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"## [{date}] {workflow_type} | {headline}", ""]
    lines.extend(f"- {line}" for line in summary_lines if line)
    lines.append("")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def finish_run(
    wiki_dir: Path,
    run_id: str,
    *,
    status: str,
    headline: str,
    summary: dict,
) -> None:
    """Mark a run complete and export a machine-readable summary."""
    ensure_layout(wiki_dir)
    completed_at = _utcnow()
    with get_session() as session:
        run_log = session.get(RunLog, run_id)
        run_telemetry = session.exec(select(RunTelemetry).where(RunTelemetry.run_id == run_id)).first()
        for row in (run_log, run_telemetry):
            if row is None:
                continue
            row.status = status
            row.completed_at = completed_at
            row.summary_json = json.dumps(summary, ensure_ascii=False)
            session.add(row)
        session.commit()

    run_summary_path = runs_dir(wiki_dir) / f"{run_id}.json"
    run_summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    append_log_entry(
        wiki_dir,
        workflow_type=str(summary.get("workflow_type", "")),
        headline=headline,
        summary_lines=[f"{key}: {value}" for key, value in summary.items() if key != "workflow_type"],
    )


def _parse_frontmatter(path: Path) -> dict:
    from wikify.wiki.builder import read_article_frontmatter

    return read_article_frontmatter(path)


def rebuild_index_stub(wiki_dir: Path) -> None:
    """Ensure the new visible-layer index/log files exist."""
    ensure_layout(wiki_dir)
    if not index_path(wiki_dir).exists():
        index_path(wiki_dir).write_text("# Knowledge Base Index\n", encoding="utf-8")
    if not log_path(wiki_dir).exists():
        log_path(wiki_dir).write_text("# Wiki Change Log\n\n", encoding="utf-8")
