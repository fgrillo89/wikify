"""Run lifecycle and stage timing helpers.

Owns:
- ``new_run_id``, ``begin_run``, ``update_run_metadata``
- ``StageTimer`` + ``stage_timer``
- per-stage counters: tool calls, retrieval, tokens, page deltas,
  experiment tags, loss components
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import (
    ExperimentTag,
    LossDefinitionResult,
    PageDeltaTelemetry,
    RetrievalTelemetry,
    RunLog,
    RunTelemetry,
    StageTelemetry,
    TokenUsageTelemetry,
    ToolCallTelemetry,
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

    def finish(self, **counts: object) -> StageTelemetry:
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
    with get_session() as session:
        run_log = session.get(RunLog, run_id)
        run_telem = session.exec(
            select(RunTelemetry).where(RunTelemetry.run_id == run_id)
        ).first()
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


__all__ = [
    "StageTimer",
    "begin_run",
    "new_run_id",
    "record_experiment_tags",
    "record_loss_components",
    "record_page_delta",
    "record_retrieval",
    "record_tokens",
    "record_tool_call",
    "stage_timer",
    "update_run_metadata",
]
