"""Replay and analyse KG exploration traces.

Reads the JSONL trace file produced by KnowledgeGraph.save_trace() and
provides stats, subgraph extraction, and per-strategy comparison.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TraceEntry:
    timestamp: str
    caller: str
    method: str
    args: dict = field(default_factory=dict)
    input_count: int = 0
    output_count: int = 0
    output_sample: list[str] = field(default_factory=list)


def load_trace(path: Path) -> list[TraceEntry]:
    """Load trace entries from JSONL."""
    entries: list[TraceEntry] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        entries.append(TraceEntry(
            timestamp=d.get("timestamp", ""),
            caller=d.get("caller", ""),
            method=d.get("method", ""),
            args=d.get("args", {}),
            input_count=d.get("input_count", 0),
            output_count=d.get("output_count", 0),
            output_sample=d.get("output_sample", []),
        ))
    return entries


def replay_stats(trace: list[TraceEntry]) -> dict:
    """Per-caller breakdown of KG usage."""
    calls_by_caller: dict[str, int] = Counter()
    methods_by_caller: dict[str, set[str]] = {}
    sources_visited: set[str] = set()
    queries: list[str] = []
    total_output = 0

    for e in trace:
        calls_by_caller[e.caller] += 1
        methods_by_caller.setdefault(e.caller, set()).add(e.method)
        sources_visited.update(e.output_sample)
        total_output += e.output_count
        if e.method == "search" and "query" in e.args:
            queries.append(e.args["query"])

    return {
        "total_calls": len(trace),
        "calls_by_caller": dict(calls_by_caller),
        "methods_by_caller": {k: sorted(v) for k, v in methods_by_caller.items()},
        "unique_nodes_visited": len(sources_visited),
        "total_output_nodes": total_output,
        "unique_queries": len(set(queries)),
        "queries": queries[:20],
    }


def exploration_timeline(trace: list[TraceEntry]) -> list[dict]:
    """Chronological summary of exploration steps."""
    return [
        {
            "step": i,
            "timestamp": e.timestamp,
            "caller": e.caller,
            "method": e.method,
            "in": e.input_count,
            "out": e.output_count,
            "sample": e.output_sample[:3],
        }
        for i, e in enumerate(trace)
    ]
