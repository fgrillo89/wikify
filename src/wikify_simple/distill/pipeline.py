"""The fixed distillation loop. A function, not a class.

All state is passed in explicitly. No strategy-specific branches inside
the pipeline; strategy variation comes entirely from the injected sampler,
schedule, and tiering. The pipeline:

  1. profile the corpus
  2. extract candidates from sampled chunks (loop until extract budget spent)
  3. canonicalize candidates -> WikiPage skeletons
  4. write each page (loop until write budget spent)
  5. crosslink the pages
  6. write the pages to disk + emit the run snapshot

The cost meter enforces the budget gate; the pipeline checks
``meter.spent_haiku_eq`` between iterations and stops cleanly when
budgets are exhausted.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass

from ..agents.protocols import Extractor, Writer
from ..agents.schema import (
    ExtractRequest,
    ImageRef,
    WriteEvidenceRef,
    WriteRequest,
)
from ..infra.cost_meter import BudgetExceeded, CostMeter
from ..models import Chunk, Document, WikiPage
from ..models import Evidence as PageEvidence
from ..paths import BundlePaths, CorpusPaths
from ..prompts import load_prompt
from ..store.corpus import (
    all_chunks,
    list_documents,
    read_graph,
    read_vector_store,
)
from ..store.images_index import ImageIndex, ImageRecord
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from .canonicalize import Candidate, canonicalize
from .crosslink import crosslink
from .sampler import Sampler, SamplerState
from .schedule import Schedule

EXTRACT_PROMPT = load_prompt("wikify_simple/extract/v1").name
WRITE_PROMPT = load_prompt("wikify_simple/write/v1").name


@dataclass
class StrategyConfig:
    name: str
    sampler: Sampler
    schedule: Schedule
    tier_explore: str
    tier_exploit: str
    model_id: str = "haiku"
    seed: int = 0


def run(
    *,
    corpus: CorpusPaths,
    bundle: BundlePaths,
    strategy: StrategyConfig,
    extractor: Extractor,
    writer: Writer,
    meter: CostMeter,
    budget_haiku_eq: float,
    extract_batch_size: int = 4,
    max_concepts: int = 60,
    feed: bool = False,
) -> None:
    bundle.ensure()
    existing_pages: list[WikiPage] = _load_existing_pages(bundle) if feed else []
    cache_hits_start = getattr(getattr(extractor, "_cache", None), "hits", 0)
    cache_misses_start = getattr(getattr(extractor, "_cache", None), "misses", 0)
    rng = random.Random(strategy.seed)
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    vectors = read_vector_store(corpus)
    graph = read_graph(corpus)
    images_index = ImageIndex.load(corpus)

    state = _build_sampler_state(rng, docs, chunks, graph, vectors)
    chunks_by_id: dict[str, Chunk] = {c.id: c for c in chunks}
    docs_by_id: dict[str, Document] = {d.id: d for d in docs}

    split = strategy.schedule.initial_split(budget_haiku_eq)

    candidates: list[Candidate] = []
    chunks_read: list[str] = []

    # ---- extract loop ---------------------------------------------------
    try:
        while meter.spent_haiku_eq < split.extract_haiku_eq and len(candidates) < max_concepts * 4:
            batch = strategy.sampler.next_batch(state, extract_batch_size)
            if not batch:
                break
            for cid in batch:
                ck = chunks_by_id.get(cid)
                if ck is None:
                    continue
                chunks_read.append(cid)
                req = ExtractRequest(
                    chunk_id=cid,
                    chunk_text=ck.text,
                    canonical_titles=[c.concept.title for c in candidates[-32:]],
                    prompt_template=EXTRACT_PROMPT,
                    model_id=strategy.model_id,
                    tier=strategy.tier_explore,
                    images_for_doc=[_to_imageref(r) for r in images_index.for_doc(ck.doc_id)],
                )
                resp = extractor.extract(req)
                for concept in resp.concepts:
                    candidates.append(
                        Candidate(
                            concept=concept,
                            chunk_id=cid,
                            doc_id=ck.doc_id,
                        )
                    )
                # progressive seeding: any chunk we extracted from is now a
                # valid local-walk seed for similarity_walk samplers.
                if resp.concepts:
                    state.pages_concept_evidence_chunks.append(cid)
    except BudgetExceeded:
        pass

    # ---- canonicalize ---------------------------------------------------
    pages: list[WikiPage] = canonicalize(candidates, existing=existing_pages)
    # update sampler state with the chunks now in the wiki
    for p in pages:
        for ev in p.evidence:
            state.pages_concept_evidence_chunks.append(ev.chunk_id)

    # ---- write loop -----------------------------------------------------
    write_target = split.extract_haiku_eq + split.write_haiku_eq
    try:
        for page in pages[:max_concepts]:
            if meter.spent_haiku_eq >= write_target:
                break
            page_doc_ids = {ev.doc_id for ev in page.evidence}
            page_figures: list[ImageRef] = []
            seen_fig_ids: set[str] = set()
            for did in sorted(page_doc_ids):
                for rec in images_index.for_doc(did):
                    if rec.id in seen_fig_ids:
                        continue
                    seen_fig_ids.add(rec.id)
                    page_figures.append(_to_imageref(rec))
            req = WriteRequest(
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
                neighbor_titles=[p.title for p in pages if p.id != page.id][:8],
                prompt_template=WRITE_PROMPT,
                model_id=strategy.model_id,
                tier=strategy.tier_exploit,
                figures=page_figures,
            )
            resp = writer.write(req)
            page.body_markdown = resp.body_markdown
    except BudgetExceeded:
        pass

    # ---- crosslink + write to disk -------------------------------------
    pages = [p for p in pages if p.evidence]  # drop unsupported skeletons
    pages = crosslink(pages)
    for page in pages:
        page.provenance = {
            "run_id": meter._run_id,  # noqa: SLF001 — operational only
            "model": strategy.model_id,
            "strategy": strategy.name,
        }
        write_page_file(bundle, page)

    # write the bundle index so canonicalize/crosslink/eval don't have to
    # walk the directory and re-parse every page on subsequent runs.
    build_index(bundle, pages).save()

    meter.write_snapshot(bundle.run_path)
    snapshot = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    snapshot["chunks_read"] = chunks_read
    snapshot["strategy"] = strategy.name
    snapshot["seed"] = strategy.seed
    snapshot["budget_target_haiku_eq"] = budget_haiku_eq
    cache = getattr(extractor, "_cache", None)
    hits_delta = (cache.hits - cache_hits_start) if cache is not None else 0
    misses_delta = (cache.misses - cache_misses_start) if cache is not None else 0
    snapshot["n_cached_skipped"] = hits_delta
    snapshot["n_new_extracted"] = misses_delta
    snapshot["feed"] = bool(feed)
    bundle.run_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


# --- sampler state -------------------------------------------------------


def _build_sampler_state(
    rng: random.Random,
    docs: list[Document],
    chunks: list[Chunk],
    graph,
    vectors,
) -> SamplerState:
    chunks_by_doc: dict[str, list[str]] = defaultdict(list)
    abstract_by_doc: dict[str, str] = {}
    for c in chunks:
        chunks_by_doc[c.doc_id].append(c.id)
        if c.id not in abstract_by_doc:
            abstract_by_doc[c.doc_id] = c.id  # first chunk == abstract proxy
    pagerank = _uniform_pagerank(list(chunks_by_doc.keys()))
    return SamplerState(
        rng=rng,
        graph=graph,
        vectors=vectors,
        chunks_by_doc=dict(chunks_by_doc),
        abstract_chunk_by_doc=abstract_by_doc,
        pagerank_doc=pagerank,
    )


def _load_existing_pages(bundle: BundlePaths) -> list[WikiPage]:
    """Load prior wiki pages from a bundle dir as ``WikiPage`` objects.

    Used by ``--feed`` so canonicalize can merge new candidates into the
    existing alias map instead of starting from scratch.
    """
    from ..eval.bundle import _parse_page

    pages: list[WikiPage] = []
    for sub in ("concepts", "people"):
        d = bundle.root / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                parsed = _parse_page(f)
            except Exception:
                continue
            evidence = [
                PageEvidence(
                    marker=ev.marker,
                    chunk_id=ev.chunk_id,
                    doc_id=ev.doc_id,
                    quote=ev.quote,
                    locator=ev.locator,
                )
                for ev in parsed.evidence
            ]
            pages.append(
                WikiPage(
                    id=parsed.id,
                    kind=parsed.kind,
                    title=parsed.title,
                    aliases=list(parsed.aliases),
                    body_markdown=parsed.body_clean,
                    evidence=evidence,
                    links=list(parsed.links),
                )
            )
    return pages


def _to_imageref(rec: ImageRecord) -> ImageRef:
    return ImageRef(
        id=rec.id,
        label=rec.label,
        caption=rec.caption,
        page=rec.page,
        path=rec.path,
    )


def _uniform_pagerank(doc_ids: list[str]) -> dict[str, float]:
    if not doc_ids:
        return {}
    w = 1.0 / len(doc_ids)
    return {d: w for d in doc_ids}
