"""The fixed distillation loop. A function, not a class.

All state is passed in explicitly. No strategy-specific branches inside
the pipeline; strategy variation comes entirely from the injected sampler,
schedule, and tiering.

Supports staged execution via the ``phase`` parameter:

  ``extract`` — sample chunks, extract concepts, canonicalize, build
      dossiers, compact, run editor, save WriteRequest JSONs to the
      bundle's ``_write_requests/`` directory, then stop.

  ``write`` — load WriteRequest JSONs + pages manifest from the
      bundle, call the writer, crosslink, write pages to disk.

  ``all`` (default) — run both phases in one shot (default behaviour).

The staged split lets an orchestrator (e.g. Claude Code) process
write requests with model-backed subagents between phases.

The cost meter enforces the budget gate; the pipeline checks
``meter.spent_haiku_eq`` between iterations and stops cleanly when
budgets are exhausted.
"""

import dataclasses
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from ..contracts.protocols import Compactor, Editor, Extractor, Orchestrator, Writer
from ..contracts.schema import (
    EditorBrief,
    ExtractRequest,
    ImageRef,
    QuoteNotInChunkError,
    WriteRequest,
    WriteResponse,
)
from ..infra.cost_meter import BudgetExceededError, CostMeter
from ..ingest.sampler_index import load_sampler_index
from ..models import Chunk, Document, WikiPage
from ..paths import BundlePaths, CorpusPaths
from ..prompts import (
    load_artifact_template,
    load_field_guide,
    load_prompt,
    load_style_guide,
)
from ..store.corpus import (
    all_chunks,
    list_documents,
    read_graph,
    read_vector_store,
)
from ..store.images_index import ImageIndex, ImageRecord
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from .extract.canonicalize import Candidate, canonicalize
from .extract.dossier import Dossier, DossierEntry, DossierStore
from .iteration import (
    append_run_history,
    load_coverage_memory,
    load_existing_pages,
    run_merge_iteration,
    save_coverage_memory,
    updated_page_provenance,
)
from .policy import PolicyContext, PolicyName, PolicyRuntime, build_policy
from .sampler import (
    Sampler,
    SamplerState,
    apply_coverage_feedback,
    init_coverage_state,
    restore_coverage_state,
)
from .schedule import BudgetSplit, Schedule
from .write.author_context import build_author_context
from .write.crosslink import crosslink
from .write.requests import (
    WriteRequestConfig,
    build_write_request,
    is_writable_page,
    load_pages_manifest,
    save_pages_manifest,
    save_write_requests,
)


@dataclass
class StrategyConfig:
    name: str
    sampler: Sampler
    schedule: Schedule
    # Per-role tiers. Default tiers implement the spec:
    #   extract = S (haiku)
    #   write   = M (sonnet)
    #   edit    = M (sonnet)
    #   compact = S (haiku)
    #   orchestrate = L (opus, locked — not user-settable)
    extract_tier: str = "S"
    write_tier: str = "M"
    edit_tier: str = "M"
    compact_tier: str = "S"
    orchestrate_tier: str = "L"
    # Allocation override. When not None, replaces the schedule's
    # exploit_fraction for the initial split. The LLM policy can still
    # mutate the allocation mid-run via set_allocation actions.
    exploit_fraction_override: float | None = None
    model_id: str = "haiku"
    seed: int = 0
    field_name: str = "generic"
    artifact_name: str = "wiki_article"
    policy_name: str = "rule_policy"


Phase = Literal["all", "extract", "write"]
Iteration = Literal["create", "refine", "merge"]


EXTRACT_PROMPT = load_prompt("wikify_simple/extract").name
WRITE_PROMPT = load_prompt("wikify_simple/write").name


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
    iteration: Iteration = "create",
    merge_from_bundle: BundlePaths | None = None,
    editor: Editor | None = None,
    compactor: Compactor | None = None,
    orchestrator: Orchestrator | None = None,
    policy_name: str | None = None,
    compact_threshold: int = 10,
    phase: Phase = "all",
) -> None:
    if feed and iteration == "create":
        iteration = "refine"
    bundle.ensure()
    if iteration == "merge":
        run_merge_iteration(
            bundle,
            merge_from_bundle,
            meter,
            model_id=strategy.model_id,
            strategy_name=strategy.name,
        )
        return

    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    chunks_by_id: dict[str, Chunk] = {c.id: c for c in chunks}
    images_index = ImageIndex.load(corpus)

    # ---- write-only phase: skip extraction entirely ---------------------
    if phase == "write":
        pages = load_pages_manifest(bundle)
        _run_write_phase(
            bundle,
            pages,
            max_concepts,
            writer,
            meter,
            strategy,
            docs,
            iteration,
        )
        return

    # ---- extract + all: full setup needed --------------------------------
    existing_pages: list[WikiPage] = load_existing_pages(bundle) if iteration == "refine" else []
    cache_hits_start = getattr(getattr(extractor, "_cache", None), "hits", 0)
    cache_misses_start = getattr(getattr(extractor, "_cache", None), "misses", 0)
    rng = random.Random(strategy.seed)
    vectors = read_vector_store(corpus)
    graph = read_graph(corpus)

    style_text = load_style_guide()
    field_text = load_field_guide(strategy.field_name)
    artifact_text = load_artifact_template(strategy.artifact_name)
    person_artifact_text = load_artifact_template("wiki_person")
    persona_text = ""
    if corpus.persona_path.exists():
        persona_text = corpus.persona_path.read_text(encoding="utf-8").strip()
    write_req_cfg = WriteRequestConfig(
        model_id=strategy.model_id,
        writer_tier=strategy.write_tier,
        prompt_name=WRITE_PROMPT,
        style_text=style_text,
        field_text=field_text,
        artifact_text=artifact_text,
        person_artifact_text=person_artifact_text,
        persona_text=persona_text,
    )

    state = _build_sampler_state(rng, docs, chunks, graph, vectors, corpus=corpus)
    use_coverage_memory = iteration == "refine" and not feed
    if use_coverage_memory:
        mem = load_coverage_memory(bundle)
        restore_coverage_state(
            state,
            residuals=mem.get("coverage_residuals"),
            seen_chunks=set(mem.get("seen_chunks", [])),
            doc_seen_counts=mem.get("doc_seen_counts"),
        )

    # Mutable runtime seeded from the strategy's per-role tiers. The LLM
    # policy mutates this object in response to set_tier / set_allocation
    # actions; the pipeline reads it on every iteration.
    runtime = PolicyRuntime(
        extract_tier=strategy.extract_tier,
        write_tier=strategy.write_tier,
        edit_tier=strategy.edit_tier,
        compact_tier=strategy.compact_tier,
        orchestrate_tier=strategy.orchestrate_tier,
        exploit_fraction=strategy.exploit_fraction_override,
    )
    policy = build_policy(
        name=cast(PolicyName, policy_name or strategy.policy_name),
        sampler=strategy.sampler,
        orchestrator=orchestrator,
        runtime=runtime,
    )

    if runtime.exploit_fraction is not None:
        # Apply a user-supplied or LLM-supplied override by constructing
        # a one-shot StaticSchedule split. The strategy's own schedule is
        # still kept for reallocate() behaviour downstream.
        from .schedule import StaticSchedule

        split = StaticSchedule(exploit_fraction=runtime.exploit_fraction).initial_split(
            budget_haiku_eq
        )
    else:
        split = strategy.schedule.initial_split(budget_haiku_eq)
    last_allocation_epoch = runtime.allocation_epoch

    # Reserve 95% of the planned write budget before the extract loop so
    # the extract phase cannot consume headroom that the write phase needs.
    expected_write_reserve = split.write_haiku_eq * 0.95

    candidates: list[Candidate] = []
    chunks_read: list[str] = []
    extract_completed_normally = False
    split_initial = split
    policy_events: list[dict] = []
    write_rejections: list[dict] = []
    vision_requests: list[dict] = []

    # ---- extract loop ---------------------------------------------------
    try:
        while (
            meter.spent_haiku_eq
            < min(split.extract_haiku_eq, budget_haiku_eq - expected_write_reserve)
            and len(candidates) < max_concepts * 4
        ):
            # If the LLM policy changed the allocation, re-split the
            # REMAINING budget on the new exploit_fraction and continue.
            if runtime.allocation_epoch != last_allocation_epoch:
                from .schedule import StaticSchedule

                remaining = max(0.0, budget_haiku_eq - meter.spent_haiku_eq)
                new_split = StaticSchedule(
                    exploit_fraction=runtime.exploit_fraction or 0.5
                ).initial_split(remaining)
                split = BudgetSplit(
                    extract_haiku_eq=meter.spent_haiku_eq + new_split.extract_haiku_eq,
                    write_haiku_eq=new_split.write_haiku_eq,
                    curate_haiku_eq=new_split.curate_haiku_eq,
                )
                last_allocation_epoch = runtime.allocation_epoch
            ctx = _policy_context(
                run_id=meter._run_id,  # noqa: SLF001
                pages=existing_pages,
                candidates=candidates,
                docs_total=len(docs),
            )
            decision = policy.next_extract(state, extract_batch_size, ctx)
            policy_events.extend(policy.drain_events())
            batch = list(decision.batch)
            if decision.stop:
                break
            if not batch:
                # Control actions (set_allocation, set_tier) legitimately
                # return an empty batch. Continue the loop so the next
                # iteration picks up the new runtime settings.
                if decision.action in ("set_allocation", "set_tier"):
                    continue
                break
            # Build requests for all valid chunks in the batch.
            batch_chunks: list[tuple[str, Chunk]] = []
            for cid in batch:
                ck = chunks_by_id.get(cid)
                if ck is None:
                    continue
                chunks_read.append(cid)
                apply_coverage_feedback(state, cid, as_evidence=False)
                batch_chunks.append((cid, ck))

            batch_reqs = [
                ExtractRequest(
                    chunk_id=cid,
                    chunk_text=ck.text,
                    canonical_titles=[c.concept.title for c in candidates[-32:]],
                    prompt_template=EXTRACT_PROMPT,
                    model_id=strategy.model_id,
                    tier=runtime.extract_tier,
                    images_for_doc=[_to_imageref(r) for r in images_index.for_doc(ck.doc_id)],
                )
                for cid, ck in batch_chunks
            ]

            extract_many = getattr(extractor, "extract_many", None)
            if extract_many is not None:
                # Parallel dispatch: fire all requests, collect responses.
                # Per-chunk rejections must NOT crash the run; collect responses
                # then process each. extract_many raises on batch-level failure.
                try:
                    batch_resps = extract_many(batch_reqs)
                except (ValidationError, QuoteNotInChunkError):
                    batch_resps = []
                for (cid, ck), resp in zip(batch_chunks, batch_resps):
                    for concept in resp.concepts:
                        candidates.append(
                            Candidate(concept=concept, chunk_id=cid, doc_id=ck.doc_id)
                        )
                    if resp.concepts:
                        state.pages_concept_evidence_chunks.append(cid)
                        apply_coverage_feedback(state, cid, as_evidence=True)
                    # Gap 5: log needs_vision telemetry for future vision-on-demand.
                    if getattr(resp, "extra", None) and resp.extra.get("needs_vision"):  # type: ignore[union-attr]
                        vision_requests.append({"chunk_id": cid, "doc_id": ck.doc_id})
            else:
                # Serial fallback for bindings that don't implement extract_many.
                for (cid, ck), req in zip(batch_chunks, batch_reqs):
                    # Per-chunk rejections (validator failure, hallucinated quote)
                    # must NOT crash the run. Log via the .error.json artifact
                    # the binding already wrote, skip the chunk, keep going.
                    try:
                        resp = extractor.extract(req)
                    except (ValidationError, QuoteNotInChunkError):
                        continue
                    for concept in resp.concepts:
                        candidates.append(
                            Candidate(concept=concept, chunk_id=cid, doc_id=ck.doc_id)
                        )
                    # progressive seeding: any chunk we extracted from is now a
                    # valid local-walk seed for similarity_walk samplers.
                    if resp.concepts:
                        state.pages_concept_evidence_chunks.append(cid)
                        apply_coverage_feedback(state, cid, as_evidence=True)
                    # Gap 5: log needs_vision telemetry for future vision-on-demand.
                    if getattr(resp, "extra", None) and resp.extra.get("needs_vision"):  # type: ignore[union-attr]
                        vision_requests.append({"chunk_id": cid, "doc_id": ck.doc_id})
        extract_completed_normally = True
    except BudgetExceededError:
        pass

    # ---- adaptive reallocation -----------------------------------------
    # Static schedules return the same split (no-op); adaptive may shift
    # the remaining budget toward write when novelty drops below the
    # configured threshold.
    novelty_rate: float = 0.0
    if extract_completed_normally and chunks_read:
        unique_titles = {_normalize_title(c.concept.title) for c in candidates}
        novelty_rate = len(unique_titles) / len(chunks_read)
        remaining = max(budget_haiku_eq - meter.spent_haiku_eq, 0.0)
        new_split = strategy.schedule.reallocate(remaining=remaining, novelty_rate=novelty_rate)
        # Rebase: keep already-spent extract budget pinned, add the
        # reallocated extract/write/curate slices on top.
        split = BudgetSplit(
            extract_haiku_eq=meter.spent_haiku_eq + new_split.extract_haiku_eq,
            write_haiku_eq=new_split.write_haiku_eq,
            curate_haiku_eq=new_split.curate_haiku_eq,
        )

    # ---- canonicalize ---------------------------------------------------
    pages: list[WikiPage] = canonicalize(candidates, existing=existing_pages)
    # update sampler state with the chunks now in the wiki
    for p in pages:
        for ev in p.evidence:
            state.pages_concept_evidence_chunks.append(ev.chunk_id)
            apply_coverage_feedback(state, ev.chunk_id, as_evidence=True)

    # ---- build dossiers ---------------------------------------------------
    # Populate structured dossiers from extraction candidates. Dossiers
    # persist to disk so incremental runs (--feed) accumulate material.
    dossier_store = DossierStore(bundle.root)
    alias_to_page: dict[str, WikiPage] = {}
    for p in pages:
        alias_to_page[_normalize_title(p.title)] = p
        for a in p.aliases if hasattr(p, "aliases") else []:
            alias_to_page[_normalize_title(a)] = p

    for cand in candidates:
        c = cand.concept
        # Match by title or alias (same logic as canonicalize)
        matched = alias_to_page.get(_normalize_title(c.title))
        if matched is None:
            for alias in c.aliases:
                matched = alias_to_page.get(_normalize_title(alias))
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

    # ---- compact dossiers ------------------------------------------------
    # Concepts with many raw entries get consolidated via a cheap model call.
    if compactor is not None:
        for dossier in dossier_store.load_all():
            if dossier.n_entries <= compact_threshold:
                continue
            try:
                compacted = compactor.compact(
                    page_id=dossier.page_id,
                    title=dossier.title,
                    entries=[e.to_dict() for e in dossier.entries],
                )
                dossier.apply_compaction(compacted)
                dossier_store.save(dossier)
            except (ValidationError, BudgetExceededError):
                pass  # keep raw entries as-is

    # ---- editor pass: decide write-readiness + produce briefs -----------
    # The editor reads each dossier + the wiki index to decide which
    # concepts have enough substance for a page. It produces a brief
    # for each page it greenlights.
    briefs: dict[str, EditorBrief] = {}
    if editor is not None:
        existing_titles = [{"title": p.title, "id": p.id} for p in pages if p.body_markdown.strip()]
        for dossier in dossier_store.load_all():
            if dossier.kind == "person":
                continue
            if not dossier.has_substance:
                continue
            try:
                brief = editor.edit(
                    page_id=dossier.page_id,
                    title=dossier.title,
                    dossier=[dossier.for_editor()],
                    neighbors=existing_titles[-8:],
                )
                briefs[dossier.page_id] = brief
            except (ValidationError, BudgetExceededError):
                continue

    write_ctx = _policy_context(
        run_id=meter._run_id,  # noqa: SLF001
        pages=pages,
        candidates=candidates,
        docs_total=len(docs),
    )
    write_pages = policy.order_write_pages(pages, max_concepts, write_ctx)
    policy_events.extend(policy.drain_events())

    # ---- phase gate: save write requests or stop -------------------------
    author_ctx = build_author_context(docs)
    save_write_requests(
        bundle,
        write_pages,
        briefs,
        dossier_store,
        chunks_by_id,
        images_index,
        write_req_cfg,
        author_ctx,
    )
    if phase == "extract":
        save_pages_manifest(bundle, pages)
        _write_extract_snapshot(
            bundle,
            meter,
            strategy,
            budget_haiku_eq,
            chunks_read,
            extractor,
            cache_hits_start,
            cache_misses_start,
            split_initial,
            split,
            novelty_rate,
            iteration,
            policy_name or strategy.policy_name,
            policy_events,
        )
        save_coverage_memory(bundle, state, run_id=meter._run_id)  # noqa: SLF001
        return

    # ---- write loop (phase=all) -----------------------------------------
    # Rebuild write_req_cfg with the (possibly mutated) runtime write tier
    # so the LLM policy's set_tier actions take effect on writer calls.
    write_req_cfg = dataclasses.replace(write_req_cfg, writer_tier=runtime.write_tier)
    avg_write_cost = 30_000.0
    n_writes_completed = 0
    try:
        for page in write_pages:
            if not is_writable_page(page):
                continue
            # Pre-check: stop before issuing a write that would push spend
            # past the 1.05x hard ceiling.
            if meter.spent_haiku_eq + avg_write_cost > budget_haiku_eq * 1.05:
                write_rejections.append({"page_id": page.id, "reason": "budget_truncated"})
                continue
            _spent_before_call = meter.spent_haiku_eq
            req = build_write_request(
                page,
                pages,
                briefs,
                dossier_store,
                chunks_by_id,
                images_index,
                write_req_cfg,
                author_ctx,
            )
            try:
                resp = writer.write(req)
            except ValidationError as exc:
                import sys as _sys

                _sys.stderr.write(
                    f"[{meter._run_id}] writer REJECTED page={page.id!r}: "  # noqa: SLF001
                    f"{type(exc).__name__}: {str(exc)[:200]}\n"
                )
                write_rejections.append({"page_id": page.id, "error": str(exc)[:500]})
                continue
            page.body_markdown = resp.body_markdown
            # Update running mean of observed write costs.
            call_cost = meter.spent_haiku_eq - _spent_before_call
            n_writes_completed += 1
            avg_write_cost = (
                avg_write_cost * (n_writes_completed - 1) + call_cost
            ) / n_writes_completed
    except BudgetExceededError:
        pass

    _finalize_pages(bundle, pages, docs, meter, strategy, iteration)
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
    snapshot["feed"] = bool(iteration == "refine")
    snapshot["iteration"] = iteration
    snapshot["policy"] = policy_name or strategy.policy_name
    snapshot["policy_actions"] = policy_events
    snapshot["split_initial"] = {
        "extract_haiku_eq": split_initial.extract_haiku_eq,
        "write_haiku_eq": split_initial.write_haiku_eq,
        "curate_haiku_eq": split_initial.curate_haiku_eq,
    }
    snapshot["split_reallocated"] = {
        "extract_haiku_eq": split.extract_haiku_eq,
        "write_haiku_eq": split.write_haiku_eq,
        "curate_haiku_eq": split.curate_haiku_eq,
    }
    snapshot["novelty_rate_at_reallocation"] = novelty_rate
    snapshot["write_rejections"] = write_rejections
    snapshot["vision_requests"] = vision_requests
    snapshot["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    bundle.run_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    append_run_history(bundle, snapshot)
    save_coverage_memory(bundle, state, run_id=meter._run_id)  # noqa: SLF001


def _normalize_title(t: str) -> str:
    return " ".join(t.lower().split())


# --- sampler state -------------------------------------------------------


def _build_sampler_state(
    rng: random.Random,
    docs: list[Document],
    chunks: list[Chunk],
    graph,
    vectors,
    corpus: CorpusPaths | None = None,
) -> SamplerState:
    import sys

    # Try to load the pre-computed index written by ingest.
    idx = load_sampler_index(corpus.sampler_index_path) if corpus is not None else None
    if idx is not None and corpus is not None:
        # Assemble SamplerState from the persisted index.
        neighbour_map = {cid: tuple(ns) for cid, ns in idx["neighbors_by_chunk"].items()}
        # Load real pagerank if available, fall back to uniform.
        pagerank = _load_pagerank(corpus)
        if not pagerank:
            pagerank = _uniform_pagerank(idx["doc_ids_sorted"])
        all_chunk_ids = idx["content_chunk_ids"] + idx["caption_chunk_ids"]
        caption_ids: set[str] = set(idx["caption_chunk_ids"])
        state = SamplerState(
            rng=rng,
            graph=graph,
            vectors=vectors,
            chunks_by_doc=idx["chunks_by_doc"],
            abstract_chunk_by_doc=idx["abstract_chunk_by_doc"],
            pagerank_doc=pagerank,
            neighbors_by_chunk=neighbour_map,
            chunk_degree=idx["chunk_degree"],
            chunk_to_doc=idx["chunk_to_doc"],
            caption_chunk_ids=caption_ids,
        )
        init_coverage_state(state, all_chunk_ids)
        return state

    # Older corpus without sampler_index.json: fall back to in-memory build.
    sys.stderr.write(
        "[pipeline] sampler_index.json not found; rebuilding in memory (re-ingest to pre-compute)\n"
    )
    chunks_by_doc: dict[str, list[str]] = defaultdict(list)
    abstract_by_doc: dict[str, str] = {}
    chunk_to_doc: dict[str, str] = {}
    all_chunk_ids_fb: list[str] = []
    for c in chunks:
        chunks_by_doc[c.doc_id].append(c.id)
        chunk_to_doc[c.id] = c.doc_id
        all_chunk_ids_fb.append(c.id)
        if c.id not in abstract_by_doc:
            abstract_by_doc[c.doc_id] = c.id  # first chunk == abstract proxy
    pagerank = _uniform_pagerank(list(chunks_by_doc.keys()))
    neighbours: dict[str, set[str]] = defaultdict(set)
    for a, b in graph.edges.get("similar_strong", []):
        neighbours[a].add(b)
        neighbours[b].add(a)
    for a, b in graph.edges.get("co_section", []):
        neighbours[a].add(b)
        neighbours[b].add(a)
    neighbour_map_fb = {cid: tuple(sorted(ns)) for cid, ns in neighbours.items()}
    degree = {cid: len(neighbour_map_fb.get(cid, ())) for cid in all_chunk_ids_fb}
    state = SamplerState(
        rng=rng,
        graph=graph,
        vectors=vectors,
        chunks_by_doc=dict(chunks_by_doc),
        abstract_chunk_by_doc=abstract_by_doc,
        pagerank_doc=pagerank,
        neighbors_by_chunk=neighbour_map_fb,
        chunk_degree=degree,
        chunk_to_doc=chunk_to_doc,
    )
    init_coverage_state(state, all_chunk_ids_fb)
    return state


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


def _load_pagerank(corpus: CorpusPaths) -> dict[str, float]:
    """Load pre-computed pagerank from disk, or return empty dict if absent."""
    p = corpus.pagerank_path
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _finalize_pages(
    bundle: BundlePaths,
    pages: list[WikiPage],
    docs: list[Document],
    meter: CostMeter,
    strategy: StrategyConfig,
    iteration: Iteration,
) -> None:
    """Crosslink, persist files/index, snapshot meter.

    Person candidates come from the extractor (kind='person' entries).
    No deterministic author-page generation; the writer produces all prose.
    """
    pages = [p for p in pages if p.evidence]
    pages = crosslink(pages)
    for page in pages:
        page.provenance = updated_page_provenance(
            existing=(page.provenance or {}),
            run_id=meter._run_id,  # noqa: SLF001
            model_id=strategy.model_id,
            strategy_name=strategy.name,
            iteration=iteration,
            drafted=bool(page.body_markdown.strip()),
        )
        write_page_file(bundle, page)
    build_index(bundle, pages).save()
    meter.write_snapshot(bundle.run_path)


def _run_write_phase(
    bundle: BundlePaths,
    pages: list[WikiPage],
    max_concepts: int,
    writer: Writer,
    meter: CostMeter,
    strategy: StrategyConfig,
    docs: list[Document],
    iteration: Iteration,
) -> None:
    """Execute the write phase: load requests/responses, call writer, crosslink."""
    write_dir = bundle.write_requests_dir

    try:
        for page in pages[:max_concepts]:
            if not is_writable_page(page):
                continue
            # Check for pre-computed response (from subagent processing)
            resp_path = write_dir / f"{page.id}.response.json"
            if resp_path.exists():
                try:
                    raw = json.loads(resp_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    _write_staged_response_error(
                        resp_path,
                        {"_raw": resp_path.read_text(encoding="utf-8")},
                        exc,
                    )
                    raw = {}
                try:
                    staged = WriteResponse.model_validate(raw)
                except ValidationError as exc:
                    _write_staged_response_error(resp_path, raw, exc)
                else:
                    page.body_markdown = staged.body_markdown
                    continue
            # Load saved request and call writer binding
            req_path = write_dir / f"{page.id}.request.json"
            if not req_path.exists():
                continue
            req = WriteRequest.model_validate_json(req_path.read_text(encoding="utf-8"))
            try:
                resp = writer.write(req)
            except ValidationError as exc:
                import sys as _sys

                _sys.stderr.write(
                    f"[{meter._run_id}] writer REJECTED page={page.id!r}: "  # noqa: SLF001
                    f"{type(exc).__name__}: {str(exc)[:200]}\n"
                )
                continue
            page.body_markdown = resp.body_markdown
    except BudgetExceededError:
        pass

    _finalize_pages(bundle, pages, docs, meter, strategy, iteration)
    snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    snap["iteration"] = iteration
    snap["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    bundle.run_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    append_run_history(bundle, snap)


def _write_extract_snapshot(
    bundle: BundlePaths,
    meter: CostMeter,
    strategy: StrategyConfig,
    budget_haiku_eq: float,
    chunks_read: list[str],
    extractor: Extractor,
    cache_hits_start: int,
    cache_misses_start: int,
    split_initial: BudgetSplit,
    split: BudgetSplit,
    novelty_rate: float,
    iteration: Iteration,
    policy_name: str,
    policy_events: list[dict],
) -> None:
    """Write a partial run snapshot after the extract phase."""
    meter.write_snapshot(bundle.run_path)
    snapshot = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    snapshot["phase"] = "extract"
    snapshot["chunks_read"] = chunks_read
    snapshot["strategy"] = strategy.name
    snapshot["seed"] = strategy.seed
    snapshot["budget_target_haiku_eq"] = budget_haiku_eq
    cache = getattr(extractor, "_cache", None)
    hits_delta = (cache.hits - cache_hits_start) if cache is not None else 0
    misses_delta = (cache.misses - cache_misses_start) if cache is not None else 0
    snapshot["n_cached_skipped"] = hits_delta
    snapshot["n_new_extracted"] = misses_delta
    snapshot["feed"] = bool(iteration == "refine")
    snapshot["iteration"] = iteration
    snapshot["policy"] = policy_name
    snapshot["policy_actions"] = policy_events
    snapshot["split_initial"] = {
        "extract_haiku_eq": split_initial.extract_haiku_eq,
        "write_haiku_eq": split_initial.write_haiku_eq,
        "curate_haiku_eq": split_initial.curate_haiku_eq,
    }
    snapshot["split_reallocated"] = {
        "extract_haiku_eq": split.extract_haiku_eq,
        "write_haiku_eq": split.write_haiku_eq,
        "curate_haiku_eq": split.curate_haiku_eq,
    }
    snapshot["novelty_rate_at_reallocation"] = novelty_rate
    wr_dir = bundle.write_requests_dir
    n_reqs = len(list(wr_dir.glob("*.request.json"))) if wr_dir.exists() else 0
    snapshot["n_write_requests"] = n_reqs
    snapshot["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    bundle.run_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    append_run_history(bundle, snapshot)


def _policy_context(
    *,
    run_id: str,
    pages: list[WikiPage],
    candidates: list[Candidate],
    docs_total: int,
) -> PolicyContext:
    page_ids = {p.id for p in pages}
    n_concepts = sum(1 for p in pages if p.kind == "article")
    n_people = sum(1 for p in pages if p.kind == "person")
    docs_covered = len({ev.doc_id for p in pages for ev in p.evidence})
    return PolicyContext(
        run_id=run_id,
        n_pages=len(page_ids),
        n_candidates=len(candidates),
        n_concepts=n_concepts,
        n_people=n_people,
        docs_covered=docs_covered,
        docs_total=docs_total,
    )


def _write_staged_response_error(resp_path: Path, raw: dict, exc: Exception) -> None:
    err_path = resp_path.with_name(resp_path.name.replace(".response.", ".error."))
    payload = {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "schema": "WriteResponse",
        "raw": raw,
    }
    err_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
