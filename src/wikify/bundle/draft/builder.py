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

    evidence_records = read_evidence(bundle, slug)
    active = [r for r in evidence_records if r.status == "active"]

    doc_chunks_cache: dict[str, list] = {}

    def _context_window_for(rec) -> str:
        if not with_adjacent:
            return ""
        doc_chunks = doc_chunks_cache.get(rec.doc_id)
        if doc_chunks is None:
            doc_chunks = corpus_queries.list_chunks_for_doc(corpus, rec.doc_id)
            doc_chunks_cache[rec.doc_id] = doc_chunks
        pos = next(
            (i for i, c in enumerate(doc_chunks) if c.id == rec.chunk_id),
            None,
        )
        if pos is None:
            return ""
        parts: list[str] = []
        if pos > 0:
            prev = doc_chunks[pos - 1]
            parts.append(f"[prev ord={prev.ord}]\n{prev.text or ''}")
        if pos + 1 < len(doc_chunks):
            nxt = doc_chunks[pos + 1]
            parts.append(f"[next ord={nxt.ord}]\n{nxt.text or ''}")
        return "\n\n".join(parts)

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

    evidence: list[WriteEvidenceRef] = []
    for rec in usable:
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
                context_window=_context_window_for(rec),
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
