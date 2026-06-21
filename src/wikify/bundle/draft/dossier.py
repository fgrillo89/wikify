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
2. Within a source group, by ``doc_id``. Documents are ordered by
   their per-doc max evidence score (descending) so the strongest
   source leads. Ties break on ``doc_id`` for deterministic output.
3. Within a document, chunks are ordered by ``chunk_ord`` (ascending)
   so the dossier reads in narrative order — intros before methods,
   methods before results — regardless of retrieval order. Chunks
   without a known ``chunk_ord`` (corpus lookup failed) sort last,
   preserving their insertion order.

Marker numbering (``eN``) stays positional against the underlying
``draft.evidence`` array. The dossier is presentation only; it does
not renumber.
"""

from __future__ import annotations

from collections import OrderedDict

from ...schema import WriteEvidenceRef, WriteRequest


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

    # Group: source → doc → chunks.
    # Within a doc, sort by chunk_ord ascending (narrative order); unknown
    # ord (-1) sorts last and falls back to insertion order via the marker.
    # Across docs within a source, sort by per-doc max score descending so
    # the strongest paper leads; tie-break on doc_id for determinism.
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
                    kv[1].chunk_ord if kv[1].chunk_ord >= 0 else float("inf"),
                    kv[0],
                )
            )
        ordered_docs = sorted(
            by_doc.items(),
            key=lambda kv: (-max(r.score for _, r in kv[1]), kv[0]),
        )
        groups[src] = OrderedDict(ordered_docs)

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

    # Verified data points drawn from this page's evidence chunks. This is an
    # internal citation index, NOT content to paste: each fact is citable via
    # the marker of the chunk it came from. The cross-source comparison table
    # is a separate, evolving data artifact — link it rather than rebuild it.
    if draft.data_points:
        marker_by_chunk = {ref.chunk_id: m for m, ref in by_marker.items()}
        rows = [
            dp for dp in draft.data_points if marker_by_chunk.get(dp.get("chunk_id"))
        ]
        if rows:
            out.append("## Available data (citation index — do not paste verbatim)")
            out.append("")
            out.append(
                "_Verified figures from this page's own sources. Use them to make "
                "and ground GENERAL claims in prose: state a number inline and "
                "attach the marker from the `Cite as` column to it (e.g. \"a SET "
                "voltage of 0.9 V[^e3]\"). Do NOT reproduce this as a table and do "
                "NOT add a `Marker`/`Cite as` column to the article — the marker is "
                "the citation, not data. For the side-by-side comparison, link the "
                "data artifact(s) listed below._"
            )
            out.append("")
            if draft.related_data_artifacts:
                links = ", ".join(
                    f"[[{a.get('title', '')}]]"
                    for a in draft.related_data_artifacts
                    if a.get("title")
                )
                if links:
                    out.append(f"**Link the cross-source data artifact(s):** {links}")
                    out.append("")
            out.append("| Subject | Property | Value | Cite as |")
            out.append("|---|---|---|---|")
            for dp in rows:
                marker = marker_by_chunk[dp["chunk_id"]]
                value = str(dp.get("value", "")).replace("|", "/")
                unit = str(dp.get("unit", "")).strip()
                if unit and unit.lower() not in value.lower():
                    value = f"{value} {unit}".strip()
                subj = str(dp.get("subject", "")).replace("|", "/")
                prop = str(dp.get("property", "")).replace("|", "/")
                out.append(f"| {subj} | {prop} | {value} | [^{marker}] |")
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
                # When the vetter supplied a curated `quote` via
                # `wikify work build-evidence --from-ids @-`, surface it
                # above the chunk text so the writer reads the on-topic
                # sentence first instead of the chunk head (which is often
                # a byline). Default text[:400] fallbacks are not curated
                # — they match the chunk's leading text and add nothing,
                # so suppress the block in that case.
                quote = (ref.quote or "").strip()
                if quote and not text.startswith(quote):
                    out.append(f"> **Selected quote:** {quote}")
                    out.append("")
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
                # Per-chunk figure captions are intentionally omitted: the
                # top-of-dossier "## Figure candidates" table already lists
                # every figure with its near-marker mapping, so repeating
                # the captions here only triples the token spend.
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
