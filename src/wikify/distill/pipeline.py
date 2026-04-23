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
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from ..meter import BudgetExceededError, CostMeter
from ..models import Chunk, Document, WikiPage
from ..paths import BundlePaths, CorpusPaths
from ..prompts import (
    compose_writer_prompt_layer_hashes,
    load_artifact_template,
    load_field_guide,
    load_prompt,
    load_style_guide,
)
from ..prompts.registry import _content_hash
from ..schema import (
    EditorBrief,
    ExtractRequest,
    QuoteNotInChunkError,
    WriteRequest,
    WriteResponse,
)
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from ..types import Compactor, Editor, Extractor, Orchestrator, Writer
from .author_context import build_author_context
from .dossier import (
    SKIP_SECTION_TYPES,
    Candidate,
    Dossier,
    DossierEntry,
    DossierStore,
    canonicalize,
)
from .explorer import (
    ExplorerState,
    apply_coverage_feedback,
    build_snapshot,
    init_coverage_state,
    restore_coverage_state,
)
from .extract_request import (
    equations_for_chunk as _equations_for_chunk,
)
from .extract_request import (
    figure_captions_for_chunk as _figure_captions_for_chunk,
)
from .extract_request import (
    normalize_title as _normalize_title,
)
from .extract_request import (
    resolve_citation_refs as _resolve_citation_refs,
)
from .extract_request import (
    to_imageref as _to_imageref,
)
from .iteration import (
    append_run_history,
    load_coverage_memory,
    load_existing_pages,
    run_merge_iteration,
    save_coverage_memory,
    updated_page_provenance,
)
from .preload import PreloadedCorpus, preload_corpus
from .strategy import (
    BudgetSplit,
    ModeContext,
    ModeName,
    RuntimeOverrides,
    StaticBudget,
    StrategyConfig,
    build_mode,
)
from .write_prep import (
    WriteRequestConfig,
    crosslink,
    is_writable_page,
    load_pages_manifest,
    save_pages_manifest,
    save_write_requests,
)
from .write_runner import append_verbalize as _append_verbalize
from .write_runner import rebuild_wiki_graph as _rebuild_wiki_graph
from .write_runner import run_write_pass as _run_write_pass

Phase = Literal["all", "extract", "write"]
Iteration = Literal["create", "refine", "merge"]


EXTRACT_PROMPT = load_prompt("wikify/extract").name
WRITE_PROMPT = load_prompt("wikify/write").name


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
    iteration: Iteration = "create",
    merge_from_bundle: BundlePaths | None = None,
    editor: Editor | None = None,
    compactor: Compactor | None = None,
    orchestrator: Orchestrator | None = None,
    mode_name: str | None = None,
    field_name: str = "generic",
    artifact_name: str = "wiki_article",
    compact_threshold: int = 10,
    phase: Phase = "all",
    verbalize: bool = False,
    allowed_tools: frozenset[str] | None = None,
) -> None:
    """Thin wrapper: load corpus once then delegate to run_with_preloaded."""
    preloaded = preload_corpus(corpus)
    run_with_preloaded(
        preloaded=preloaded,
        bundle=bundle,
        strategy=strategy,
        extractor=extractor,
        writer=writer,
        meter=meter,
        budget_haiku_eq=budget_haiku_eq,
        extract_batch_size=extract_batch_size,
        max_concepts=max_concepts,
        iteration=iteration,
        merge_from_bundle=merge_from_bundle,
        editor=editor,
        compactor=compactor,
        orchestrator=orchestrator,
        mode_name=mode_name,
        field_name=field_name,
        artifact_name=artifact_name,
        compact_threshold=compact_threshold,
        phase=phase,
        verbalize=verbalize,
        allowed_tools=allowed_tools,
    )


def run_with_preloaded(
    *,
    preloaded: PreloadedCorpus,
    bundle: BundlePaths,
    strategy: StrategyConfig,
    extractor: Extractor,
    writer: Writer,
    meter: CostMeter,
    budget_haiku_eq: float,
    extract_batch_size: int = 4,
    max_concepts: int = 60,
    iteration: Iteration = "create",
    merge_from_bundle: BundlePaths | None = None,
    editor: Editor | None = None,
    compactor: Compactor | None = None,
    orchestrator: Orchestrator | None = None,
    mode_name: str | None = None,
    field_name: str = "generic",
    artifact_name: str = "wiki_article",
    compact_threshold: int = 10,
    phase: Phase = "all",
    verbalize: bool = False,
    allowed_tools: frozenset[str] | None = None,
) -> None:
    effective_mode_name = mode_name or "scripted"
    bundle.ensure()
    if iteration == "merge":
        run_merge_iteration(
            bundle,
            merge_from_bundle,
            meter,
            model_id=strategy.write_tier.value,
            strategy_name=strategy.name,
        )
        return

    docs = preloaded.docs
    docs_by_id = preloaded.docs_by_id
    chunks = preloaded.chunks
    chunks_by_id = preloaded.chunks_by_id
    images_index = preloaded.images_index
    equations_index = preloaded.equations_index
    citation_index = preloaded.citation_index

    knowledge_graph = preloaded.knowledge_graph

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

    style_text = load_style_guide()
    field_text = load_field_guide(field_name)
    artifact_text = load_artifact_template(artifact_name)
    person_artifact_text = load_artifact_template("wiki_person")
    persona_text = preloaded.persona_text
    layer_hashes = compose_writer_prompt_layer_hashes(field_name, artifact_name)
    person_artifact_hash = _content_hash(person_artifact_text)
    corpus_persona_hash = _content_hash(persona_text) if persona_text else None
    _write_prompt_layer_files(
        bundle,
        {
            layer_hashes["style_guide"]: style_text,
            layer_hashes["field_guide"]: field_text,
            layer_hashes["artifact_template"]: artifact_text,
            person_artifact_hash: person_artifact_text,
            **(
                {corpus_persona_hash: persona_text}
                if corpus_persona_hash is not None
                else {}
            ),
        },
    )
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

    state = _build_explorer_state(rng, chunks, knowledge_graph)
    use_coverage_memory = iteration == "refine"
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
    runtime = RuntimeOverrides(
        extract_tier=strategy.extract_tier,
        write_tier=strategy.write_tier,
        edit_tier=strategy.edit_tier,
        compact_tier=strategy.compact_tier,
        orchestrate_tier=strategy.orchestrate_tier,
        exploit_fraction=strategy.exploit_fraction_override,
    )
    policy = build_mode(
        name=cast(ModeName, effective_mode_name),
        explorer=strategy.explorer,
        orchestrator=orchestrator,
        runtime=runtime,
        allowed_tools=allowed_tools,
    )

    # Wire KG tool context for multi-turn guided dispatch.
    _attach = getattr(orchestrator, "attach_guided_context", None)
    if _attach is not None and effective_mode_name == "guided":
        from .kg_tools import TOOL_SCHEMAS

        _attach(
            kg=knowledge_graph,
            pages=existing_pages,
            budget_target=budget_haiku_eq,
            tool_schemas=TOOL_SCHEMAS,
        )

    if runtime.exploit_fraction is not None:
        # Apply a user-supplied or LLM-supplied override by constructing
        # a one-shot StaticBudget split. The strategy's own schedule is
        # still kept for reallocate() behaviour downstream.
        split = StaticBudget(exploit_fraction=runtime.exploit_fraction).initial_split(
            budget_haiku_eq
        )
    else:
        split = strategy.budget.initial_split(budget_haiku_eq)
    last_allocation_epoch = runtime.allocation_epoch

    # Reserve 95% of the planned write budget before the extract loop so
    # the extract phase cannot consume headroom that the write phase needs.
    expected_write_reserve = split.write_haiku_eq * 0.95

    candidates: list[Candidate] = []
    chunks_read: list[str] = []
    extract_completed_normally = False
    split_initial = split
    policy_events: list[dict] = []
    # Pre-initialize write-pass dependencies so write_now can use them.
    dossier_store = DossierStore(bundle.root)
    author_ctx = build_author_context(docs)
    briefs: dict[str, EditorBrief] = {}
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
                remaining = max(0.0, budget_haiku_eq - meter.spent_haiku_eq)
                new_split = StaticBudget(
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
                budget_spent=meter.spent_haiku_eq,
                budget_remaining=max(0.0, budget_haiku_eq - meter.spent_haiku_eq),
            )
            # Push latest state to the orchestrator before each step
            # so KG tools (get_coverage, get_pages, get_budget) are fresh.
            _update = getattr(orchestrator, "update_guided_state", None)
            if _update is not None:
                _update(
                    snapshot=build_snapshot(
                        state,
                        budget_spent=meter.spent_haiku_eq,
                        budget_remaining=max(0.0, budget_haiku_eq - meter.spent_haiku_eq),
                    ),
                    pages=existing_pages,
                )
            decision = policy.next_extract(state, extract_batch_size, ctx)
            policy_events.extend(policy.drain_events())
            batch = list(decision.batch)
            if decision.stop:
                if decision.action == "write_now":
                    if not candidates:
                        # No candidates to write; resume extraction.
                        continue
                    # Mid-session write: flush current candidates then resume.
                    # Only write pages that are new or unwritten to avoid
                    # re-writing already-completed pages on each write_now.
                    mid_pages = canonicalize(candidates, existing=existing_pages)
                    already_written = {
                        p.id for p in existing_pages if p.body_markdown.strip()
                    }
                    new_pages = [p for p in mid_pages if p.id not in already_written]
                    for p in mid_pages:
                        for ev in p.evidence:
                            state.pages_concept_evidence_chunks.append(ev.chunk_id)
                            apply_coverage_feedback(state, ev.chunk_id, as_evidence=True)
                    existing_pages = mid_pages
                    _run_write_pass(
                        new_pages, max_concepts, writer, meter, strategy,
                        bundle, briefs, dossier_store, chunks_by_id,
                        images_index, write_req_cfg, author_ctx,
                        citation_index, knowledge_graph, budget_haiku_eq,
                        verbalize, write_rejections,
                        equations_index=equations_index,
                    )
                    candidates.clear()
                    policy_events.append({
                        "stage": "write_now",
                        "mode": effective_mode_name,
                        "n_pages": len(mid_pages),
                    })
                    continue
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
                    model_id=runtime.extract_tier.value,
                    tier=runtime.extract_tier,
                    images_for_doc=[_to_imageref(r) for r in images_index.for_doc(ck.doc_id)],
                    equations=_equations_for_chunk(ck, docs_by_id),
                    figure_captions=_figure_captions_for_chunk(
                        ck, docs_by_id, images_index
                    ),
                    verbalize=verbalize,
                    citation_refs=_resolve_citation_refs(
                        ck.text, ck.doc_id, knowledge_graph,
                    ),
                )
                for cid, ck in batch_chunks
            ]

            extract_many = getattr(extractor, "extract_many", None)
            if extract_many is not None:
                # Parallel dispatch: fire all requests, collect responses.
                # extract_many returns ONLY successful responses (errored
                # slots get .error.json files for diagnosis but are not
                # propagated). Match by chunk_id since the result list
                # may be shorter than the request list.
                try:
                    batch_resps = extract_many(batch_reqs)
                except (ValidationError, QuoteNotInChunkError):
                    batch_resps = []
                ck_by_id = {cid: ck for cid, ck in batch_chunks}
                for resp in batch_resps:
                    cid = resp.chunk_id
                    ck = ck_by_id.get(cid)
                    if ck is None:
                        continue
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
                    if verbalize:
                        _append_verbalize(
                            bundle, meter._run_id, "extract", cid, resp.reasoning  # noqa: SLF001
                        )
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
                    if verbalize:
                        _append_verbalize(
                            bundle, meter._run_id, "extract", cid, resp.reasoning  # noqa: SLF001
                        )
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
        new_split = strategy.budget.reallocate(remaining=remaining, novelty_rate=novelty_rate)
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
    # persist to disk so incremental runs (refine) accumulate material.
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
        budget_spent=meter.spent_haiku_eq,
        budget_remaining=max(0.0, budget_haiku_eq - meter.spent_haiku_eq),
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
        citation_index,
        knowledge_graph=knowledge_graph,
        equations_index=equations_index,
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
            effective_mode_name,
            policy_events,
        )
        # Patch dossier_summary into the already-written snapshot.
        snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))
        snap["dossier_summary"] = _dossier_summary(dossier_store, meter._run_id)  # noqa: SLF001
        bundle.run_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        save_coverage_memory(bundle, state, run_id=meter._run_id)  # noqa: SLF001
        _write_io_lineage(  # noqa: SLF001
            bundle, meter._run_id, chunks_read, chunks_by_id, candidates, dossier_store
        )
        return

    # ---- write loop (phase=all) -----------------------------------------
    # Rebuild write_req_cfg with the (possibly mutated) runtime write tier
    # so the LLM policy's set_tier actions take effect on writer calls.
    write_req_cfg = dataclasses.replace(
        write_req_cfg,
        model_id=runtime.write_tier.value,
        writer_tier=runtime.write_tier,
    )
    _run_write_pass(
        write_pages, max_concepts, writer, meter, strategy,
        bundle, briefs, dossier_store, chunks_by_id,
        images_index, write_req_cfg, author_ctx,
        citation_index, knowledge_graph, budget_haiku_eq,
        verbalize, write_rejections,
        equations_index=equations_index,
    )

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
    snapshot["iteration"] = iteration
    snapshot["mode"] = effective_mode_name
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
    snapshot["dossier_summary"] = _dossier_summary(dossier_store, meter._run_id)  # noqa: SLF001
    snapshot["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    bundle.run_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    append_run_history(bundle, snapshot)
    save_coverage_memory(bundle, state, run_id=meter._run_id)  # noqa: SLF001
    _write_io_lineage(bundle, meter._run_id, chunks_read, chunks_by_id, candidates, dossier_store)  # noqa: SLF001


def _write_prompt_layer_files(bundle: BundlePaths, layers: dict[str, str]) -> None:
    """Write each unique prompt layer to ``_meta/prompt_layers/<hash>.md``.

    Idempotent: skips existing files. Called once per run so the serve-dispatch
    runtime can fetch uncached layers by hash without re-receiving the full text
    on every write request.
    """
    out = bundle.prompt_layers_dir
    out.mkdir(parents=True, exist_ok=True)
    for h, text in layers.items():
        path = out / f"{h}.md"
        if not path.exists():
            path.write_text(text, encoding="utf-8")


# --- sampler state -------------------------------------------------------


def _build_explorer_state(
    rng: random.Random,
    chunks: list[Chunk],
    knowledge_graph: object,
) -> ExplorerState:
    """Build ExplorerState from the KnowledgeGraph.

    All corpus data comes from the KG. No CorpusGraph, no explorer_index,
    no flat-dict fallback.
    """
    chunks_by_doc: dict[str, list[str]] = defaultdict(list)
    abstract_by_doc: dict[str, str] = {}
    chunk_to_doc: dict[str, str] = {}
    all_chunk_ids: list[str] = []
    caption_ids: set[str] = set()

    for c in chunks:
        if c.section_type in SKIP_SECTION_TYPES:
            continue
        chunks_by_doc[c.doc_id].append(c.id)
        chunk_to_doc[c.id] = c.doc_id
        all_chunk_ids.append(c.id)
        sp = list(c.section_path or [])
        if sp and sp[0] == "__image__":
            caption_ids.add(c.id)
        if c.doc_id not in abstract_by_doc:
            abstract_by_doc[c.doc_id] = c.id

    # PageRank from KG source nodes (computed at ingest by graph_build)
    pagerank: dict[str, float] = {}
    for source in knowledge_graph.sources(kind="corpus").collect():
        pr = source.get("pagerank", 0.0)
        pagerank[source["id"]] = pr if pr else 1.0 / max(len(chunks_by_doc), 1)

    state = ExplorerState(
        rng=rng,
        kg=knowledge_graph,
        chunks_by_doc=dict(chunks_by_doc),
        abstract_chunk_by_doc=abstract_by_doc,
        pagerank_doc=pagerank,
        chunk_to_doc=chunk_to_doc,
        caption_chunk_ids=caption_ids,
    )
    init_coverage_state(state, all_chunk_ids)
    return state


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
            model_id=strategy.write_tier.value,
            strategy_name=strategy.name,
            iteration=iteration,
            drafted=bool(page.body_markdown.strip()),
        )
        write_page_file(bundle, page)
    build_index(bundle, pages).save()
    _rebuild_wiki_graph(bundle, pages)
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
                    page.equations = [eq.model_dump() for eq in staged.equations]
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
            page.equations = [eq.model_dump() for eq in resp.equations]
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
    mode_name: str,
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
    snapshot["iteration"] = iteration
    snapshot["mode"] = mode_name
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
    budget_spent: float = 0.0,
    budget_remaining: float = 0.0,
) -> ModeContext:
    page_ids = {p.id for p in pages}
    n_concepts = sum(1 for p in pages if p.kind == "article")
    n_people = sum(1 for p in pages if p.kind == "person")
    docs_covered = len({ev.doc_id for p in pages for ev in p.evidence})
    return ModeContext(
        run_id=run_id,
        n_pages=len(page_ids),
        n_candidates=len(candidates),
        n_concepts=n_concepts,
        n_people=n_people,
        docs_covered=docs_covered,
        docs_total=docs_total,
        budget_spent=budget_spent,
        budget_remaining=budget_remaining,
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


def _dossier_summary(dossier_store: DossierStore, run_id: str) -> dict:
    """Count substantive vs empty dossier entries; warn on stderr if ratio is high."""
    dossiers = dossier_store.load_all()
    n_total = sum(d.n_entries for d in dossiers)
    n_substantive = sum(
        1 for d in dossiers for e in d.entries if e.is_substantive
    )
    n_empty = n_total - n_substantive
    if n_total > 0 and n_empty / n_total > 0.2:
        sys.stderr.write(
            f"[{run_id}] WARNING: {n_empty}/{n_total} dossier entries are empty"
            f" ({100 * n_empty // n_total}% > 20% threshold)."
            f" Check sampler section filtering and extract prompt quality.\n"
        )
    return {
        "n_total": n_total,
        "n_substantive": n_substantive,
        "n_empty": n_empty,
        "n_dossiers": len(dossiers),
    }


def _write_io_lineage(
    bundle: BundlePaths,
    run_id: str,
    chunks_read: list[str],
    chunks_by_id: dict[str, Chunk],
    candidates: list,
    dossier_store: DossierStore,
) -> None:
    """Write per-run I/O lineage files to <bundle>/_meta/io_lineage/<run_id>/."""
    lineage_dir = bundle.meta_dir / "io_lineage" / run_id
    lineage_dir.mkdir(parents=True, exist_ok=True)

    # 1. chunks_read: metadata for every chunk the sampler sent to the extractor
    chunks_log = []
    for cid in chunks_read:
        ck = chunks_by_id.get(cid)
        chunks_log.append(
            {
                "chunk_id": cid,
                "section_type": ck.section_type if ck else "",
                "length": len(ck.text) if ck else 0,
            }
        )
    (lineage_dir / "chunks_read.json").write_text(
        json.dumps(chunks_log, indent=2), encoding="utf-8"
    )

    # 2. extract_candidates: every concept the extractor emitted
    cands_log = [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "title": c.concept.title,
            "kind": c.concept.kind,
            "has_definition": bool(c.concept.definition),
            "has_summary": bool(c.concept.summary),
            "definition_words": len(c.concept.definition.split()) if c.concept.definition else 0,
            "summary_words": len(c.concept.summary.split()) if c.concept.summary else 0,
        }
        for c in candidates
    ]
    (lineage_dir / "extract_candidates.json").write_text(
        json.dumps(cands_log, indent=2), encoding="utf-8"
    )

    # 3. dossier_entries: every entry with substantive flag
    dossier_log = []
    for d in dossier_store.load_all():
        for e in d.entries:
            dossier_log.append(
                {
                    "page_id": d.page_id,
                    "chunk_id": e.chunk_id,
                    "section_type": e.section_type,
                    "is_substantive": e.is_substantive,
                    "definition_words": len(e.definition.split()) if e.definition else 0,
                    "summary_words": len(e.summary.split()) if e.summary else 0,
                }
            )
    (lineage_dir / "dossier_entries.json").write_text(
        json.dumps(dossier_log, indent=2), encoding="utf-8"
    )
