"""Run-completion export.

``finish_run`` marks a run complete in SQL, writes a machine-readable
JSON summary under ``data/wiki/_meta/runs/<run_id>.json``, and appends a
human-readable line to ``data/wiki/log.md``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import RunLog, RunTelemetry
from wikify.wiki.observability.logs import append_log_entry
from wikify.wiki.presentation.layout import ensure_layout, runs_dir


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def finish_run(
    wiki_dir: Path,
    run_id: str,
    *,
    status: str,
    headline: str,
    summary: dict,
) -> None:
    ensure_layout(wiki_dir)
    completed_at = _utcnow()
    with get_session() as session:
        run_log = session.get(RunLog, run_id)
        run_telemetry = session.exec(
            select(RunTelemetry).where(RunTelemetry.run_id == run_id)
        ).first()
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
        summary_lines=[
            f"{key}: {value}" for key, value in summary.items() if key != "workflow_type"
        ],
    )


__all__ = ["finish_run"]
