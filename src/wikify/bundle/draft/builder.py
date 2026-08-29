"""DraftBuilder — assemble ``draft.json`` from ``work.md`` + ``evidence.jsonl``.

Strategy stays in skills. ``model_id`` and ``tier`` are required
parameters of :func:`build_draft`; the CLI exposes ``--model-id`` and
``--tier`` flags so the skill or the agent must supply them
explicitly. Python never picks a default model.

What this builder DOES populate:
- page_id / page_kind / title / aliases (from work.md frontmatter)
- evidence (from evidence.jsonl + corpus chunk text)
- model_id / tier (caller-supplied)
- author_context (person pages whose title or aliases match a corpus author)

What is left empty (set by the writer skill before invocation):
- style_guide / field_guide / artifact_template / corpus_persona
  and their hashes
- dossier_context_yaml / related_pages / equations_context
- prompt_template / skeleton
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError

from ...api import Bundle, Corpus
from ...corpus import queries as corpus_queries
from ...corpus.chunks import list_documents
from ...corpus.images_index import ImageIndex, ImageRecord, is_decoration_dims
from ...corpus.store.authors import author_key
from ...schema import ImageRef, WriteEvidenceRef, WriteRequest
from ...types import ModelTier
from ..work.card import load_card
from ..work.evidence import read_evidence
from .artifact import (
    dossier_path,
    draft_path,
    read_json,
    response_path,
    strip_draft_envelope,
    write_json,
)
from .author_context import build_author_context
from .dossier import render_dossier


def _drop_empty_body_evidence(active: list, fetched_chunks: dict) -> tuple[list, int]:
    """Partition active evidence into ``(usable, n_dropped)``.

    A record is usable only when its chunk resolves to non-whitespace text.
    Empty-body records — an id that resolved to nothing, or figure/table/caption
    residue with no prose — are dropped here so the writer never receives
    evidence it cannot ground and then silently discards (F18). The dossier,
    the draft evidence list, and the reported evidence count then reflect
    usable evidence only.
    """
    usable: list = []
    dropped = 0
    for rec in active:
        chunk = fetched_chunks.get(rec.chunk_id)
        text = getattr(chunk, "text", "") if chunk is not None else ""
        if text and text.strip():
            usable.append(rec)
        else:
            dropped += 1
    return usable, dropped


# A bare evidence marker as stored in ``wiki_evidence.marker`` (``e7``, not
# the ``[^e7]`` prose form).
_MARKER_RE = re.compile(r"e(\d+)")


def _committed_marker_positions(bundle: Bundle, page_id: str) -> dict[int, str]:
    """``{old_index: chunk_id}`` for the markers the committed page cites.

    Read from ``wiki_evidence``, which records the marker->chunk binding the
    page was committed with. This is the only surviving record of the old
    ordering: ``wiki commit`` garbage-collects ``draft.json``, so on a refine
    there is no prior draft to diff against. Sparse by construction — a page
    cites a subset of its evidence — which is exactly right, since only cited
    markers can be carried over.
    """
    from ..wiki.store import open_wiki_store

    if not bundle.sqlite_path.exists():
        return {}
    # ``open_wiki_store`` is inside the try: it runs the schema script, so a
    # truncated or corrupt wiki.db raises there, not at the query. This is a
    # best-effort baseline; losing it must degrade the remap, not break the
    # refine build.
    con = None
    try:
        con = open_wiki_store(bundle.sqlite_path)
        rows = con.execute(
            "SELECT marker, chunk_id FROM wiki_evidence WHERE page_id = ?",
            (page_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        if con is not None:
            con.close()
    out: dict[int, str] = {}
    for marker, chunk_id in rows:
        m = _MARKER_RE.fullmatch(str(marker or "").strip())
        if m and chunk_id:
            out[int(m.group(1)) - 1] = str(chunk_id)
    return out


def _marker_remap(previous: dict[int, str], new_chunk_ids: list[str]) -> dict:
    """Report how evidence indices moved since the page's last marker binding.

    ``[^eN]`` resolves positionally to ``evidence[N-1]``, so any rebuild that
    reorders, drops, or inserts an evidence record silently repoints every
    marker after the change. A refiner reusing markers from the committed page
    needs the mapping to re-derive them deterministically rather than
    re-reading the whole dossier.

    *previous* maps an old 0-based index to the chunk_id it addressed. Returns
    ``{"moved": {old_marker: new_marker}, "dropped": [old_marker],
    "stable": bool}``. Markers whose index is unchanged are omitted from
    ``moved``; appended records are simply the new tail and are not reported.
    """
    # FIRST occurrence per chunk id. A chunk gathered twice would otherwise
    # resolve every earlier occurrence to the last index, so a rebuild that
    # changed nothing would report a fabricated move and the refiner would
    # renumber a correct marker to a wrong one.
    first_pos: dict[str, int] = {}
    for i, cid in enumerate(new_chunk_ids):
        first_pos.setdefault(cid, i)
    moved: dict[str, str] = {}
    dropped: list[str] = []
    for old_idx in sorted(previous):
        cid = previous[old_idx]
        # An index still holding the same chunk has not moved, duplicates or not.
        if 0 <= old_idx < len(new_chunk_ids) and new_chunk_ids[old_idx] == cid:
            continue
        pos = first_pos.get(cid)
        if pos is None:
            dropped.append(f"e{old_idx + 1}")
        elif pos != old_idx:
            moved[f"e{old_idx + 1}"] = f"e{pos + 1}"
    return {"moved": moved, "dropped": dropped, "stable": not moved and not dropped}


def build_draft(
    bundle: Bundle,
    *,
    slug: str,
    corpus: Corpus,
    model_id: str,
    tier: ModelTier | str,
    task: Literal["create", "refine"] = "create",
    with_adjacent: bool = False,
) -> WriteRequest:
    """Assemble a ``WriteRequest`` for *slug* and write it to draft.json.

    Strategy knobs (``model_id``, ``tier``, ``task``) are required;
    this function never picks them. When ``with_adjacent`` is true,
    each evidence record's flanking chunks (ord-1 and ord+1 within the
    same document) are concatenated into ``context_window`` so the
    writer can read sentences that bridge into and out of the cited
    chunk. Citations and quote grounding still target the primary
    ``chunk_id`` only.
    """
    card = load_card(bundle, slug)
    if not card.front:
        raise FileNotFoundError(
            f"work/concepts/{slug}/work.md not found; create the concept first"
        )

    # Captured BEFORE the rebuild: on a refine the caller needs to know how
    # evidence indices moved, since `[^eN]` is positional.
    previous_positions: dict[int, str] = {}
    remap_source = "none"
    prior_path = draft_path(bundle, slug)
    if task == "refine":
        # Which baseline is right depends on what the markers in hand were
        # numbered against. An in-flight response.json was written against the
        # PRIOR DRAFT and may cite markers the commit never saw, so that draft
        # is the correct baseline while it exists. With no attempt in flight
        # the committed page is the only thing being edited - and it must win
        # over a leftover draft.json, or a second `draft build --task refine`
        # would diff the new order against its own previous output and report
        # `stable` while the markers had moved relative to the committed page.
        in_flight = response_path(bundle, slug).is_file()
        if not in_flight:
            previous_positions = _committed_marker_positions(bundle, card.page_id)
            if previous_positions:
                remap_source = "committed_page"
        if not previous_positions and prior_path.is_file():
            try:
                prior = strip_draft_envelope(read_json(prior_path))
            except (OSError, ValueError):
                prior = {}  # a half-written draft must not block the rebuild
            previous_positions = {
                i: str(ev.get("chunk_id", ""))
                for i, ev in enumerate(prior.get("evidence") or [])
                if isinstance(ev, dict) and ev.get("chunk_id")
            }
            if previous_positions:
                remap_source = "prior_draft"

    evidence_records = read_evidence(bundle, slug)
    active = [r for r in evidence_records if r.status == "active"]

    doc_chunks_cache: dict[str, list] = {}

    def _neighbours_for(rec) -> list[tuple[str, object]]:
        """``(label, chunk)`` for the ord-1 / ord+1 chunks of *rec*'s doc."""
        if not with_adjacent:
            return []
        doc_chunks = doc_chunks_cache.get(rec.doc_id)
        if doc_chunks is None:
            doc_chunks = corpus_queries.list_chunks_for_doc(corpus, rec.doc_id)
            doc_chunks_cache[rec.doc_id] = doc_chunks
        pos = next(
            (i for i, c in enumerate(doc_chunks) if c.id == rec.chunk_id),
            None,
        )
        if pos is None:
            return []
        out: list[tuple[str, object]] = []
        if pos > 0:
            out.append(("prev", doc_chunks[pos - 1]))
        if pos + 1 < len(doc_chunks):
            out.append(("next", doc_chunks[pos + 1]))
        return out

    def _context_window_for(neighbours: list[tuple[str, object]]) -> str:
        return "\n\n".join(
            f"[{label} ord={c.ord}]\n{c.text or ''}" for label, c in neighbours
        )

    chunk_ids = [r.chunk_id for r in active]
    equations_by_chunk = corpus_queries.equations_for_chunks(corpus, chunk_ids)
    fetched_chunks: dict[str, object] = {}
    for cid in chunk_ids:
        chunk = corpus_queries.get_chunk(corpus, cid)
        if chunk is not None:
            fetched_chunks[cid] = chunk
    artifacts_by_chunk = corpus_queries.referenced_artifacts_for_chunks(
        corpus, list(fetched_chunks.values())
    )
    # F18: drop evidence whose chunk resolved to an empty body before the writer
    # ever sees it, so it cannot silently discard markers the dossier advertised.
    usable, dropped_empty = _drop_empty_body_evidence(active, fetched_chunks)
    figures = _figure_candidates_for_evidence(corpus, usable, limit=6)

    # Adjacent-window asset scan. Retrieval favours prose that DESCRIBES a
    # result over the equation or table that STATES it, so the key formula or
    # fitted coefficient often sits in the chunk next to the selected one —
    # readable via ``--with-adjacent``, but not citable. Detecting it here
    # lets the editor promote the neighbour before a writer burns a run.
    neighbours_by_rec = {rec.chunk_id: _neighbours_for(rec) for rec in usable}
    neighbour_equations: dict[str, list[str]] = {}
    if with_adjacent:
        neighbour_equations = corpus_queries.equations_for_chunks(
            corpus,
            [c.id for pairs in neighbours_by_rec.values() for _, c in pairs],
        )

    def _context_window_assets(rec) -> tuple[list[str], list[str]]:
        """``(asset kinds, ids of the neighbours carrying them)``."""
        pairs = neighbours_by_rec.get(rec.chunk_id) or []
        kinds: list[str] = []
        promotable: list[str] = []
        if not equations_by_chunk.get(rec.chunk_id):
            with_eq = [c.id for _, c in pairs if neighbour_equations.get(c.id)]
            if with_eq:
                kinds.append("equation")
                promotable.extend(with_eq)
        primary_type = getattr(fetched_chunks.get(rec.chunk_id), "section_type", "")
        if primary_type != "table":
            with_table = [
                c.id for _, c in pairs
                if getattr(c, "section_type", "") == "table"
            ]
            if with_table:
                kinds.append("table")
                promotable.extend(with_table)
        return kinds, list(dict.fromkeys(promotable))

    evidence: list[WriteEvidenceRef] = []
    for rec in usable:
        window_kinds, promotable_ids = _context_window_assets(rec)
        chunk = fetched_chunks.get(rec.chunk_id)
        chunk_text = getattr(chunk, "text", "") if chunk is not None else ""
        section_type = getattr(chunk, "section_type", "") if chunk is not None else ""
        chunk_ord = getattr(chunk, "ord", -1) if chunk is not None else -1
        artifacts = artifacts_by_chunk.get(rec.chunk_id, {})
        # `rec` may carry an out-of-schema ``source`` label written by
        # the workflow when evidence was gathered via multiple
        # sub-queries (refinement / guided strategies). Pass it through
        # so the dossier renderer can group by retrieval source.
        source_label = ""
        extras = getattr(rec, "__pydantic_extra__", None) or {}
        if isinstance(extras.get("source"), str):
            source_label = extras["source"]
        evidence.append(
            WriteEvidenceRef(
                chunk_id=rec.chunk_id,
                doc_id=rec.doc_id,
                quote=rec.quote,
                chunk_text=chunk_text,
                section_type=section_type,
                score=rec.score,
                chunk_ord=chunk_ord,
                context_window=_context_window_for(
                    neighbours_by_rec.get(rec.chunk_id) or []
                ),
                context_window_assets=window_kinds,
                promotable_chunk_ids=promotable_ids,
                source=source_label,
                chunk_equations=equations_by_chunk.get(rec.chunk_id, []),
                chunk_tables=artifacts.get("tables", []),
                chunk_figures=artifacts.get("figures", []),
            )
        )

    tier_value = tier if isinstance(tier, ModelTier) else ModelTier(tier)
    data_points, related_data_artifacts = _data_for_evidence(
        bundle, {r.chunk_id for r in usable}, {r.doc_id for r in usable}
    )
    request = WriteRequest(
        page_id=card.page_id,
        page_kind=card.kind,
        title=card.page_id,
        aliases=card.aliases,
        skeleton="",
        prompt_template="",
        model_id=model_id,
        tier=tier_value,
        evidence=evidence,
        figures=figures,
        author_context=_author_context_for_card(corpus, card)
        if card.kind == "person"
        else None,
        data_points=data_points,
        related_data_artifacts=related_data_artifacts,
    )

    payload = request.model_dump(mode="json")
    payload["schema_version"] = 1
    payload["task"] = task
    payload["dropped_empty_evidence"] = dropped_empty
    if task == "refine":
        remap = _marker_remap(previous_positions, [ev.chunk_id for ev in evidence])
        if remap_source == "none":
            # No baseline was available, so nothing is known about whether the
            # markers moved. Reporting `stable: true` here would assert safety
            # this never established; a refiner must re-derive from the dossier.
            remap["stable"] = False
        payload["marker_remap"] = {**remap, "source": remap_source}
    write_json(draft_path(bundle, slug), payload)
    # Regenerate the markdown evidence dossier so iterative strategies
    # (refine / guided / query) that re-run ``draft build`` after
    # appending evidence always see a fresh dossier alongside draft.json.
    dossier_p = dossier_path(bundle, slug)
    dossier_p.parent.mkdir(parents=True, exist_ok=True)
    dossier_p.write_text(render_dossier(request), encoding="utf-8")
    return request


def _data_for_evidence(
    bundle: Bundle, evidence_chunk_ids: set[str], evidence_doc_ids: set[str]
) -> tuple[list[dict], list[dict]]:
    """Verified data points + related data artifacts for this page's evidence.

    ``points`` are CHUNK-level: only claims whose ``chunk_id`` is already in
    the draft's evidence, so the writer can cite each number via that chunk's
    existing ``[^eN]`` marker without introducing un-vetted evidence.

    ``related`` is DOC-level: committed data artifacts whose backing claims
    share a source DOCUMENT with this page. The DATA wave harvests the
    number-dense chunks the article explorers skip, so an artifact and the
    page it generalizes share source documents but not chunks -- a chunk
    intersection is empty by construction. This mirrors
    ``relevant_committed_artifacts`` (commit-time snapshot), so a page's
    linked artifacts and its committed ``data_artifacts_seen`` agree.

    Returns ``([], [])`` when no claim store exists yet.
    """
    if not bundle.claims_db_path.exists() or (
        not evidence_chunk_ids and not evidence_doc_ids
    ):
        return [], []
    from ...data.store import DataStore

    store = DataStore.open(bundle.root)
    try:
        rows = store.list_points(status="verified")
        artifacts = store.artifacts_for_docs(list(evidence_doc_ids))
    finally:
        store.close()
    points = [
        {
            "subject": r["subject"],
            "property": r["property"],
            "value": r["value_text"],
            "unit": r["unit"] or "",
            "chunk_id": r["chunk_id"],
        }
        for r in rows
        if r["chunk_id"] in evidence_chunk_ids
    ]
    related = [{"title": a["title"]} for a in artifacts]
    return points, related


def load_draft(bundle: Bundle, slug: str) -> WriteRequest:
    """Read ``draft.json`` and return the parsed model."""
    payload = strip_draft_envelope(read_json(draft_path(bundle, slug)))
    return WriteRequest.model_validate(payload)


def _author_context_for_card(corpus: Corpus, card) -> dict | None:
    context = build_author_context(list_documents(corpus))
    for name in [card.page_id, *card.aliases]:
        keys = []
        if isinstance(name, str) and name.lower().startswith("author:"):
            payload = name.split(":", 1)[1].strip().replace("_", " ")
            keys.extend([payload, author_key(payload)])
        else:
            keys.append(author_key(name))
        for key in keys:
            if key in context:
                return asdict(context[key])
    return None


def _is_likely_decoration(img: ImageRecord, corpus_root: Path) -> bool:
    """Reject publisher banners, logos, and tiny rasters by raster size.

    Prefers ``img.width``/``img.height`` from the assets table; falls back
    to opening the file with Pillow when the metadata is missing. Returns
    ``False`` on any I/O or decoding error so a flaky read never silently
    drops a legitimate figure.
    """
    width, height = img.width, img.height
    if width is None or height is None:
        abs_path = corpus_root / img.path
        if not abs_path.is_file():
            return False
        try:
            with Image.open(abs_path) as im:
                width, height = im.size
        except (OSError, UnidentifiedImageError):
            return False
    return is_decoration_dims(width, height)


def _figure_candidates_for_evidence(corpus: Corpus, records, *, limit: int) -> list[ImageRef]:
    """Return captioned figures near the active evidence chunks.

    The writer chooses whether to use any of these. This helper only
    supplies deterministic candidates already linked to cited chunks or
    explicitly flagged on the evidence record.
    """
    import sqlite3

    from ...corpus.handles import HandleNotFoundError
    from ...corpus.handles import resolve as resolve_handle

    try:
        index = ImageIndex.load(corpus)
    except (OSError, sqlite3.Error, ValueError):
        return []

    doc_keys = list(index.by_doc.keys())

    def _resolve_doc_id(raw: str) -> str | None:
        """Resolve a possibly-short ``doc:<hex>`` handle to the full doc_id key."""
        # Strip the "doc:" prefix if present, leaving the bare hex or full id.
        short = raw[4:] if raw.startswith("doc:") else raw
        try:
            return resolve_handle(short, iter(doc_keys))
        except (HandleNotFoundError, LookupError):
            return None

    out: list[ImageRef] = []
    seen: set[str] = set()
    for rec in records:
        extras = getattr(rec, "__pydantic_extra__", None) or {}
        flagged = {
            str(x)
            for x in (extras.get("evidence_figures") or extras.get("figures") or [])
            if x
        }
        resolved_doc_id = _resolve_doc_id(rec.doc_id)
        if resolved_doc_id is None:
            continue
        for img in index.for_doc(resolved_doc_id):
            if not img.caption or not img.path:
                continue
            if rec.chunk_id not in img.near_chunk_ids and img.id not in flagged:
                continue
            if img.id in seen:
                continue
            if _is_likely_decoration(img, index.corpus_root):
                continue
            seen.add(img.id)
            out.append(
                ImageRef(
                    id=img.id,
                    label=img.label,
                    caption=img.caption,
                    page=img.page,
                    path=img.path,
                    near_chunk_ids=list(img.near_chunk_ids),
                )
            )
            if len(out) >= limit:
                return out
    return out
