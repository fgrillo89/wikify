"""Read/write wiki page .md files (frontmatter + body + evidence footnotes)."""

import json
from pathlib import Path

from ...models import Evidence, WikiPage
from ...paths import BundlePaths
from .page_naming import page_filename


def write_page(bundle: BundlePaths, page: WikiPage) -> Path:
    bundle.ensure()
    target_dir = bundle.articles_dir if page.kind == "article" else bundle.people_dir
    path = target_dir / page_filename(page.id)
    path.write_text(_render_page(page), encoding="utf-8")
    # Sidecar provenance JSON: the YAML frontmatter writer can only
    # serialise scalars cleanly, so we mirror the full provenance dict
    # to a sibling .provenance.json for the audit reader.
    if page.provenance:
        sidecar = path.with_suffix(".provenance.json")
        sidecar.write_text(json.dumps(page.provenance, indent=2, default=str), encoding="utf-8")
    eq_sidecar = path.with_suffix(".equations.json")
    if page.equations:
        eq_sidecar.write_text(json.dumps(page.equations, indent=2), encoding="utf-8")
    elif eq_sidecar.exists():
        eq_sidecar.unlink()
    return path


def _render_page(page: WikiPage) -> str:
    lines: list[str] = ["---"]
    lines.append(f"id: {page.id}")
    lines.append(f"kind: {page.kind}")
    lines.append(f"title: {page.title}")
    lines.append(f"aliases: [{', '.join(page.aliases)}]")
    lines.append(f"links: [{', '.join(page.links)}]")
    if page.equations:
        lines.append(f"equation_count: {len(page.equations)}")
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

    # Don't append a References block if the body already has one (the writer
    # emits evidence as ## References; appending a second block produces
    # duplicates).
    has_references = "## References" in body
    if page.evidence and not has_references:
        lines.append("## References")
        lines.append("")
        for ev in page.evidence:
            lines.append(_render_evidence(ev))
    return "\n".join(lines) + "\n"


def _render_evidence(ev: Evidence) -> str:
    loc = f", {ev.locator}" if ev.locator else ""
    quote = ev.quote.replace('"', "'")
    # Use the human-readable doc_id as the display label, hiding the
    # internal chunk_id hash from the rendered output.
    return f'[^{ev.marker}]: {ev.doc_id}{loc} > "{quote}"'
