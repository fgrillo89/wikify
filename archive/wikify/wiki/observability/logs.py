"""Human-readable wiki epoch log writer.

The log lives at ``data/wiki/log.md`` and accumulates short headlines
plus bulleted summaries per run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wikify.wiki.presentation.layout import ensure_layout, index_path, log_path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def rebuild_index_stub(wiki_dir: Path) -> None:
    """Ensure the visible-layer index and log files exist."""

    ensure_layout(wiki_dir)
    if not index_path(wiki_dir).exists():
        index_path(wiki_dir).write_text("# Knowledge Base Index\n", encoding="utf-8")
    if not log_path(wiki_dir).exists():
        log_path(wiki_dir).write_text("# Wiki Change Log\n\n", encoding="utf-8")


__all__ = ["append_log_entry", "rebuild_index_stub"]
