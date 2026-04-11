"""Wiki snapshot metrics.

Computes a lightweight evolution snapshot of the visible wiki for one
run: page-type counts, link counts, orphan/bridge counts, weak-support /
contradiction / unresolved-gap counts, and evidence density.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import (
    GraphEdge,
    MaintenanceFinding,
    PageProvenance,
    WikiSnapshotMetric,
)
from wikify.wiki.presentation.layout import (
    ensure_layout,
    iter_visible_page_files,
    metrics_dir,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _count_wikilinks(text: str) -> int:
    return text.count("[[")


def _parse_frontmatter(path: Path) -> dict:
    from wikify.wiki.builder import read_article_frontmatter

    return read_article_frontmatter(path)


def snapshot_wiki_metrics(wiki_dir: Path, run_id: str) -> dict[str, float]:
    """Persist a lightweight wiki evolution snapshot for one run."""

    ensure_layout(wiki_dir)
    page_files = iter_visible_page_files(wiki_dir)
    page_type_counter: Counter[str] = Counter()
    total_links = 0
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
            list(
                session.exec(
                    select(MaintenanceFinding).where(
                        MaintenanceFinding.finding_type == "weak_support"
                    )
                ).all()
            )
        )
        contradiction_count = len(
            list(
                session.exec(
                    select(MaintenanceFinding).where(
                        MaintenanceFinding.finding_type == "contradiction"
                    )
                ).all()
            )
        )
        unresolved_gap_count = len(
            list(
                session.exec(
                    select(MaintenanceFinding).where(MaintenanceFinding.finding_type == "gap")
                ).all()
            )
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
        "cross_domain_edge_ratio": (
            float(cross_domain_edge_count / graph_edge_count) if graph_edge_count else 0.0
        ),
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


__all__ = ["snapshot_wiki_metrics"]
