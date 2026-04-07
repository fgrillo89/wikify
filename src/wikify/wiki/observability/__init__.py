"""Wiki observability subsystem.

Owns run lifecycle telemetry, stage timings, snapshots, retrieval/tool/
token counters, and human-readable epoch logs. Today the implementation
lives in ``runs.py``; future slices may split it into ``stages.py``,
``snapshots.py``, ``logs.py``, and ``export.py`` per the target layout.
"""

from wikify.wiki.observability.runs import (
    StageTimer,
    append_log_entry,
    begin_run,
    finish_run,
    new_run_id,
    rebuild_index_stub,
    record_experiment_tags,
    record_loss_components,
    record_page_delta,
    record_retrieval,
    record_tokens,
    record_tool_call,
    snapshot_wiki_metrics,
    stage_timer,
    update_run_metadata,
)

__all__ = [
    "StageTimer",
    "append_log_entry",
    "begin_run",
    "finish_run",
    "new_run_id",
    "rebuild_index_stub",
    "record_experiment_tags",
    "record_loss_components",
    "record_page_delta",
    "record_retrieval",
    "record_tokens",
    "record_tool_call",
    "snapshot_wiki_metrics",
    "stage_timer",
    "update_run_metadata",
]
