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
    WriteEvidenceRef,
    WriteRequest,
)
from ..infra.cost_meter import BudgetExceeded, CostMeter
from ..models import Chunk, Document, WikiPage
from ..paths import BundlePaths, CorpusPaths
from ..store.corpus import (
    all_chunks,
    list_documents,
    read_graph,
    read_vector_store,
)
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from .canonicalize import Candidate, canonicalize
from .crosslink import crosslink
from .sampler import Sampler, SamplerState
from .schedule import Schedule

EXTRACT_PROMPT = "wikify_simple/extract/v1"
WRITE_PROMPT = "wikify_simple/write/v1"


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
) -> None:
    bundle.ensure()
    rng = random.Random(strategy.seed)
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    vectors = read_vector_store(corpus)
    graph = read_graph(corpus)

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
    pages: list[WikiPage] = canonicalize(candidates, existing=[])
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


def _uniform_pagerank(doc_ids: list[str]) -> dict[str, float]:
    if not doc_ids:
        return {}
    w = 1.0 / len(doc_ids)
    return {d: w for d in doc_ids}
