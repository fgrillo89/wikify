"""Read/write wiki page .md files (frontmatter + body + evidence footnotes)."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Evidence, WikiPage
from ..paths import BundlePaths
from .page_naming import page_filename


def write_page(bundle: BundlePaths, page: WikiPage) -> Path:
    bundle.ensure()
    target_dir = bundle.concepts_dir if page.kind == "concept" else bundle.people_dir
    path = target_dir / page_filename(page.id)
    path.write_text(_render_page(page), encoding="utf-8")
    # Sidecar provenance JSON: the YAML frontmatter writer can only
    # serialise scalars cleanly, so we mirror the full provenance dict
    # to a sibling .provenance.json for the audit reader.
    if page.provenance:
        sidecar = path.with_suffix(".provenance.json")
        sidecar.write_text(json.dumps(page.provenance, indent=2, default=str), encoding="utf-8")
    return path


def _render_page(page: WikiPage) -> str:
    lines: list[str] = ["---"]
    lines.append(f"id: {page.id}")
    lines.append(f"kind: {page.kind}")
    lines.append(f"title: {page.title}")
    lines.append(f"aliases: [{', '.join(page.aliases)}]")
    lines.append(f"links: [{', '.join(page.links)}]")
    if page.kind == "person":
        lines.append("tags: [author]")
    if page.provenance:
        lines.append("provenance:")
        for k, v in page.provenance.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                lines.append(f"  {k}: {v}")
    lines.append("---")
    lines.append("")

    body = page.body_markdown.strip()

    # Don't prepend a title heading if the body already starts with one.
    if not body.startswith(f"# {page.title}"):
        lines.append(f"# {page.title}")
        lines.append("")

    lines.append(body)
    lines.append("")

    # Don't append an Evidence block if the body already has a References
    # section (the writer emits evidence as ## References; appending a
    # second block under ## Evidence produces duplicates).
    has_references = "## References" in body
    if page.evidence and not has_references:
        lines.append("## Evidence")
        lines.append("")
        for ev in page.evidence:
            lines.append(_render_evidence(ev))
    return "\n".join(lines) + "\n"


def _render_evidence(ev: Evidence) -> str:
    loc = f", {ev.locator}" if ev.locator else ""
    quote = ev.quote.replace('"', "'")
    return f'[^{ev.marker}]: {ev.chunk_id} ({ev.doc_id}{loc}) > "{quote}"'
