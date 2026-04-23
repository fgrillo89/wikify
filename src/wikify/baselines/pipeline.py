"""Abstract-first source-grounded baseline.

Replaces the legacy retrieve-and-summarise + post-hoc-cite stack. The
goal of this baseline is to produce a non-agentic but corpus-grounded
reference point for `balanced` / `guided`: same default tiers, same
60/35/5 budget split, but no exploration loop and no graph walk after
each read.

Pipeline stages:
1. **Seed selection** — greedy submodular over corpus-citation PageRank
   plus document-embedding coverage. Caps seeds at the abstract-seeding
   budget (1/3 of the extract slice). One abstract-equivalent chunk per
   selected document.
2. **Extract** on the seed chunks to produce candidate concepts.
3. **Evidence retrieval** — for each candidate page, plain chunk
   similarity search with `top_k=8` and at most 2 chunks per source.
   Bounded by the remaining 2/3 of the extract slice.
4. **Write** through the same writer + write_prep stack as the standard
   pipeline so output format is comparable.

The seed-selection objective is exactly:

    score(d | S) = 0.7 * pr_norm(d) + 0.3 * coverage_gain(d | S)
    coverage_gain(d | S) = sum_u [ max_{s in S+{d}} max(0, cos(e_u, e_s))
                                  - max_{s in S}     max(0, cos(e_u, e_s)) ]

where ``e_x`` is the document embedding (mean-pooled chunk embeddings
over non-reference, non-caption chunks), and ``pr_norm`` is PageRank
linearly rescaled to ``[0, 1]`` over the corpus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..distill.author_context import build_author_context
from ..distill.dossier import Candidate, Dossier, DossierEntry, DossierStore, canonicalize
from ..distill.extract_request import (
    equations_for_chunk,
    figure_captions_for_chunk,
    normalize_title,
    resolve_citation_refs,
    to_imageref,
)
from ..distill.pipeline import EXTRACT_PROMPT, WRITE_PROMPT
from ..distill.preload import PreloadedCorpus
from ..distill.seed import (
    SeedSelectionConfig,
    doc_embeddings,
    greedy_seed_select,
    pagerank_normalised,
)
from ..distill.strategy import StaticBudget, StrategyConfig
from ..distill.write_prep import WriteRequestConfig, crosslink
from ..distill.write_runner import rebuild_wiki_graph, run_write_pass
from ..meter import BudgetExceededError, CostMeter
from ..models import Chunk, Document, WikiPage
from ..paths import BundlePaths
from ..prompts import (
    compose_writer_prompt_layer_hashes,
    load_artifact_template,
    load_field_guide,
    load_style_guide,
)
from ..prompts.registry import _content_hash
from ..schema import EditorBrief, ExtractRequest, QuoteNotInChunkError
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from ..types import Extractor, Writer

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph


# -- Configuration ---------------------------------------------------------


@dataclass(frozen=True)
class BaselineConfig:
    """Knobs for the abstract-first baseline.

    All defaults match the memo (docs/distill-test-readiness.md).

    Note: the baseline always uses a fixed 60/35/5 extract/write/curate
    split (``write_fraction = 0.35``) regardless of any strategy-level
    ``exploit_fraction_override``. This is what makes the baseline
    cross-comparable against ``balanced`` / ``guided`` at the run level.
    To ablate the split, edit ``BaselineConfig`` directly rather than
    passing ``--exploit-fraction``.

    Cost shaves the baseline takes on top of the standard write pass:

    - ``writer_skip_cited_corpus_chunks``: don't pass the knowledge graph
      to the writer, so ``_build_cited_corpus_chunks`` (graph-walk
      bonus context) returns empty. Aligns with the memo's "plain chunk
      similarity search only" rule and saves ~5-12k input tokens per
      write call.
    - ``writer_max_length_chars``: cap writer output via a planted
      default ``EditorBrief``. Without an editor, the brief is None and
      the writer drifts to 2-4k tokens of output; a tight cap saves
      ~30k heq per write at tier M.
    """

    # Hardwired write fraction; baseline does not honor strategy overrides.
    write_fraction: float = 0.35
    # Fraction of the extract budget consumed by abstract seeding.
    abstract_fraction: float = 1.0 / 3.0
    # PageRank weight in the greedy seed objective; coverage_gain weight is
    # ``1 - pagerank_weight``.
    pagerank_weight: float = 0.7
    # Per-page evidence retrieval.
    evidence_top_k: int = 8
    evidence_max_per_source: int = 2
    # Hard cap on number of seed documents the greedy ranker will return.
    # 20 seeds at ~5.5k heq per tier-S extract = ~110k heq for the full seed
    # pass, which the seed_extract_budget can only fund at budget ~550k heq
    # (see docs/distill-test-readiness.md budget math). On smaller budgets
    # the per-call meter gate stops before all 20 ranked seeds are read,
    # which is the intended behaviour.
    max_seeds: int = 20
    # Cost shaves on the writer side.
    writer_skip_cited_corpus_chunks: bool = True
    # 8000 chars ~ 2000 tokens output. Solid Wikipedia-article length;
    # CLAUDE.md forbids stubs. Drop this further only as a side experiment.
    writer_max_length_chars: int = 8000
    # Minimum evidence chunks a page must accumulate before the writer
    # is invoked on it. CLAUDE.md forbids stubs; a page with one or two
    # evidence chunks cannot ground a 2000-token article. The
    # baseline trades page count for per-page substance: better five
    # well-grounded pages than fifteen stubs. ``0`` disables the gate.
    min_evidence_chunks: int = 3


# -- Public entry ----------------------------------------------------------


def run_baseline(
    *,
    kg: KnowledgeGraph,
    bundle: BundlePaths,
    strategy: StrategyConfig | None = None,
    extractor: Extractor | None = None,
    writer: Writer | None = None,
    meter: CostMeter | None = None,
    budget_haiku_eq: float = 0.0,
    preloaded: PreloadedCorpus | None = None,
    config: BaselineConfig | None = None,
    field_name: str = "generic",
    artifact_name: str = "wiki_article",
    verbalize: bool = False,
    iteration: str = "create",
) -> list[WikiPage]:
    """Execute the abstract-first source-grounded baseline.

    The seed-selection logic is deterministic given the corpus and config;
    the extract / write stages share the dispatch / cache / cost-meter
    machinery used by ``distill.pipeline.run`` so cost reporting stays
    apples-to-apples with ``balanced`` and ``guided``.
    """
    cfg = config or BaselineConfig()
    if any(x is None for x in (strategy, extractor, writer, meter, preloaded)):
        raise ValueError(
            "run_baseline requires strategy, extractor, writer, meter, "
            "and preloaded -- the legacy stub-only signature is gone."
        )

    bundle.ensure()
    docs = preloaded.docs
    docs_by_id = preloaded.docs_by_id
    chunks = preloaded.chunks
    chunks_by_id = preloaded.chunks_by_id
    images_index = preloaded.images_index
    equations_index = preloaded.equations_index
    citation_index = preloaded.citation_index
    vectors = preloaded.vectors

    # Budget split: the baseline owns its own fixed 60/35/5 split.
    # Strategy-level exploit_fraction overrides are intentionally ignored
    # so cross-condition budget arithmetic stays comparable; ablating the
    # split requires editing BaselineConfig.write_fraction directly.
    split = StaticBudget(exploit_fraction=cfg.write_fraction).initial_split(budget_haiku_eq)
    extract_budget = split.extract_haiku_eq
    write_reserve = split.write_haiku_eq * 0.95
    seed_extract_budget = max(0.0, extract_budget * cfg.abstract_fraction)

    # 1. Seed selection — PageRank + submodular embedding coverage.
    # Greedy ranks up to ``max_seeds`` documents by score; the actual
    # extract loop's budget gate (seed_extract_budget) is what bounds
    # real spend. No avg-cost calibration to drift over time.
    seed_cfg = SeedSelectionConfig(
        pagerank_weight=cfg.pagerank_weight,
        max_seeds=cfg.max_seeds,
    )
    embeds, doc_order = doc_embeddings(chunks, vectors)
    pr_norm = pagerank_normalised(kg, doc_order)
    seed_doc_ids = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=cfg.max_seeds,
        cfg=seed_cfg,
    )
    # Read the canonical abstract chunk per seed doc from the fluent
    # KG API. The ingest-time abstract_tagger guarantees exactly one
    # ``section_type='abstract'`` chunk per body-bearing doc; the
    # accessor returns its id (or None on the rare empty doc, which we
    # silently skip).
    seed_chunk_ids = [
        chunk["id"]
        for did in seed_doc_ids
        if (chunk := kg.source(did).abstract_chunk()) is not None
    ]

    style_text = load_style_guide()
    field_text = load_field_guide(field_name)
    artifact_text = load_artifact_template(artifact_name)
    person_artifact_text = load_artifact_template("wiki_person")
    persona_text = preloaded.persona_text
    layer_hashes = compose_writer_prompt_layer_hashes(field_name, artifact_name)
    person_artifact_hash = _content_hash(person_artifact_text)
    corpus_persona_hash = _content_hash(persona_text) if persona_text else None
    write_req_cfg = WriteRequestConfig(
        model_id=strategy.write_tier.value,
        writer_tier=strategy.write_tier,
        prompt_name=WRITE_PROMPT,
        style_text=style_text,
        field_text=field_text,
        artifact_text=artifact_text,
        person_artifact_text=person_artifact_text,
        persona_text=persona_text,
        style_guide_hash=layer_hashes["style_guide"],
        field_guide_hash=layer_hashes["field_guide"],
        artifact_template_hash=layer_hashes["artifact_template"],
        person_artifact_hash=person_artifact_hash,
        corpus_persona_hash=corpus_persona_hash,
        verbalize=verbalize,
    )

    # 2. Seed extract — produce candidate concepts. Stops when the seed
    # extract budget (1/3 of the extract slice) is reached.
    seed_candidates: list[Candidate] = _extract_chunks(
        chunk_ids=seed_chunk_ids,
        chunks_by_id=chunks_by_id,
        docs_by_id=docs_by_id,
        kg=kg,
        images_index=images_index,
        extractor=extractor,
        meter=meter,
        budget_cap=seed_extract_budget,
        write_reserve=write_reserve,
        budget_haiku_eq=budget_haiku_eq,
        extract_tier=strategy.extract_tier,
        verbalize=verbalize,
    )

    # 3. Evidence retrieval — for each candidate page, top_k=8 with cap of
    # 2 chunks per source. Bounded by the remaining extract budget.
    # Pages are processed BEST-FIRST (highest seed-extract candidate count)
    # so when the meter gate fires the strongest pages keep their full
    # evidence rather than the budget being smeared across many thin pages.
    candidate_pages = canonicalize(seed_candidates, existing=[])
    candidate_pages = _rank_pages_by_seed_support(candidate_pages, seed_candidates)
    evidence_chunk_ids = _select_evidence_chunks(
        pages=candidate_pages,
        kg=kg,
        cfg=cfg,
        seed_chunk_ids=set(seed_chunk_ids),
    )
    extra_candidates: list[Candidate] = _extract_chunks(
        chunk_ids=evidence_chunk_ids,
        chunks_by_id=chunks_by_id,
        docs_by_id=docs_by_id,
        kg=kg,
        images_index=images_index,
        extractor=extractor,
        meter=meter,
        budget_cap=extract_budget,
        write_reserve=write_reserve,
        budget_haiku_eq=budget_haiku_eq,
        extract_tier=strategy.extract_tier,
        verbalize=verbalize,
    )

    all_candidates = seed_candidates + extra_candidates
    pages = canonicalize(all_candidates, existing=[])

    # 4. Build dossiers + briefs and run the standard write pass.
    # Cost shaves applied here:
    #   - briefs: a tight default brief per page caps writer output.
    #     Without this the writer drifts to 2-4k tokens at tier M.
    #   - kg_for_writer = None when ``writer_skip_cited_corpus_chunks``
    #     is True: skips the graph-walk top-3-per-cited-work bonus that
    #     ``_build_cited_corpus_chunks`` injects into the writer prompt.
    #     Aligns with the memo's "plain chunk similarity search only".
    dossier_store = DossierStore(bundle.root)
    _build_dossiers(pages, all_candidates, chunks_by_id, dossier_store)
    author_ctx = build_author_context(docs)

    # min_evidence_chunks gate: skip pages whose dossier is too thin to
    # ground a full article. CLAUDE.md forbids stubs. Pages below the
    # floor are tracked as skipped_thin so the snapshot can show how
    # many candidates were dropped for being under-supported.
    write_pages = [p for p in pages if len(p.evidence) >= cfg.min_evidence_chunks]
    skipped_thin = [
        {"page_id": p.id, "n_evidence": len(p.evidence)}
        for p in pages
        if len(p.evidence) < cfg.min_evidence_chunks
    ]

    briefs = {
        p.id: _baseline_brief(p, max_length_chars=cfg.writer_max_length_chars)
        for p in write_pages
    }
    kg_for_writer = None if cfg.writer_skip_cited_corpus_chunks else kg
    write_rejections: list[dict] = []
    run_write_pass(
        write_pages, len(write_pages), writer, meter, strategy, bundle, briefs,
        dossier_store, chunks_by_id, images_index, write_req_cfg, author_ctx,
        citation_index, kg_for_writer, budget_haiku_eq, verbalize, write_rejections,
        equations_index=equations_index,
    )

    pages = [p for p in pages if p.evidence]
    pages = crosslink(pages)
    for page in pages:
        page.provenance = {
            **(page.provenance or {}),
            "condition": "baseline",
            "run_id": meter._run_id,  # noqa: SLF001
            "iteration": iteration,
            "seed_doc_ids": seed_doc_ids,
        }
        write_page_file(bundle, page)
    build_index(bundle, pages).save()
    rebuild_wiki_graph(bundle, pages)
    meter.write_snapshot(bundle.run_path)

    snapshot = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    snapshot["strategy"] = "baseline"
    snapshot["seed"] = strategy.seed
    snapshot["mode"] = "baseline"
    snapshot["budget_target_haiku_eq"] = budget_haiku_eq
    snapshot["iteration"] = iteration
    snapshot["seed_doc_ids"] = seed_doc_ids
    snapshot["seed_chunks_read"] = seed_chunk_ids
    snapshot["evidence_chunks_read"] = evidence_chunk_ids
    snapshot["split_initial"] = {
        "extract_haiku_eq": split.extract_haiku_eq,
        "write_haiku_eq": split.write_haiku_eq,
        "curate_haiku_eq": split.curate_haiku_eq,
    }
    snapshot["seed_extract_budget"] = seed_extract_budget
    snapshot["baseline_write_fraction"] = cfg.write_fraction
    snapshot["min_evidence_chunks"] = cfg.min_evidence_chunks
    snapshot["skipped_thin_pages"] = skipped_thin
    snapshot["n_pages_written"] = len(write_pages)
    snapshot["write_rejections"] = write_rejections
    snapshot["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    bundle.run_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return pages


# -- Default brief (no editor pass) ---------------------------------------


def _rank_pages_by_seed_support(
    pages: list[WikiPage], seed_candidates: list[Candidate],
) -> list[WikiPage]:
    """Order pages by how many seed-extract candidates support them.

    Pages that show up across many seed chunks are the ones the corpus
    talks about most; they should get evidence-retrieval budget first
    so that when the meter gate fires the strongest pages are still
    fully grounded. Ties broken by original order.
    """
    page_id_set = {p.id: i for i, p in enumerate(pages)}
    support: dict[str, int] = {pid: 0 for pid in page_id_set}
    page_titles_by_id = {p.id: p.title for p in pages}
    title_norm_to_id = {normalize_title(p.title): p.id for p in pages}
    for c in seed_candidates:
        pid = title_norm_to_id.get(normalize_title(c.concept.title))
        if pid is None:
            for alias in c.concept.aliases:
                pid = title_norm_to_id.get(normalize_title(alias))
                if pid is not None:
                    break
        if pid is not None and pid in support:
            support[pid] += 1
    # Stable sort: most supported first, original order as tiebreaker.
    return sorted(
        pages,
        key=lambda p: (-support[p.id], page_id_set[p.id], page_titles_by_id[p.id]),
    )


def _baseline_brief(page: WikiPage, *, max_length_chars: int) -> EditorBrief:
    """Plant a minimal default brief so the writer has a length cap.

    The baseline does not run the editor. Without a brief, the writer
    receives ``brief=None`` and tends to drift to 2-4k tokens of output.
    A minimal brief with a tight ``max_length_chars`` keeps writes
    bounded — the savings are large at tier M where output costs 15x
    input.
    """
    return EditorBrief(
        page_id=page.id,
        title=page.title,
        article_register="academic",
        max_length_chars=max_length_chars,
    )


# -- Extract + evidence retrieval -----------------------------------------


def _extract_chunks(
    *,
    chunk_ids: list[str],
    chunks_by_id: dict[str, Chunk],
    docs_by_id: dict[str, Document],
    kg,
    images_index,
    extractor: Extractor,
    meter: CostMeter,
    budget_cap: float,
    write_reserve: float,
    budget_haiku_eq: float,
    extract_tier,
    verbalize: bool,
    batch_size: int = 4,
) -> list[Candidate]:
    """Send the given chunk ids through the extractor in batches.

    Uses ``extract_many`` for parallel dispatch when the binding exposes
    it (the file-dispatch binding does), so a batch of N round-trips
    happens concurrently rather than sequentially. Falls back to per-call
    ``extract`` when the binding doesn't.

    Stops when the cost meter reaches ``budget_cap`` or we would dip into
    the reserved write headroom. Per-chunk validator failures are skipped
    just like in the standard pipeline.
    """
    candidates: list[Candidate] = []
    if not chunk_ids:
        return candidates

    extract_many = getattr(extractor, "extract_many", None)
    cap = min(budget_cap, budget_haiku_eq - write_reserve)

    def _build_req(cid: str, ck: Chunk) -> ExtractRequest:
        return ExtractRequest(
            chunk_id=cid,
            chunk_text=ck.text,
            canonical_titles=[c.concept.title for c in candidates[-32:]],
            prompt_template=EXTRACT_PROMPT,
            model_id=extract_tier.value,
            tier=extract_tier,
            images_for_doc=[to_imageref(r) for r in images_index.for_doc(ck.doc_id)],
            equations=equations_for_chunk(ck, docs_by_id),
            figure_captions=figure_captions_for_chunk(ck, docs_by_id, images_index),
            verbalize=verbalize,
            citation_refs=resolve_citation_refs(ck.text, ck.doc_id, kg),
        )

    try:
        # Iterate in batches so the dispatch binding can fire requests in
        # parallel. canonical_titles only updates between batches, which
        # is fine — the cache-key contract doesn't depend on it.
        i = 0
        while i < len(chunk_ids):
            if meter.spent_haiku_eq >= cap:
                break
            batch_ids = chunk_ids[i : i + batch_size]
            i += batch_size
            batch_pairs: list[tuple[str, Chunk]] = []
            for cid in batch_ids:
                ck = chunks_by_id.get(cid)
                if ck is None:
                    continue
                batch_pairs.append((cid, ck))
            if not batch_pairs:
                continue
            reqs = [_build_req(cid, ck) for cid, ck in batch_pairs]
            if extract_many is not None:
                try:
                    resps = extract_many(reqs)
                except (ValidationError, QuoteNotInChunkError):
                    resps = []
                # extract_many now returns partial results — match by
                # chunk_id rather than zipping by position, since some
                # input slots may have been dropped due to per-item
                # validation errors.
                ck_by_id = {cid: ck for cid, ck in batch_pairs}
                for resp in resps:
                    cid = resp.chunk_id
                    ck = ck_by_id.get(cid)
                    if ck is None:
                        continue
                    for concept in resp.concepts:
                        candidates.append(
                            Candidate(concept=concept, chunk_id=cid, doc_id=ck.doc_id)
                        )
            else:
                for (cid, ck), req in zip(batch_pairs, reqs, strict=False):
                    try:
                        resp = extractor.extract(req)
                    except (ValidationError, QuoteNotInChunkError):
                        continue
                    for concept in resp.concepts:
                        candidates.append(
                            Candidate(concept=concept, chunk_id=cid, doc_id=ck.doc_id)
                        )
    except BudgetExceededError:
        pass
    return candidates


def _select_evidence_chunks(
    *,
    pages: list[WikiPage],
    kg,
    cfg: BaselineConfig,
    seed_chunk_ids: set[str],
) -> list[str]:
    """For each candidate page, pull top_k chunks and apply per-source cap."""
    out: list[str] = []
    seen = set(seed_chunk_ids)
    for page in pages:
        hits = kg.chunks().search(page.title, top_k=cfg.evidence_top_k * 4)
        per_doc: dict[str, int] = {}
        kept = 0
        for hit in hits:
            cid = hit.get("id") or hit.get("chunk_id")
            if not cid or cid in seen:
                continue
            doc_id = hit.get("source_id") or hit.get("doc_id") or ""
            if per_doc.get(doc_id, 0) >= cfg.evidence_max_per_source:
                continue
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            out.append(cid)
            seen.add(cid)
            kept += 1
            if kept >= cfg.evidence_top_k:
                break
    return out


def _build_dossiers(
    pages: list[WikiPage],
    candidates: list[Candidate],
    chunks_by_id: dict[str, Chunk],
    dossier_store: DossierStore,
) -> None:
    """Mirror distill.pipeline's dossier build so writers see the same shape."""
    alias_to_page: dict[str, WikiPage] = {}
    for p in pages:
        alias_to_page[normalize_title(p.title)] = p
        for a in getattr(p, "aliases", []) or []:
            alias_to_page[normalize_title(a)] = p

    for cand in candidates:
        c = cand.concept
        matched = alias_to_page.get(normalize_title(c.title))
        if matched is None:
            for alias in c.aliases:
                matched = alias_to_page.get(normalize_title(alias))
                if matched:
                    break
        if matched is None:
            continue
        dossier = dossier_store.load(matched.id) or Dossier(
            page_id=matched.id,
            title=matched.title,
            aliases=list(getattr(matched, "aliases", [])),
            kind=matched.kind,
            category=getattr(c, "category", None),
        )
        chunk = chunks_by_id.get(cand.chunk_id)
        dossier.add_entry(
            DossierEntry(
                chunk_id=cand.chunk_id,
                doc_id=cand.doc_id,
                quote=c.quote,
                definition=c.definition,
                summary=c.summary,
                parameters=[p.model_dump() for p in c.parameters] if c.parameters else [],
                mechanisms=list(c.mechanisms) if c.mechanisms else [],
                relationships=[r.model_dump() for r in c.relationships] if c.relationships else [],
                equations=[eq.model_dump() for eq in c.equations] if c.equations else [],
                section_type=chunk.section_type if chunk else "",
                figure_ids=list(c.evidence_figures),
            )
        )
        dossier_store.save(dossier)
