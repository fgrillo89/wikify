"""Write request building, related page lookup, and cross-linking."""

from __future__ import annotations

import dataclasses
import json
import re
from collections import defaultdict
from dataclasses import dataclass

from wikify.models import Chunk, WikiPage
from wikify.paths import BundlePaths
from wikify.schema import (
    EditorBrief,
    ImageRef,
    WriteEvidenceRef,
    WriteEvidenceRefV2,
    WriteRequest,
)
from wikify.store.bibliography import citation_context_for_docs
from wikify.store.images_index import ImageIndex, ImageRecord
from wikify.types import ModelTier

from .author_context import AuthorContext, _author_key
from .dossier import DossierEntry, DossierStore, dossier_to_yaml

# ---------------------------------------------------------------------------
# Related pages
# ---------------------------------------------------------------------------

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "is",
        "in",
        "on",
        "for",
        "with",
        "by",
        "at",
        "from",
        "as",
    }
)
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_-]+")
_SEE_ALSO_RE = re.compile(r"^##\s*see\s+also\b", re.IGNORECASE | re.MULTILINE)


def _tokenise(text: str) -> frozenset[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return frozenset(t for t in tokens if t not in _STOP and len(t) >= 3)


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _extract_see_also(body: str) -> list[str]:
    """Return lines from a ## See also section (if present)."""
    m = _SEE_ALSO_RE.search(body)
    if m is None:
        return []
    after = body[m.end():]
    # Collect until next ## heading or end.
    next_h2 = re.search(r"^##\s", after, re.MULTILINE)
    section = after[: next_h2.start()] if next_h2 else after
    links: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") or stripped.startswith("*"):
            stripped = stripped.lstrip("-*").strip()
        if stripped:
            links.append(stripped)
    return links[:10]


def compute_related_pages(
    page: WikiPage,
    all_pages: list[WikiPage],
    k: int = 5,
) -> list[dict]:
    """Return top-k related pages for *page* from *all_pages*.

    Each result is:
    ``{id, title, topic_overlap, body_excerpt, see_also, evidence_doc_ids}``

    The caller is responsible for excluding *page* itself via ``page.id``;
    this function also skips pages without a body or without evidence.
    """
    cand_terms = _tokenise(page.title + " " + " ".join(page.aliases))
    cand_docs: frozenset[str] = frozenset(ev.doc_id for ev in page.evidence)

    scored: list[tuple[float, WikiPage]] = []
    for other in all_pages:
        if other.id == page.id:
            continue
        other_terms = _tokenise(other.title + " " + " ".join(other.aliases))
        other_docs: frozenset[str] = frozenset(ev.doc_id for ev in other.evidence)
        token_j = _jaccard(cand_terms, other_terms)
        doc_j = _jaccard(cand_docs, other_docs)
        score = 0.5 * token_j + 0.5 * doc_j
        if score > 0.0:
            scored.append((score, other))

    scored.sort(key=lambda t: -t[0])
    top = scored[:k]

    results: list[dict] = []
    for score, other in top:
        body = other.body_markdown or ""
        excerpt = body.strip()[:500]
        see_also = _extract_see_also(body)
        results.append(
            {
                "id": other.id,
                "title": other.title,
                "topic_overlap": round(score, 4),
                "body_excerpt": excerpt,
                "see_also": see_also,
                "evidence_doc_ids": [ev.doc_id for ev in other.evidence],
            }
        )
    return results


# ---------------------------------------------------------------------------
# Crosslink
# ---------------------------------------------------------------------------

_CROSSLINK_TOKEN_RE = re.compile(r"[a-z0-9]+")


def crosslink(pages: list[WikiPage]) -> list[WikiPage]:
    """Populate `links` on each WikiPage by alias matching + evidence overlap.

    No LLM. Two pages are linked if (a) one mentions the other's title or alias
    in its body, or (b) they share at least one source document via evidence.
    """
    # alias -> page ids
    alias_to_ids: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        alias_to_ids[p.title.lower()].append(p.id)
        for a in p.aliases:
            alias_to_ids[a.lower()].append(p.id)
    alias_by_first_token: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for alias, ids in alias_to_ids.items():
        toks = _CROSSLINK_TOKEN_RE.findall(alias)
        if not toks:
            continue
        alias_by_first_token[toks[0]].append((alias, ids))

    # evidence overlap by doc
    doc_to_pages: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        for ev in p.evidence:
            doc_to_pages[ev.doc_id].add(p.id)

    for p in pages:
        links: set[str] = set(p.links)
        body = (p.body_markdown or "").lower()
        body_tokens = set(_CROSSLINK_TOKEN_RE.findall(body))
        scanned: set[str] = set()
        for tok in body_tokens:
            for alias, ids in alias_by_first_token.get(tok, []):
                if alias in scanned:
                    continue
                scanned.add(alias)
                if alias and alias != p.title.lower() and alias in body:
                    for tid in ids:
                        if tid != p.id:
                            links.add(tid)
        for ev in p.evidence:
            for tid in doc_to_pages.get(ev.doc_id, set()):
                if tid != p.id:
                    links.add(tid)
        p.links = sorted(links)
    return pages


# ---------------------------------------------------------------------------
# Write requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteRequestConfig:
    model_id: str
    writer_tier: ModelTier
    prompt_name: str
    style_text: str
    field_text: str
    artifact_text: str
    person_artifact_text: str
    persona_text: str
    style_guide_hash: str | None = None
    field_guide_hash: str | None = None
    artifact_template_hash: str | None = None
    person_artifact_hash: str | None = None
    corpus_persona_hash: str | None = None
    verbalize: bool = False


def is_writable_page(page: WikiPage) -> bool:
    """Return whether a page should be sent to writer/runtime draft path."""
    if not page.evidence:
        return False
    if page.kind == "person" and len(page.evidence) < 2:
        return False
    return True


_PAGE_FIGURES_TOP_K = 8
_CITED_CHUNKS_PER_REF = 3


def _build_cited_corpus_chunks(
    page: WikiPage,
    chunks_by_id: dict[str, Chunk],
    knowledge_graph: object,
) -> dict[str, list[dict]]:
    """Pre-compute relevant chunks from in-corpus cited works.

    For each evidence source on the page, use the knowledge graph to find
    sources it cites and retrieve top chunks relevant to the page concept
    via scoped vector search.

    Returns {corpus_doc_id: [{chunk_id, text}]}.
    """
    result: dict[str, list[dict]] = {}
    page_doc_ids = {ev.doc_id for ev in page.evidence}

    for doc_id in sorted(page_doc_ids):
        cited = knowledge_graph.source(doc_id).references()
        for cited_id in cited.ids():
            if cited_id in page_doc_ids or cited_id in result:
                continue
            hits = knowledge_graph.source(cited_id).chunks().search(
                page.title, top_k=_CITED_CHUNKS_PER_REF,
            )
            if hits:
                result[cited_id] = [
                    {
                        "chunk_id": h["id"],
                        "text": chunks_by_id[h["id"]].text[:500]
                        if h["id"] in chunks_by_id else "",
                    }
                    for h in hits
                    if h.get("id") in chunks_by_id
                ]
    return result


def build_write_request(
    page: WikiPage,
    all_pages: list[WikiPage],
    briefs: dict[str, EditorBrief],
    dossier_store: DossierStore,
    chunks_by_id: dict[str, Chunk],
    images_index: ImageIndex,
    cfg: WriteRequestConfig,
    author_ctx: dict[str, AuthorContext] | None = None,
    citation_index: dict | None = None,
    knowledge_graph: object | None = None,
    equations_index: object | None = None,
) -> WriteRequest:
    """Build a WriteRequest for a single page.

    The figure list passed to the writer is ranked by *relevance*: each
    candidate image gets a score equal to the number of page-evidence
    chunks present in its ``near_chunk_ids``. Images that the body
    discussion explicitly cites in chunks the writer is also citing
    bubble to the top, while figures from the same doc that aren't
    discussed near any cited chunk fall to the bottom. The list is
    capped at ``_PAGE_FIGURES_TOP_K`` so the writer prompt doesn't get
    flooded with figures that aren't tied to the claims being written.
    """
    page_doc_ids = {ev.doc_id for ev in page.evidence}
    page_evidence_chunk_ids = {ev.chunk_id for ev in page.evidence}
    candidate_recs: list[tuple[int, int, ImageRecord]] = []
    seen_fig_ids: set[str] = set()
    for did in sorted(page_doc_ids):
        for rec in images_index.for_doc(did):
            if rec.id in seen_fig_ids:
                continue
            seen_fig_ids.add(rec.id)
            overlap = sum(1 for cid in rec.near_chunk_ids if cid in page_evidence_chunk_ids)
            # Tie-break: images with ANY near_chunk_ids beat images with
            # zero (decorative figures), then by stem for determinism.
            has_any_near = 1 if rec.near_chunk_ids else 0
            candidate_recs.append((-overlap, -has_any_near, rec))
    candidate_recs.sort(key=lambda t: (t[0], t[1], t[2].id))
    page_figures: list[ImageRef] = [
        _to_imageref(rec) for _, _, rec in candidate_recs[:_PAGE_FIGURES_TOP_K]
    ]

    evidence_v2 = []
    dossier = dossier_store.load(page.id)
    dossier_context = dossier_to_yaml(dossier.for_editor()) if dossier else ""
    dossier_entries_by_chunk: dict[str, DossierEntry] = {}
    if dossier:
        dossier_entries_by_chunk = {e.chunk_id: e for e in dossier.entries}
    for ev in page.evidence:
        de = dossier_entries_by_chunk.get(ev.chunk_id)
        chunk = chunks_by_id.get(ev.chunk_id)
        evidence_v2.append(
            WriteEvidenceRefV2(
                chunk_id=ev.chunk_id,
                doc_id=ev.doc_id,
                quote=ev.quote,
                locator=ev.locator,
                chunk_text=chunk.text if chunk else "",
                section_type=de.section_type if de else "",
                definition=de.definition if de else "",
                summary=de.summary if de else "",
                evidence_figures=list(de.figure_ids) if de else [],
            )
        )

    neighbor_summaries = []
    for other in all_pages:
        if other.id == page.id or not other.body_markdown:
            continue
        lead = other.body_markdown.strip().split("\n\n")[0][:300]
        neighbor_summaries.append({"title": other.title, "lead": lead})
        if len(neighbor_summaries) >= 8:
            break

    related_pages = compute_related_pages(page, all_pages, k=5)

    # Collect equations from dossier, deduplicate by normalized LaTeX.
    # Annotate with source_doc_ids from the corpus equation index when
    # available, so the writer can describe cross-paper provenance.
    equations_context: list[dict] = []
    if dossier:
        seen_latex: set[str] = set()
        raw_eqs = dossier.for_editor().get("equations", [])
        for eq in raw_eqs:
            latex = eq.get("latex", "")
            norm = " ".join(latex.split()).lower()
            if norm and norm not in seen_latex:
                seen_latex.add(norm)
                entry: dict = {
                    "latex": latex,
                    "label": eq.get("label", ""),
                    "kind": eq.get("kind", "mathematical"),
                    "context": eq.get("context", ""),
                }
                if equations_index is not None:
                    hit = equations_index.find_exact(norm)
                    if hit is not None:
                        entry["source_doc_ids"] = list(hit.source_doc_ids)
                equations_context.append(entry)

    is_person = page.kind == "person"
    artifact_text = cfg.person_artifact_text if is_person else cfg.artifact_text
    artifact_hash = cfg.person_artifact_hash if is_person else cfg.artifact_template_hash

    # Look up author context for person pages.
    page_author_context: dict | None = None
    if is_person and author_ctx:
        key = _author_key(page.title)
        ctx = author_ctx.get(key)
        if ctx is not None:
            page_author_context = {
                "primary_publications": [
                    {"doc_id": p.doc_id, "title": p.title, "year": p.year}
                    for p in ctx.primary_publications
                ],
                "cited_works": [
                    {"title": c.title, "year": c.year, "citing_doc_id": c.citing_doc_id}
                    for c in ctx.cited_works
                ],
                "collaborators": ctx.collaborators,
                "year_range": list(ctx.year_range) if ctx.year_range else None,
                "affiliations": ctx.affiliations,
            }

    evidence_refs = [
        WriteEvidenceRef(
            chunk_id=ev.chunk_id,
            doc_id=ev.doc_id,
            quote=ev.quote,
            locator=ev.locator,
        )
        for ev in page.evidence
    ]

    return WriteRequest(
        page_id=page.id,
        page_kind=page.kind,
        title=page.title,
        aliases=page.aliases,
        skeleton=page.body_markdown,
        evidence=evidence_refs,
        prompt_template=cfg.prompt_name,
        model_id=cfg.model_id,
        tier=cfg.writer_tier,
        figures=page_figures,
        style_guide=cfg.style_text,
        field_guide=cfg.field_text,
        artifact_template=artifact_text,
        corpus_persona=cfg.persona_text,
        style_guide_hash=cfg.style_guide_hash,
        field_guide_hash=cfg.field_guide_hash,
        artifact_template_hash=artifact_hash,
        corpus_persona_hash=cfg.corpus_persona_hash,
        brief=briefs.get(page.id),
        evidence_v2=evidence_v2,
        neighbor_summaries=neighbor_summaries,
        author_context=page_author_context,
        citation_context=(
            citation_context_for_docs(citation_index, page_doc_ids) if citation_index else {}
        ),
        cited_corpus_chunks=_build_cited_corpus_chunks(
            page, chunks_by_id, knowledge_graph,
        ) if knowledge_graph else {},
        dossier_context_yaml=dossier_context,
        related_pages=related_pages,
        equations_context=equations_context,
        verbalize=cfg.verbalize,
    )


def save_write_requests(
    bundle: BundlePaths,
    pages: list[WikiPage],
    briefs: dict[str, EditorBrief],
    dossier_store: DossierStore,
    chunks_by_id: dict[str, Chunk],
    images_index: ImageIndex,
    cfg: WriteRequestConfig,
    author_ctx: dict[str, AuthorContext] | None = None,
    citation_index: dict | None = None,
    knowledge_graph: object | None = None,
    equations_index: object | None = None,
) -> None:
    """Serialize WriteRequest JSONs to ``_write_requests/``."""
    out = bundle.write_requests_dir
    out.mkdir(parents=True, exist_ok=True)
    for page in pages:
        if not is_writable_page(page):
            continue
        req = build_write_request(
            page,
            pages,
            briefs,
            dossier_store,
            chunks_by_id,
            images_index,
            cfg,
            author_ctx,
            citation_index,
            knowledge_graph=knowledge_graph,
            equations_index=equations_index,
        )
        path = out / f"{page.id}.request.json"
        path.write_text(req.model_dump_json(indent=2), encoding="utf-8")


def save_pages_manifest(bundle: BundlePaths, pages: list[WikiPage]) -> None:
    """Save page list so the write phase can reload it."""
    out = bundle.write_requests_dir
    out.mkdir(parents=True, exist_ok=True)
    data = [dataclasses.asdict(p) for p in pages]
    (out / "_pages.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_pages_manifest(bundle: BundlePaths) -> list[WikiPage]:
    """Reload pages from the manifest saved by the extract phase."""
    manifest = bundle.write_requests_dir / "_pages.json"
    if not manifest.exists():
        raise FileNotFoundError(f"no pages manifest at {manifest}; run --phase extract first")
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    from wikify.models import Evidence

    return [
        WikiPage(
            id=d["id"],
            kind=d["kind"],
            title=d["title"],
            aliases=d.get("aliases", []),
            body_markdown=d.get("body_markdown", ""),
            evidence=[Evidence(**e) for e in d.get("evidence", [])],
            links=d.get("links", []),
            equations=d.get("equations", []),
            provenance=d.get("provenance", {}),
        )
        for d in raw
    ]


def _to_imageref(rec: ImageRecord) -> ImageRef:
    return ImageRef(
        id=rec.id,
        label=rec.label,
        caption=rec.caption,
        page=rec.page,
        path=rec.path,
        near_chunk_ids=list(rec.near_chunk_ids),
    )
