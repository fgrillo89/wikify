"""Run-scoped agent state for a single exploration or generation session."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from scholarforge.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from scholarforge.agent.concept_graph import ConceptGraph
    from scholarforge.agent.reading_log import ReadingLog


def _new_reading_log():
    from scholarforge.agent.reading_log import ReadingLog

    return ReadingLog()


def _new_concept_graph():
    from scholarforge.agent.concept_graph import ConceptGraph

    return ConceptGraph()


def default_reading_log_file() -> Path:
    """Default JSONL backing file for the active library."""
    return settings.data_dir / "output" / ".reading_log.jsonl"


@dataclass
class RunContext:
    """Mutable state owned by a single agent/scripted run."""

    run_id: str = field(default_factory=lambda: uuid4().hex[:8])
    topic: str = ""
    strategy: str = ""
    reading_log: ReadingLog = field(default_factory=_new_reading_log)
    reading_log_file: Path = field(default_factory=default_reading_log_file)
    reading_log_seen: set[str] = field(default_factory=set)
    reading_log_loaded: bool = False
    paper_summaries: list[dict] = field(default_factory=list)
    concept_graph: ConceptGraph = field(default_factory=_new_concept_graph)


_CURRENT_RUN_CONTEXT: ContextVar[RunContext | None] = ContextVar(
    "scholarforge_current_run_context",
    default=None,
)


def create_run_context(
    topic: str = "",
    strategy: str = "",
    log_file: str | Path | None = None,
) -> RunContext:
    """Create a fresh run context."""
    ctx = RunContext(
        topic=topic,
        strategy=strategy,
        reading_log_file=Path(log_file) if log_file is not None else default_reading_log_file(),
    )
    ctx.reading_log.topic = topic
    ctx.reading_log.strategy = strategy
    return ctx


def get_current_run_context() -> RunContext:
    """Return the active run context, creating a default one if needed."""
    ctx = _CURRENT_RUN_CONTEXT.get()
    if ctx is None:
        ctx = create_run_context()
        _CURRENT_RUN_CONTEXT.set(ctx)
    return ctx


def set_current_run_context(run_context: RunContext) -> Token:
    """Set the current run context for the calling context."""
    return _CURRENT_RUN_CONTEXT.set(run_context)


def restore_run_context(token: Token) -> None:
    """Restore the previous run context after a temporary override."""
    _CURRENT_RUN_CONTEXT.reset(token)


def reset_current_run_context() -> None:
    """Drop any ambient run context for the calling context."""
    _CURRENT_RUN_CONTEXT.set(None)


@contextmanager
def use_run_context(run_context: RunContext) -> Iterator[RunContext]:
    """Temporarily bind a run context for tools and workflow helpers."""
    token = set_current_run_context(run_context)
    try:
        yield run_context
    finally:
        restore_run_context(token)
