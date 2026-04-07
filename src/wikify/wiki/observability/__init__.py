"""Wiki observability subsystem.

Owns run lifecycle telemetry, stage timings, wiki snapshots, retrieval/
tool/token counters, human-readable epoch logs, and run completion
exports. Internal layout:

- ``stages``    : run lifecycle, stage timings, per-stage counters
- ``snapshots`` : wiki snapshot metrics for one run
- ``logs``      : human-readable wiki log writer
- ``export``    : run-completion JSON + log entry
"""

from wikify.wiki.observability.export import finish_run
from wikify.wiki.observability.logs import append_log_entry, rebuild_index_stub
from wikify.wiki.observability.snapshots import snapshot_wiki_metrics
from wikify.wiki.observability.stages import (
    StageTimer,
    begin_run,
    new_run_id,
    record_experiment_tags,
    record_loss_components,
    record_page_delta,
    record_retrieval,
    record_tokens,
    record_tool_call,
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
