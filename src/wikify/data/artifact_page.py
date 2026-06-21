"""Render a consolidated table into a wiki data-artifact page.

A data-artifact page is structurally an ordinary wiki page: YAML frontmatter
(``kind: data``), a markdown table whose cells carry ``[^dN]`` markers, and a
``## References`` block in the standard evidence-footnote format. Because it
reuses that format, the existing HTML renderer turns the table into ``<table>``
and the reference aggregator folds the page's sources into ``references.html``
with no special plumbing.

A ``.dataspec.json`` sidecar stores the durable spec + backing claim ids so
``wikify data rebuild`` can re-derive the page from the current claim store.
"""

from __future__ import annotations

import json
from pathlib import Path

from .consolidate import ConsolidatedTable
from .models import ArtifactSpec


def _escape_cell(text: str) -> str:
    """Make a value safe inside a markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _cell_markdown(col: str, cell) -> str:
    if not cell.text:
        return ""
    if cell.conflict:
        # Conflict cells already embed their own [^dN] markers per value.
        return _escape_cell(cell.text)
    markers = "".join(f"[^{m}]" for m in cell.markers)
    return _escape_cell(cell.text) + markers


def render_artifact_markdown(table: ConsolidatedTable) -> str:
    """Return the full markdown body (frontmatter + table + references)."""
    page_id = table.title
    lines: list[str] = []
    lines.append("---")
    lines.append(f"id: {page_id}")
    lines.append("kind: data")
    lines.append(f"title: {page_id}")
    lines.append("aliases: []")
    lines.append("links: []")
    lines.append("---")
    lines.append("")
    lines.append(f"# {page_id}")
    lines.append("")
    if table.description:
        lines.append(table.description.strip())
        lines.append("")

    header = ["Subject", *table.columns]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in table.rows:
        cells = [_escape_cell(row["subject"])]
        for col in table.columns:
            cells.append(_cell_markdown(col, row["cells"][col]))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    if table.n_conflicts:
        lines.append(
            f"*{table.n_conflicts} cell(s) report conflicting values across "
            "sources; each reported value is shown with its citation.*"
        )
        lines.append("")

    lines.append("## References")
    lines.append("")
    for ev in table.evidence:
        chunk_id = ev["chunk_id"] or ev["doc_id"]
        doc_id = ev["doc_id"]
        locator = ev.get("locator") or ""
        head = f"{chunk_id} ({doc_id}, {locator})" if locator else f"{chunk_id} ({doc_id})"
        quote = ev["quote"].replace("\n", " ").strip()
        lines.append(f'[^{ev["marker"]}]: {head} > "{quote}"')
    lines.append("")
    return "\n".join(lines)


def build_sidecar(spec: ArtifactSpec, table: ConsolidatedTable) -> dict:
    return {
        "artifact_id": spec.artifact_id,
        "spec": json.loads(spec.to_json()),
        "claim_ids": table.claim_ids,
        "n_rows": table.n_rows,
        "n_conflicts": table.n_conflicts,
    }


def write_artifact_page(
    wiki_data_dir: Path,
    spec: ArtifactSpec,
    table: ConsolidatedTable,
) -> Path:
    """Write ``<title>.md`` + ``<title>.dataspec.json`` under *wiki_data_dir*.

    Returns the path to the markdown page.
    """
    from ..bundle.wiki.page_naming import page_filename, page_id_from_title

    wiki_data_dir.mkdir(parents=True, exist_ok=True)
    page_id = page_id_from_title(table.title)
    md_path = wiki_data_dir / page_filename(page_id)
    md_path.write_text(render_artifact_markdown(table), encoding="utf-8")
    sidecar = md_path.with_suffix(".dataspec.json")
    sidecar.write_text(
        json.dumps(build_sidecar(spec, table), indent=2), encoding="utf-8"
    )
    return md_path
