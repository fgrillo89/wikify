"""WriteRequest construction and staged request persistence."""

import dataclasses
import json
from dataclasses import dataclass

from wikify_simple.contracts.schema import (
    EditorBrief,
    ImageRef,
    WriteEvidenceRef,
    WriteEvidenceRefV2,
    WriteRequest,
)
from wikify_simple.models import Chunk, WikiPage
from wikify_simple.paths import BundlePaths
from wikify_simple.store.images_index import ImageIndex, ImageRecord

from ..extract.dossier import DossierEntry, DossierStore


@dataclass(frozen=True)
class WriteRequestConfig:
    model_id: str
    writer_tier: str
    prompt_name: str
    style_text: str
    field_text: str
    artifact_text: str
    person_artifact_text: str
    persona_text: str


def is_writable_page(page: WikiPage) -> bool:
    """Return whether a page should be sent to writer/runtime draft path."""
    if not page.evidence:
        return False
    if page.kind == "person" and len(page.evidence) < 2:
        return False
    return True


def build_write_request(
    page: WikiPage,
    all_pages: list[WikiPage],
    briefs: dict[str, EditorBrief],
    dossier_store: DossierStore,
    chunks_by_id: dict[str, Chunk],
    images_index: ImageIndex,
    cfg: WriteRequestConfig,
) -> WriteRequest:
    """Build a WriteRequest for a single page."""
    page_doc_ids = {ev.doc_id for ev in page.evidence}
    page_figures: list[ImageRef] = []
    seen_fig_ids: set[str] = set()
    for did in sorted(page_doc_ids):
        for rec in images_index.for_doc(did):
            if rec.id in seen_fig_ids:
                continue
            seen_fig_ids.add(rec.id)
            page_figures.append(_to_imageref(rec))

    evidence_v2 = []
    dossier = dossier_store.load(page.id)
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

    return WriteRequest(
        page_id=page.id,
        page_kind=page.kind,
        title=page.title,
        aliases=page.aliases,
        skeleton=page.body_markdown,
        evidence=[
            WriteEvidenceRef(
                chunk_id=ev.chunk_id,
                doc_id=ev.doc_id,
                quote=ev.quote,
                locator=ev.locator,
            )
            for ev in page.evidence
        ],
        neighbor_titles=[p.title for p in all_pages if p.id != page.id][:8],
        prompt_template=cfg.prompt_name,
        model_id=cfg.model_id,
        tier=cfg.writer_tier,
        figures=page_figures,
        style_guide=cfg.style_text,
        field_guide=cfg.field_text,
        artifact_template=cfg.person_artifact_text if page.kind == "person" else cfg.artifact_text,
        corpus_persona=cfg.persona_text,
        brief=briefs.get(page.id),
        evidence_v2=evidence_v2,
        neighbor_summaries=neighbor_summaries,
    )


def save_write_requests(
    bundle: BundlePaths,
    pages: list[WikiPage],
    briefs: dict[str, EditorBrief],
    dossier_store: DossierStore,
    chunks_by_id: dict[str, Chunk],
    images_index: ImageIndex,
    cfg: WriteRequestConfig,
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
    from wikify_simple.models import Evidence

    return [
        WikiPage(
            id=d["id"],
            kind=d["kind"],
            title=d["title"],
            aliases=d.get("aliases", []),
            body_markdown=d.get("body_markdown", ""),
            evidence=[Evidence(**e) for e in d.get("evidence", [])],
            links=d.get("links", []),
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
    )
