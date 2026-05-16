"""Render a ``WriteRequest``'s evidence list as a markdown dossier.

The writer reads ``dossier.md``; the validator reads ``draft.json``.
Both are derived from the same source — ``evidence.jsonl`` plus chunk
content — and `wikify draft build` regenerates both atomically. Any
iterative strategy that adds evidence and re-runs `draft build`
automatically gets a fresh dossier.

Grouping order (deterministic):

1. By ``source`` label when evidence records carry distinct values
   (refinement / guided strategies that gather via sub-queries).
   Baseline evidence has a single empty ``source`` so this level
   collapses.
2. Within a source group, by ``doc_id``. Documents appear in their
   first-marker order (the order their earliest cited chunk appears
   in the evidence list) so the most-prominent source leads.
3. Within a document, chunks are sorted by section priority (abstract
   → introduction → background → methods → results → discussion →
   conclusion → body → other) and then by chunk ord. This gives the
   writer a coherent "read the paper" flow per source.

Marker numbering (``eN``) stays positional against the underlying
``draft.evidence`` array. The dossier is presentation only; it does
not renumber.
"""

from __future__ import annotations

from collections import OrderedDict

from ...schema import WriteEvidenceRef, WriteRequest

_SECTION_ORDER = {
    "abstract": 0,
    "introduction": 1,
    "background": 2,
    "methods": 3,
    "results": 4,
    "discussion": 5,
    "conclusion": 6,
    "body": 7,
}


def _section_rank(s: str) -> int:
    return _SECTION_ORDER.get(s or "body", 8)


def _short_doc(doc_id: str) -> str:
    """Last 12 hex of doc_id (the conventional short handle)."""
    return doc_id[-12:] if doc_id else ""


def _short_chunk(chunk_id: str) -> str:
    return chunk_id[-12:] if chunk_id else ""


def _doc_year_range(refs: list[WriteEvidenceRef]) -> tuple[int, int] | None:
    """Best-effort extract earliest/latest publication year from doc_ids
    that begin with ``[YYYY ...``. None when no years parse."""
    years: list[int] = []
    for r in refs:
        d = r.doc_id or ""
        if len(d) >= 6 and d.startswith("[") and d[5:6] == " ":
            try:
                years.append(int(d[1:5]))
            except ValueError:
                continue
    if not years:
        return None
    return (min(years), max(years))


def _doc_label(doc_id: str) -> str:
    """Human-readable doc handle. Strips the trailing hash suffix when
    the id has the conventional ``..._<12hex>`` shape."""
    if not doc_id:
        return ""
    last = doc_id.rsplit("_", 1)
    if len(last) == 2 and len(last[1]) == 12 and all(
        c in "0123456789abcdef" for c in last[1]
    ):
        return last[0]
    return doc_id


def _yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    safe = [
        i.replace('"', '\\"') for i in items if isinstance(i, str)
    ]
    return "[" + ", ".join(f'"{s}"' for s in safe) + "]"


def render_dossier(draft: WriteRequest) -> str:
    """Return a markdown dossier string for *draft*'s evidence list.

    The marker for ``draft.evidence[N-1]`` is ``eN``. Re-running with
    the same draft yields byte-identical output (deterministic).
    """
    refs = list(draft.evidence)
    markers = [f"e{i + 1}" for i in range(len(refs))]
    by_marker: dict[str, WriteEvidenceRef] = dict(zip(markers, refs))

    # Group: source → doc → chunks (sorted by section then text length).
    groups: "OrderedDict[str, OrderedDict[str, list[tuple[str, WriteEvidenceRef]]]]"
    groups = OrderedDict()
    for marker, ref in by_marker.items():
        src = ref.source or ""
        doc = ref.doc_id or ""
        groups.setdefault(src, OrderedDict()).setdefault(doc, []).append(
            (marker, ref)
        )
    for src, by_doc in groups.items():
        for doc, items in by_doc.items():
            items.sort(
                key=lambda kv: (
                    _section_rank(kv[1].section_type),
                    kv[1].chunk_id,
                )
            )

    section_types = sorted(
        {r.section_type for r in refs if r.section_type}
    )
    year_range = _doc_year_range(refs)
    distinct_sources = len({r.doc_id for r in refs if r.doc_id})

    fm_lines = [
        "---",
        f"page_id: {draft.page_id}",
        f"kind: {draft.page_kind}",
        f"aliases: {_yaml_list(list(draft.aliases))}",
        f"evidence_records: {len(refs)}",
        f"distinct_sources: {distinct_sources}",
    ]
    if year_range is not None:
        fm_lines.append(f"year_range: {year_range[0]}-{year_range[1]}")
    fm_lines.append(f"section_types: {_yaml_list(section_types)}")
    fm_lines.append("generated_by: wikify draft build (regenerated each run)")
    fm_lines.append("---")

    out = ["\n".join(fm_lines), ""]
    out.append(
        "<!-- generated artifact; do not edit. Re-run `wikify draft build` "
        "or `wikify draft render-dossier <slug>` to regenerate. -->"
    )
    out.append("")
    out.append(f"# Evidence Dossier — {draft.page_id}")
    out.append("")

    # Marker index
    out.append("## Marker index")
    out.append("")
    out.append("| Marker | Doc | Section | Chunk |")
    out.append("|---|---|---|---|")
    for marker, ref in by_marker.items():
        out.append(
            f"| {marker} | {_doc_label(ref.doc_id)} "
            f"| {ref.section_type or 'body'} "
            f"| {_short_chunk(ref.chunk_id)} |"
        )
    out.append("")

    if draft.figures:
        out.append("## Figure candidates")
        out.append("")
        out.append("| Figure | Page | Near markers | Caption | Path |")
        out.append("|---|---:|---|---|---|")
        marker_by_chunk = {
            ref.chunk_id: marker for marker, ref in by_marker.items()
        }
        for fig in draft.figures:
            near_markers = [
                marker_by_chunk[cid]
                for cid in fig.near_chunk_ids
                if cid in marker_by_chunk
            ]
            out.append(
                f"| {fig.id} | {fig.page or ''} | {', '.join(near_markers) or '-'} "
                f"| {(fig.caption or '').replace('|', '/')} | {fig.path} |"
            )
        out.append("")

    # Body groups
    out.append("## Evidence")
    out.append("")
    if not groups:
        out.append("_No evidence records._")
        return "\n".join(out) + "\n"

    multi_source = len(groups) > 1
    for src, by_doc in groups.items():
        if multi_source:
            label = src or "(unscoped)"
            out.append(f"### Source query: {label}")
            out.append("")
        for doc, items in by_doc.items():
            doc_markers = ", ".join(m for m, _ in items)
            out.append(f"### {_doc_label(doc)}")
            out.append("")
            out.append(f"_Chunks: {len(items)} ({doc_markers})_")
            out.append("")
            for marker, ref in items:
                out.append(
                    f"#### [{marker}] section={ref.section_type or 'body'} "
                    f"· chunk {_short_chunk(ref.chunk_id)}"
                )
                out.append("")
                text = (ref.chunk_text or "").rstrip()
                out.append(text or "_(empty chunk)_")
                out.append("")
                if ref.chunk_equations:
                    out.append("**Equations bound to this chunk:**")
                    out.append("")
                    for eqn in ref.chunk_equations:
                        out.append(f"- `{eqn.strip()}`")
                    out.append("")
                if ref.chunk_tables:
                    out.append("**Tables referenced in this chunk:**")
                    out.append("")
                    for tbl in ref.chunk_tables:
                        out.append("> " + tbl.replace("\n", "\n> "))
                        out.append("")
                if ref.chunk_figures:
                    out.append("**Figures referenced in this chunk (caption only):**")
                    out.append("")
                    for fig in ref.chunk_figures:
                        out.append(f"- {fig.strip()}")
                    out.append("")
                if ref.context_window:
                    out.append(
                        "<details><summary>Adjacent chunks "
                        "(synthesis context, do not cite)</summary>"
                    )
                    out.append("")
                    out.append("```")
                    out.append(ref.context_window.rstrip())
                    out.append("```")
                    out.append("")
                    out.append("</details>")
                    out.append("")
    return "\n".join(out).rstrip() + "\n"


__all__ = ["render_dossier"]
