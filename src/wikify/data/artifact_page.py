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
import re
from pathlib import Path

from .consolidate import ConsolidatedTable
from .models import ArtifactSpec


def _safe_display_title(title: str) -> str:
    """One-line display title: newlines/control chars collapsed so a
    user-controlled artifact title cannot inject YAML frontmatter lines (the
    ``kind: data`` field must stay non-overridable)."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", title or "")).strip()

# Strip trailing chunk suffix: __cNNNN_<hex>
_RE_CHUNK_SUFFIX = re.compile(r"__c\d+_[0-9a-f]+$")
# Strip trailing doc-hash: _<12 hex chars>
_RE_DOC_HASH = re.compile(r"_[0-9a-f]{12}$")


def _clean_source_label(s: str) -> str:
    """Remove raw id fragments from a doc_id or chunk_id string.

    Strips a trailing ``__cNNNN_<hex>`` chunk suffix and a trailing
    ``_<12 hex>`` doc-hash so the human-readable title part is exposed.
    """
    s = _RE_CHUNK_SUFFIX.sub("", s)
    s = _RE_DOC_HASH.sub("", s)
    return s


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
    from ..bundle.wiki.page_naming import page_id_from_title

    # One canonical id everywhere: the sanitized page id is the frontmatter
    # `id`, the filename stem, and the DB page_id/slug. The raw title is kept
    # only as the display `title`/heading, so a title with filesystem-reserved
    # characters (e.g. "ON/OFF Ratio") cannot split the file identity from the
    # registered DB identity.
    page_id = page_id_from_title(table.title)
    display_title = _safe_display_title(table.title)
    lines: list[str] = []
    lines.append("---")
    lines.append(f"id: {page_id}")
    lines.append("kind: data")
    lines.append(f"title: {display_title}")
    lines.append("aliases: []")
    lines.append("links: []")
    lines.append("---")
    lines.append("")
    lines.append(f"# {display_title}")
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
        raw_doc_id = ev["doc_id"] or ""
        # Fall back to chunk_id only when doc_id is absent; strip it fully
        # (chunk suffixes carry no cross-link value — doc_id is what matters).
        raw_id = raw_doc_id if raw_doc_id else ev.get("chunk_id") or ""
        label = _clean_source_label(raw_id) if raw_id else ""
        locator = ev.get("locator") or ""
        # Build the visual label: title[. locator]
        if label and locator:
            visual = f"{label}. {locator}"
        elif label:
            visual = label
        else:
            visual = locator
        quote = ev["quote"].replace("\n", " ").strip()
        # When doc_id was stripped, preserve the original doc_id in parentheses
        # so the page parser can recover the full id for cross-page link matching.
        if raw_doc_id and raw_doc_id != label:
            head = f"{visual} ({raw_doc_id})" if visual else raw_doc_id
        else:
            head = visual
        prefix = f"{head} " if head else ""
        lines.append(f'[^{ev["marker"]}]: {prefix}> "{quote}"')
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


def register_artifact_wiki_page(bundle, spec: ArtifactSpec, table: ConsolidatedTable) -> str:
    """Register a committed data artifact as a ``kind=data`` row in the wiki
    page DB so navigation / index / graph can reference it instead of orphaning
    it — without this the organizer hits a FOREIGN KEY error placing the page
    in a nav group (F28). Idempotent (upsert). Returns the page_id.
    """
    from ..bundle.wiki.page_naming import page_id_from_title
    from ..bundle.wiki.store import open_wiki_store, upsert_wiki_page

    page_id = page_id_from_title(table.title)
    con = open_wiki_store(bundle.sqlite_path)
    try:
        upsert_wiki_page(
            con,
            page_id=page_id,
            # The on-disk page is ``wiki/data/<page_id>.md`` (see
            # write_artifact_page), so the slug MUST be that same stem for a
            # search hit's path/handle to round-trip through wiki show.
            slug=page_id,
            title=_safe_display_title(table.title),
            kind="data",
            body=render_artifact_markdown(table),
            frontmatter={"aliases": []},
            evidence=[
                {
                    "marker": e.get("marker", ""),
                    "chunk_id": e.get("chunk_id", ""),
                    "doc_id": e.get("doc_id", ""),
                    "quote": e.get("quote", ""),
                }
                for e in table.evidence
            ],
            links=[],
        )
    finally:
        con.close()
    return page_id


def register_committed_data_pages(bundle) -> int:
    """Re-author every committed data artifact's wiki.db row from its
    ``.dataspec.json`` sidecar + the claim store, so ``wiki rebuild`` can
    restore data pages that the markdown rebuild deliberately skips (their
    rendered markdown is lossy for chunk ids). Chunk ids come from the claim
    store, never the markdown. Idempotent. Returns the count registered.
    """
    import json as _json

    data_dir = bundle.wiki_data_dir
    if not data_dir.is_dir():
        return 0
    sidecars = sorted(data_dir.glob("*.dataspec.json"))
    if not sidecars:
        return 0
    # Never register from an absent claim store: DataStore.open would create an
    # empty one, consolidate zero rows, and overwrite good wiki.db pages with
    # empty projections. With no claims, leave the existing rows untouched.
    if not bundle.claims_db_path.exists():
        return 0

    from .consolidate import consolidate
    from .models import ArtifactSpec
    from .store import DataStore

    store = DataStore.open(bundle.root)
    n = 0
    try:
        for sidecar in sidecars:
            try:
                payload = _json.loads(sidecar.read_text(encoding="utf-8"))
                spec = ArtifactSpec.from_json(_json.dumps(payload["spec"]))
            except (OSError, ValueError, KeyError):
                continue
            table = consolidate(store, spec)
            # An empty reconstruction means the backing claims are gone/stale;
            # skip rather than clobber the previously-good row with an empty one.
            if not table.claim_ids:
                continue
            register_artifact_wiki_page(bundle, spec, table)
            n += 1
    finally:
        store.close()
    return n
