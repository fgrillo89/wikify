"""Per-chunk ExtractRequest builders shared across pipelines.

Both ``distill/pipeline.py`` (the standard explorer-driven loop) and
``baselines/pipeline.py`` (the abstract-first baseline) build the same
shape of ``ExtractRequest`` for each chunk: text + canonical titles +
images + equations + figure captions + citation refs. Keeping these
builders in one place is what lets the two pipelines share the same
extract-call shape (and the same cache key) instead of forking on
prompt-context bookkeeping.
"""

from __future__ import annotations

import re

from ..models import Chunk, Document
from ..schema import EquationRef, FigureCaption, ImageRef
from ..store.images_index import ImageRecord


def normalize_title(t: str) -> str:
    """Lowercase + collapse whitespace; the canonicalize alias key."""
    return " ".join(t.lower().split())


def to_imageref(rec: ImageRecord) -> ImageRef:
    return ImageRef(
        id=rec.id,
        label=rec.label,
        caption=rec.caption,
        page=rec.page,
        path=rec.path,
        near_chunk_ids=list(rec.near_chunk_ids),
    )


def resolve_citation_refs(
    chunk_text: str,
    doc_id: str,
    knowledge_graph: object | None,
) -> list[dict]:
    """Build citation_refs for an ExtractRequest from the KnowledgeGraph.

    Parses [N] markers from chunk text, then resolves each ordinal to a
    target source via the KG's ord_refs index. Returns dicts compatible
    with the ExtractRequest.citation_refs schema.
    """
    if knowledge_graph is None:
        return []
    from ..citestore.graph import parse_citation_markers

    ords = parse_citation_markers(chunk_text)
    if not ords:
        return []

    source_node = knowledge_graph.source(doc_id).first()
    if not source_node:
        return []

    ord_refs = source_node.get("ord_refs", {})
    results: list[dict] = []
    for n in ords:
        target_id = ord_refs.get(n)
        if not target_id:
            continue
        target = knowledge_graph.source(target_id).first()
        if not target:
            continue
        results.append({
            "ord": n,
            "title": target.get("title", ""),
            "authors": (target.get("authors") or [])[:3],
            "year": target.get("year"),
            "doi": target.get("doi", ""),
            "in_corpus": target.get("kind") == "corpus",
            "corpus_doc_id": target_id if target.get("kind") == "corpus" else "",
        })
    return results


def equations_for_chunk(chunk: Chunk, docs_by_id: dict[str, Document]) -> list[EquationRef]:
    """Build the EquationRef list for one chunk's ExtractRequest.

    Pulls ``Document.equations`` for the chunk's parent doc and filters
    to those whose ``id`` is in ``chunk.equation_ids`` (the chunker
    binds equations to chunks at ingest time via char_span overlap).
    Equation order matches ``chunk.equation_ids`` so the model sees
    them in source order.
    """
    if not chunk.equation_ids:
        return []
    doc = docs_by_id.get(chunk.doc_id)
    if doc is None or not doc.equations:
        return []
    by_id: dict[str, dict] = {e["id"]: e for e in doc.equations if e.get("id")}
    out: list[EquationRef] = []
    for eq_id in chunk.equation_ids:
        eq = by_id.get(eq_id)
        if eq is None:
            continue
        try:
            out.append(
                EquationRef(
                    id=eq["id"],
                    latex=str(eq.get("latex") or ""),
                    type=eq.get("type", "unicode"),
                    label=eq.get("label"),
                    context=str(eq.get("context") or ""),
                )
            )
        except Exception:  # noqa: BLE001
            # Be permissive: a malformed equation record should never
            # crash the extract pipeline. Skip it and move on.
            continue
    return out


def figure_captions_for_chunk(
    chunk: Chunk,
    docs_by_id: dict[str, Document],
    images_index,
) -> list[FigureCaption]:
    """Build per-chunk figure captions for ExtractRequest.

    Combines two sources so the model sees every figure that's
    semantically near the current chunk:

    1. **Binary images** in ``images_index`` whose ``near_chunk_ids``
       includes ``chunk.id``. These have an ``image_id`` set so the
       handler knows it can attach the figure as evidence with a real
       image binary backing it.
    2. **Body figure refs** (``Document.figure_refs``) whose
       ``section_path`` matches the chunk's section_path. Caption-only
       — used when the figure extractor failed to grab the binary but
       the prose still has a usable caption.

    Caption chunks (``__image__``) skip this entirely — they ARE the
    image, no need to also link a caption.
    """
    sp = list(chunk.section_path or [])
    if sp and sp[0] == "__image__":
        return []
    out: list[FigureCaption] = []
    seen_keys: set[tuple[str, int, str]] = set()

    # 1. Binary images near this chunk.
    for rec in images_index.for_doc(chunk.doc_id):
        if chunk.id not in (rec.near_chunk_ids or ()):
            continue
        # Try to derive (kind, num, sub) from the label or stem.
        stem = rec.id.rsplit("/", 1)[-1]
        kind, num, sub = _parse_figure_label(rec.label or stem)
        if num is None:
            continue
        key_triple = (kind, num, sub)
        if key_triple in seen_keys:
            continue
        seen_keys.add(key_triple)
        out.append(
            FigureCaption(
                key=_format_figure_key(kind, num, sub),
                kind=kind,
                num=num,
                sub=sub,
                caption=(rec.caption or "")[:500],
                image_id=rec.id,
            )
        )

    # 2. Body figure_refs in the same section.
    doc = docs_by_id.get(chunk.doc_id)
    if doc is not None and doc.figure_refs:
        for fr in doc.figure_refs:
            kind = fr.get("kind") or "figure"
            num = fr.get("num")
            sub = (fr.get("sub") or "").lower()
            if num is None:
                continue
            key_triple = (kind, int(num), sub)
            if key_triple in seen_keys:
                continue
            # Only surface a body figure_ref when its section_path matches
            # the chunk's section — otherwise we'd flood every chunk with
            # every figure in the doc.
            ref_section = list(fr.get("section_path") or [])
            if ref_section and sp and ref_section[0] != sp[0]:
                # Different top-level section: skip.
                continue
            seen_keys.add(key_triple)
            out.append(
                FigureCaption(
                    key=fr.get("key") or _format_figure_key(kind, int(num), sub),
                    kind=kind,
                    num=int(num),
                    sub=sub,
                    caption=str(fr.get("caption") or "")[:500],
                    image_id=None,
                )
            )

    return out


_FIGURE_LABEL_RE = re.compile(
    r"^(?P<kind>fig(?:ure)?|table|scheme|sch)[._\s]*(?P<num>\d+)\s*(?P<sub>[a-z])?",
    re.IGNORECASE,
)


def _parse_figure_label(s: str) -> tuple[str, int | None, str]:
    """Parse a label or stem into ``(kind, num, sub)``."""
    if not s:
        return ("figure", None, "")
    m = _FIGURE_LABEL_RE.match(s.strip().lower())
    if not m:
        return ("figure", None, "")
    kind_raw = m.group("kind")
    kind = "figure" if kind_raw.startswith("fig") else kind_raw
    return (kind, int(m.group("num")), (m.group("sub") or "").lower())


def _format_figure_key(kind: str, num: int, sub: str) -> str:
    label = "Fig." if kind == "figure" else kind.capitalize()
    return f"{label} {num}{sub}"
