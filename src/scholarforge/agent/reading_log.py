"""Reading log — tracks which papers the agent read and why.

Produces a human-readable trace of the research process so users can
understand what was read, why, and how each step contributed to the output.
Users can review this log and instruct the agent to read specific papers
or change its exploration strategy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scholarforge.agent.run_context import (
    default_reading_log_file,
    get_current_run_context,
)


@dataclass
class ReadingEntry:
    """A single reading action in the research process."""

    paper: str  # display_name or search query
    tool: str  # which tool was used (deep_read, read_paper_digest, search_papers, etc.)
    reason: str  # why the agent chose to read this
    contribution: str = ""  # one-liner: what this read added to the review (filled after writing)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    depth: str = "digest"  # "digest", "full", "search", "section"


@dataclass
class ReadingLog:
    """Accumulates reading entries during a generation session."""

    entries: list[ReadingEntry] = field(default_factory=list)
    strategy: str = ""
    topic: str = ""

    def log(
        self,
        paper: str,
        tool: str,
        reason: str,
        depth: str = "digest",
    ) -> None:
        """Record a reading action (also persists to disk for cross-process use)."""
        ctx = get_current_run_context()
        key = f"{paper}::{depth}"
        if key in ctx.reading_log_seen:
            return
        entry = ReadingEntry(paper=paper, tool=tool, reason=reason, depth=depth)
        self.entries.append(entry)
        _persist_entry(entry)

    def to_markdown(self) -> str:
        """Render the log as a human-readable markdown document."""
        lines = ["# Reading Log", ""]
        if self.strategy:
            lines.append(f"**Strategy**: {self.strategy}")
        if self.topic:
            lines.append(f"**Topic**: {self.topic}")
        lines += [f"**Papers read**: {len(self.entries)}", ""]

        # Summary table
        lines += [
            "| # | Paper | Depth | Reason |",
            "|---|-------|-------|--------|",
        ]
        for i, entry in enumerate(self.entries, 1):
            reason_short = entry.reason[:80] + "..." if len(entry.reason) > 80 else entry.reason
            lines.append(f"| {i} | {entry.paper} | {entry.depth} | {reason_short} |")

        # Detailed entries
        lines += ["", "## Detailed Log", ""]
        for i, entry in enumerate(self.entries, 1):
            lines.append(f"### {i}. {entry.paper}")
            lines.append(f"- **Tool**: `{entry.tool}`")
            lines.append(f"- **Depth**: {entry.depth}")
            lines.append(f"- **Reason**: {entry.reason}")
            if entry.contribution:
                lines.append(f"- **Contribution**: {entry.contribution}")
            lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Serialize the log as JSON."""
        return json.dumps(
            {
                "strategy": self.strategy,
                "topic": self.topic,
                "entries": [
                    {
                        "paper": e.paper,
                        "tool": e.tool,
                        "reason": e.reason,
                        "contribution": e.contribution,
                        "depth": e.depth,
                        "timestamp": e.timestamp,
                    }
                    for e in self.entries
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    def save(self, output_dir: str | Path, basename: str = "reading_log") -> Path:
        """Write both .md and .json versions to the output directory."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / f"{basename}.md"
        md_path.write_text(self.to_markdown(), encoding="utf-8")
        json_path = out / f"{basename}.json"
        json_path.write_text(self.to_json(), encoding="utf-8")
        return md_path


# ── File-backed session state (no globals — uses container class) ────────────


def configure_reading_log(log_file: str | Path | None = None) -> Path:
    """Set the backing JSONL path for subsequent reading-log operations.

    This is a bridge toward fully run-scoped state: callers can now isolate
    log persistence per run instead of sharing one fixed process-wide path.
    Existing in-memory state is cleared so the next access reloads from the
    configured path.
    """
    ctx = get_current_run_context()
    ctx.reading_log_file = Path(log_file) if log_file is not None else default_reading_log_file()
    ctx.reading_log = ReadingLog(strategy=ctx.strategy, topic=ctx.topic)
    ctx.reading_log_seen = set()
    ctx.reading_log_loaded = False
    return ctx.reading_log_file


def get_reading_log() -> ReadingLog:
    """Get or create the current session's reading log.

    The log is backed by a JSONL file so entries persist across
    separate Python process invocations (each `uv run python -c` call).
    Deduplicates: same paper + same depth = logged only once.
    """
    ctx = get_current_run_context()
    if not ctx.reading_log_loaded:
        ctx.reading_log.entries = []
        ctx.reading_log.strategy = ctx.strategy
        ctx.reading_log.topic = ctx.topic
        ctx.reading_log_seen = set()
        if ctx.reading_log_file.exists():
            try:
                for line in ctx.reading_log_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        entry = ReadingEntry(**data)
                        key = f"{entry.paper}::{entry.depth}"
                        if key not in ctx.reading_log_seen:
                            ctx.reading_log.entries.append(entry)
                            ctx.reading_log_seen.add(key)
            except Exception:  # noqa: BLE001
                pass
        ctx.reading_log_loaded = True
    return ctx.reading_log


def _persist_entry(entry: ReadingEntry) -> None:
    """Append a single entry to the JSONL file (deduplicated)."""
    ctx = get_current_run_context()
    key = f"{entry.paper}::{entry.depth}"
    if key in ctx.reading_log_seen:
        return
    ctx.reading_log_seen.add(key)

    ctx.reading_log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(ctx.reading_log_file, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "paper": entry.paper,
                    "tool": entry.tool,
                    "reason": entry.reason,
                    "contribution": entry.contribution,
                    "timestamp": entry.timestamp,
                    "depth": entry.depth,
                }
            )
            + "\n"
        )


def reset_reading_log(log_file: str | Path | None = None) -> ReadingLog:
    """Start a fresh reading log (e.g., for a new generation run)."""
    ctx = get_current_run_context()
    if log_file is not None:
        configure_reading_log(log_file)
    elif ctx.reading_log_file == Path("data/output") / ".reading_log.jsonl":
        # Migrate old default lazily for existing long-lived interpreters.
        ctx.reading_log_file = default_reading_log_file()
    ctx.reading_log = ReadingLog(strategy=ctx.strategy, topic=ctx.topic)
    ctx.reading_log_seen = set()
    ctx.reading_log_loaded = True
    if ctx.reading_log_file.exists():
        ctx.reading_log_file.unlink()
    return ctx.reading_log
