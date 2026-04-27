"""Run lifecycle verbs: init + close.

These are the orchestration helpers that the CLI commands call. Keeping
them out of ``cli/run.py`` lets tests exercise the lifecycle through
the fluent API without spinning Typer up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ...api import Bundle
from .events import Event, append_event
from .state import Budget, RunState, load_state, save_state, touch


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id() -> str:
    return f"run-{uuid4().hex[:8]}"


def init_run(
    bundle: Bundle,
    *,
    corpus_path: Path | str,
    strategy: str = "",
    target_haiku_eq: int = 0,
    actor: str = "cli",
    run_id: str | None = None,
) -> RunState:
    """Create the bundle directory layout, write the initial ``run/state.json``,
    and emit the first event.

    Returns the created :class:`RunState`. The caller is responsible
    for any subsequent state mutations under the lock.
    """
    bundle.ensure()
    state = RunState(
        run_id=run_id or _new_run_id(),
        strategy=strategy,
        corpus_path=str(corpus_path),
        budget=Budget(target_haiku_eq=target_haiku_eq),
    )
    save_state(bundle, state)
    append_event(
        bundle,
        Event(
            run_id=state.run_id,
            type="stage_changed",
            actor=actor,
            stage="init",
            data={"to": "active"},
        ),
    )
    return state


def close_run(
    bundle: Bundle,
    *,
    status: str = "completed",
    actor: str = "cli",
) -> RunState:
    """Mark the run finished and emit a ``run_closed`` event.

    Idempotent on ``status``: closing an already-closed run rewrites
    state.json (touching ``updated_at``) and emits a fresh
    ``run_closed`` event so the ledger records the second close.
    """
    if status not in {"completed", "failed", "abandoned"}:
        raise ValueError(
            f"close_run: status must be completed|failed|abandoned, got {status!r}"
        )
    state = load_state(bundle)
    new_state = touch(state.model_copy(update={"status": status}))
    save_state(bundle, new_state)
    append_event(
        bundle,
        Event(
            run_id=new_state.run_id,
            type="run_closed",
            actor=actor,
            data={"status": status, "closed_at": _utcnow()},
        ),
    )
    return new_state
