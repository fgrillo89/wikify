"""Thin Typer CLI. Wires bindings into strategies; contains no business logic."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import typer

from .cache import ExtractCache
from .distill.pipeline import run as pipeline_run
from .distill.pipeline import run_with_preloaded
from .distill.preload import preload_corpus
from .distill.strategy import STRATEGY_CONFIGS, build_strategy
from .ingest.pipeline import ingest_corpus
from .meter import CostMeter
from .paths import BundlePaths, CorpusPaths
from .types import ModelTier

app = typer.Typer(add_completion=False, help="wikify CLI")


_BUDGET_TABLE = {"0.1x": 5_000.0, "1x": 50_000.0, "3x": 150_000.0}
_VALID_TIERS = tuple(tier.value for tier in ModelTier)


def _parse_budget(raw: str) -> float:
    """Parse a budget string into haiku-equivalent tokens.

    Accepts:
      - Legacy shortcuts: ``0.1x`` (5k), ``1x`` (50k), ``3x`` (150k)
      - Suffixed integers: ``50k``, ``1.5M`` (case-insensitive)
      - Raw numbers: ``50000``, ``5e4``
    """
    if raw in _BUDGET_TABLE:
        return _BUDGET_TABLE[raw]
    s = raw.strip().lower()
    multiplier = 1.0
    if s.endswith("k"):
        multiplier = 1_000.0
        s = s[:-1]
    elif s.endswith("m"):
        multiplier = 1_000_000.0
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError as exc:
        raise typer.BadParameter(
            f"--budget must be a number, Nk, NM, or one of {sorted(_BUDGET_TABLE)}: got {raw!r}"
        ) from exc


@app.command()
def ingest(
    input_dir: Path = typer.Argument(...),
    output_dir: Path = typer.Option(Path("data/corpora"), "--out"),
    workers: int = typer.Option(
        0,
        "--workers",
        help="Parse parallelism. 0 = 60%% of CPU cores (default), 1 = serial.",
    ),
    mode: str = typer.Option(
        "additive",
        "--mode",
        help="additive (default) or sync (removes absent sources).",
    ),
    parser: str = typer.Option(
        "default",
        "--parser",
        help="Parser backend: 'default' or a name registered via register_parser_backend().",
    ),
) -> None:
    """Parse, chunk, embed and graph an input directory."""
    paths = ingest_corpus(
        input_dir,
        output_dir,
        max_workers=None if workers == 0 else workers,
        mode=mode,
        parser_backend=parser,
    )
    typer.echo(f"corpus written to {paths.root}")


@app.command()
def distill(
    strategy: str = typer.Option(..., "--strategy", help="E | M | X"),
    mode: str = typer.Option(
        "scripted",
        "--mode",
        help="scripted | guided",
    ),
    budget: str = typer.Option(
        "1x",
        "--budget",
        help=(
            "Haiku-equivalent tokens. Accepts integers (50000), suffixed "
            "(50k, 1.5M), or shortcuts (0.1x=5k, 1x=50k, 3x=150k)."
        ),
    ),
    extract_tier: str | None = typer.Option(
        None,
        "--extract-tier",
        help="S | M | L. Override the strategy default (typically S=small model).",
    ),
    write_tier: str | None = typer.Option(
        None,
        "--write-tier",
        help="S | M | L. Override the strategy default (typically M=medium model).",
    ),
    edit_tier: str | None = typer.Option(
        None,
        "--edit-tier",
        help="S | M | L. Override the strategy default (typically M=medium model).",
    ),
    compact_tier: str | None = typer.Option(
        None,
        "--compact-tier",
        help="S | M | L. Override the strategy default (typically S=small model).",
    ),
    exploit_fraction: float | None = typer.Option(
        None,
        "--exploit-fraction",
        help=(
            "Fraction of budget (0..1) allocated to the write phase. "
            "Overrides the strategy default."
        ),
    ),
    seed: int = typer.Option(0, "--seed"),
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
    out_dir: Path = typer.Option(Path("data/wikis"), "--out"),
    bundle_dir: Path | None = typer.Option(
        None,
        "--bundle",
        help=(
            "Explicit bundle path. When set, overrides --out and skips the "
            "timestamped subdir on --iteration create. Use this to run "
            "create then refine against the same path across iterations."
        ),
    ),
    merge_from: Path | None = typer.Option(
        None,
        "--merge-from",
        help="Second bundle to merge when --iteration merge",
    ),
    cache_dir: Path = typer.Option(Path("data/cache/extract"), "--cache"),
    feed: bool = typer.Option(
        False,
        "--feed",
        help="Deprecated alias for --iteration refine",
    ),
    iteration: str = typer.Option(
        "create",
        "--iteration",
        help="create | refine | merge",
    ),
    field: str | None = typer.Option(
        None,
        "--field",
        help=(
            "Field guide to layer into the writer prompt. If omitted, "
            "auto-detected from the corpus topics."
        ),
    ),
    artifact: str = typer.Option(
        "wiki_article",
        "--artifact",
        help="Artifact template to layer into the writer prompt",
    ),
    phase: str = typer.Option(
        "all",
        "--phase",
        help="extract (stop after saving write requests) | write (resume) | all",
    ),
    verbalize: bool = typer.Option(
        False,
        "--verbalize/--no-verbalize",
        help=(
            "Ask handlers to include a short reasoning line on every response."
            " The pipeline appends these to <bundle>/_meta/verbalize.jsonl"
            " for post-hoc review. Adds a small token overhead per call."
        ),
    ),
) -> None:
    """Run a distillation strategy on an ingested corpus."""
    from .prompts import available_artifact_templates, available_field_guides

    if phase not in ("all", "extract", "write"):
        raise typer.BadParameter(f"unknown phase: {phase}; must be all, extract, or write")
    if mode not in ("scripted", "guided"):
        raise typer.BadParameter(f"unknown mode: {mode}")
    if iteration not in ("create", "refine", "merge"):
        raise typer.BadParameter(f"unknown iteration: {iteration}")
    if feed and iteration == "create":
        iteration = "refine"
    if iteration == "merge" and merge_from is None:
        raise typer.BadParameter("--iteration merge requires --merge-from")
    if iteration == "merge" and phase != "all":
        raise typer.BadParameter("--iteration merge only supports --phase all")
    if strategy not in STRATEGY_CONFIGS:
        raise typer.BadParameter(f"unknown strategy: {strategy}")
    if field is None:
        from .distill.extract.field_detect import detect_field

        field = detect_field(CorpusPaths(root=corpus_dir))
        typer.echo(f"auto-detected field: {field}")
    if field not in available_field_guides():
        raise typer.BadParameter(f"unknown field {field!r}; available: {available_field_guides()}")
    if artifact not in available_artifact_templates():
        raise typer.BadParameter(
            f"unknown artifact {artifact!r}; available: {available_artifact_templates()}"
        )
    budget_haiku_eq = _parse_budget(budget)
    for tier_name, tier_val in (
        ("extract-tier", extract_tier),
        ("write-tier", write_tier),
        ("edit-tier", edit_tier),
        ("compact-tier", compact_tier),
    ):
        if tier_val is not None and tier_val not in _VALID_TIERS:
            raise typer.BadParameter(
                f"--{tier_name} must be one of {_VALID_TIERS}; got {tier_val!r}"
            )
    if exploit_fraction is not None and not 0.0 <= exploit_fraction <= 1.0:
        raise typer.BadParameter(f"--exploit-fraction must be in [0, 1]; got {exploit_fraction!r}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"{strategy}_{budget}_seed{seed}_{iteration}_{phase}_{ts}"
    # Bundle path resolution:
    #   1. If --bundle is set, use it verbatim (workflow mode).
    #   2. Else refine / merge / phase=write: reuse out_dir as an existing bundle
    #   3. Else create: if out_dir already looks like a bundle, reuse it;
    #      otherwise create a timestamped subdir.
    if bundle_dir is not None:
        bundle = BundlePaths(root=bundle_dir)
    elif iteration in ("refine", "merge") or phase == "write":
        bundle = BundlePaths(root=out_dir)
    else:
        out_looks_like_bundle = out_dir.exists() and (
            (out_dir / "_index.json").exists()
            or (out_dir / "concepts").exists()
            or (out_dir / "_run.json").exists()
        )
        bundle = BundlePaths(root=out_dir if out_looks_like_bundle else out_dir / run_id)
    bundle.ensure()

    cache = ExtractCache(root=cache_dir)
    meter = CostMeter(
        budget_haiku_eq=budget_haiku_eq,
        run_id=run_id,
        events_path=bundle.calls_path,
    )

    from .dispatch import Dispatch

    dispatch = Dispatch(meter, cache)

    cfg = build_strategy(strategy, seed=seed)
    # Apply per-role tier overrides if the user supplied them.
    if extract_tier is not None:
        cfg.extract_tier = ModelTier(extract_tier)
    if write_tier is not None:
        cfg.write_tier = ModelTier(write_tier)
    if edit_tier is not None:
        cfg.edit_tier = ModelTier(edit_tier)
    if compact_tier is not None:
        cfg.compact_tier = ModelTier(compact_tier)
    # Apply allocation override (goes through PolicyRuntime in pipeline.run).
    if exploit_fraction is not None:
        cfg.exploit_fraction_override = exploit_fraction
    pipeline_run(
        corpus=CorpusPaths(root=corpus_dir),
        bundle=bundle,
        strategy=cfg,
        extractor=dispatch,
        writer=dispatch,
        meter=meter,
        budget_haiku_eq=budget_haiku_eq,
        feed=feed,
        iteration=iteration,
        merge_from_bundle=(BundlePaths(root=merge_from) if merge_from is not None else None),
        editor=dispatch,
        compactor=dispatch,
        orchestrator=dispatch,
        mode_name=mode,
        field_name=field,
        artifact_name=artifact,
        phase=phase,
        verbalize=verbalize,
    )
    snap_path = bundle.run_path
    if snap_path.exists():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        typer.echo(
            f"bundle written to {bundle.root} "
            f"(n_cached_skipped={snap.get('n_cached_skipped', 0)}, "
            f"n_new_extracted={snap.get('n_new_extracted', 0)}, "
            f"iteration={snap.get('iteration', iteration)}, mode={snap.get('policy', mode)})"
        )
    else:
        typer.echo(f"bundle written to {bundle.root}")


@app.command()
def campaign(
    strategy: str = typer.Option(..., "--strategy", help="E | M | X"),
    mode: str = typer.Option("scripted", "--mode", help="scripted | guided"),
    budget: str = typer.Option("1x", "--budget", help="Haiku-equivalent tokens per iteration."),
    iterations: int = typer.Option(1, "--iterations", help="Number of iterations to run."),
    extract_tier: str | None = typer.Option(None, "--extract-tier", help="S | M | L"),
    write_tier: str | None = typer.Option(None, "--write-tier", help="S | M | L"),
    edit_tier: str | None = typer.Option(None, "--edit-tier", help="S | M | L"),
    compact_tier: str | None = typer.Option(None, "--compact-tier", help="S | M | L"),
    exploit_fraction: float | None = typer.Option(None, "--exploit-fraction"),
    seed: int = typer.Option(0, "--seed"),
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
    bundle_dir: Path = typer.Option(..., "--bundle", help="Bundle path (required for campaign)."),
    cache_dir: Path = typer.Option(Path("data/cache/extract"), "--cache"),
    field: str | None = typer.Option(None, "--field"),
    artifact: str = typer.Option("wiki_article", "--artifact"),
    verbalize: bool = typer.Option(
        False,
        "--verbalize/--no-verbalize",
        help="Per-iteration handler reasoning log at <bundle>/_meta/verbalize.jsonl.",
    ),
) -> None:
    """Run N iterations of distillation in one process, loading the corpus once."""
    from .prompts import available_artifact_templates, available_field_guides

    if mode not in ("scripted", "guided"):
        raise typer.BadParameter(f"unknown mode: {mode}")
    if strategy not in STRATEGY_CONFIGS:
        raise typer.BadParameter(f"unknown strategy: {strategy}")
    if iterations < 1:
        raise typer.BadParameter("--iterations must be >= 1")
    for tier_name, tier_val in (
        ("extract-tier", extract_tier),
        ("write-tier", write_tier),
        ("edit-tier", edit_tier),
        ("compact-tier", compact_tier),
    ):
        if tier_val is not None and tier_val not in _VALID_TIERS:
            raise typer.BadParameter(
                f"--{tier_name} must be one of {_VALID_TIERS}; got {tier_val!r}"
            )
    if exploit_fraction is not None and not 0.0 <= exploit_fraction <= 1.0:
        raise typer.BadParameter(f"--exploit-fraction must be in [0, 1]; got {exploit_fraction!r}")

    if field is None:
        from .distill.extract.field_detect import detect_field

        field = detect_field(CorpusPaths(root=corpus_dir))
        typer.echo(f"auto-detected field: {field}")
    if field not in available_field_guides():
        raise typer.BadParameter(f"unknown field {field!r}; available: {available_field_guides()}")
    if artifact not in available_artifact_templates():
        raise typer.BadParameter(
            f"unknown artifact {artifact!r}; available: {available_artifact_templates()}"
        )

    budget_haiku_eq = _parse_budget(budget)
    corpus = CorpusPaths(root=corpus_dir)
    bundle = BundlePaths(root=bundle_dir)
    bundle.ensure()

    # Load the corpus ONCE for all iterations.
    preloaded = preload_corpus(corpus)

    # Single ExtractCache instance — survives across iterations so in-process
    # cache lookups after iteration 1 are free.
    cache = ExtractCache(root=cache_dir)

    for i in range(1, iterations + 1):
        iter_seed = seed + i - 1
        iteration_op = "create" if i == 1 else "refine"
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_id = f"campaign_{strategy}_{budget}_seed{iter_seed}_iter{i}_{ts}"

        meter = CostMeter(
            budget_haiku_eq=budget_haiku_eq,
            run_id=run_id,
            events_path=bundle.calls_path,
        )

        from .dispatch import Dispatch

        dispatch = Dispatch(meter, cache)

        cfg = build_strategy(strategy, seed=iter_seed)
        if extract_tier is not None:
            cfg.extract_tier = ModelTier(extract_tier)
        if write_tier is not None:
            cfg.write_tier = ModelTier(write_tier)
        if edit_tier is not None:
            cfg.edit_tier = ModelTier(edit_tier)
        if compact_tier is not None:
            cfg.compact_tier = ModelTier(compact_tier)
        if exploit_fraction is not None:
            cfg.exploit_fraction_override = exploit_fraction

        run_with_preloaded(
            preloaded=preloaded,
            bundle=bundle,
            strategy=cfg,
            extractor=dispatch,
            writer=dispatch,
            meter=meter,
            budget_haiku_eq=budget_haiku_eq,
            iteration=iteration_op,
            editor=dispatch,
            compactor=dispatch,
            orchestrator=dispatch,
            mode_name=mode,
            field_name=field,
            artifact_name=artifact,
            verbalize=verbalize,
        )

        typer.echo(f"iteration {i}/{iterations} done (run_id={run_id})")

    typer.echo(f"campaign complete: {bundle.root}")


@app.command("persona-generate")
def persona_generate(
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
    field: str = typer.Option("generic", "--field"),
) -> None:
    """Generate and persist the corpus persona at <corpus>/persona.txt."""
    from .dispatch import make_persona_complete
    from .distill.write.persona import generate_corpus_persona
    from .store.corpus import list_documents


    corpus = CorpusPaths(root=corpus_dir)
    docs = list_documents(corpus)
    complete = make_persona_complete()
    text = generate_corpus_persona(
        corpus=corpus,
        sample_docs=docs,
        complete=complete,
        field=field,
    )
    typer.echo(f"persona written to {corpus.persona_path} ({len(text)} chars)")


@app.command("field-detect")
def field_detect_cmd(
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
) -> None:
    """Auto-detect the most likely field for a corpus and print the top scores."""
    from .distill.extract.field_detect import detect_field, detect_field_scores

    corpus = CorpusPaths(root=corpus_dir)
    chosen = detect_field(corpus)
    scores = detect_field_scores(corpus)
    typer.echo(f"field: {chosen}")
    typer.echo("top scores:")
    for name, score in scores[:5]:
        typer.echo(f"  {name}: {score}")


@app.command()
def maintenance(
    bundle_dir: Path = typer.Option(..., "--bundle"),
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan and report without deleting logs."),
) -> None:
    """Scan the query log and emit wiki improvement actions."""
    from .distill.maintenance import run_maintenance

    bundle = BundlePaths(root=bundle_dir)
    corpus = CorpusPaths(root=corpus_dir)
    report = run_maintenance(bundle, corpus, dry_run=dry_run)
    typer.echo(
        f"maintenance: scanned={report.queries_scanned} "
        f"dispatched={report.actions_dispatched} "
        f"applied={report.actions_applied} "
        f"deleted={report.query_logs_deleted}"
    )
    for action in report.actions:
        typer.echo(
            f"  [{action.action}] {action.target_page!r}: {action.brief}"
        )


@app.command()
def query(
    question: str = typer.Argument(...),
    bundle_dir: Path = typer.Option(..., "--bundle"),
    model: str = typer.Option(ModelTier.MEDIUM.value, "--model"),
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
    out_root: Path = typer.Option(Path("data/queries"), "--out"),
    save_log: bool = typer.Option(True, "--save-log/--no-save-log"),
) -> None:
    """Ask a question against a wiki bundle; write the answer to data/queries/."""
    from .dispatch import Dispatch
    from .distill.query import run as query_run
    from .embedding import embed_texts


    bundle = BundlePaths(root=bundle_dir)
    corpus = CorpusPaths(root=corpus_dir)
    meter = CostMeter(
        budget_haiku_eq=1e9,
        run_id="query",
        events_path=Path("data/queries/_calls.jsonl"),
    )
    querier = Dispatch(meter, ExtractCache(root=Path("data/cache/extract")))

    answer = query_run(
        bundle=bundle,
        corpus=corpus,
        question=question,
        querier=querier,
        embed=embed_texts,
        model_id=model,
        tier=ModelTier.MEDIUM,
        save_log=save_log,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = out_root / bundle_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}.md"
    fm_lines = [
        "---",
        f"question: {json.dumps(question)}",
        f"bundle: {bundle_dir.name}",
        f"citations: {json.dumps(list(answer.citations))}",
        f"chunks: {json.dumps(list(answer.chunks))}",
        f"follow_ups: {json.dumps(list(answer.follow_ups))}",
        "---",
        "",
        answer.text,
        "",
    ]
    out_path.write_text("\n".join(fm_lines), encoding="utf-8")
    typer.echo(f"answer written to {out_path}")


@app.command("html")
def html(
    bundle_dir: Path = typer.Option(..., "--bundle"),
    out_dir: Path | None = typer.Option(None, "--out"),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
) -> None:
    """Render a wiki bundle to a static HTML site."""
    from .render.html import build_site

    bundle = BundlePaths(root=bundle_dir)
    target = out_dir if out_dir is not None else (bundle_dir / "_html")
    corpus_root = Path(corpus_dir) if corpus_dir is not None else None
    result = build_site(bundle, target, corpus_root=corpus_root)
    typer.echo(f"site written to {result}")


@app.command("eval")
def eval_bundle(
    bundle_dir: Path = typer.Option(..., "--bundle"),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    report: Path | None = typer.Option(None, "--report"),
) -> None:
    """Compute M1/M3/M5/M6 metrics for a bundle and write a report."""
    from .embedding import embedder_for
    from .eval import metrics
    from .eval.bundle import load_bundle
    from .store.corpus import all_chunks
    from .store.vectors import load_vectors
    from .store.vectors_meta import read_meta

    corpus = CorpusPaths(root=corpus_dir)
    bundle = load_bundle(bundle_dir)

    vs = load_vectors(corpus.vectors_path)
    meta = read_meta(corpus.vectors_path)
    if meta is None:
        raise typer.BadParameter(
            f"no vectors.meta.json next to {corpus.vectors_path}; reingest the corpus"
        )
    embed = embedder_for(meta.backend, meta.model)

    chunks = all_chunks(corpus)
    chunks_by_id = {c.id: c for c in chunks}

    m1 = metrics.coverage_residual(bundle, vs.matrix, embed)
    m3_evidence = metrics.spectral_gap_modularity(bundle)
    m3_links = metrics.g_links_modularity(bundle)
    m5 = metrics.hit_rate(bundle)
    g = metrics.grounding(
        bundle, lambda cid: chunks_by_id[cid].text if cid in chunks_by_id else None
    )

    # M1_image: image coverage and figure reference rate.
    import numpy as _np

    from .ingest.explorer_index import load_explorer_index

    sampler_idx = load_explorer_index(corpus.explorer_index_path)
    caption_ids: list[str] = sampler_idx["caption_chunk_ids"] if sampler_idx else []
    _empty_cap = _np.empty((0, vs.matrix.shape[1]), dtype="float32")
    if caption_ids:
        caption_chunks = [chunks_by_id[cid] for cid in caption_ids if cid in chunks_by_id]
        caption_texts = [c.text for c in caption_chunks]
        caption_embeds = embed(caption_texts) if caption_texts else _empty_cap
    else:
        caption_embeds = _empty_cap
    m1_image = metrics.image_coverage_residual(bundle, caption_embeds, embed)
    fig_counts = metrics.figure_reference_counts(bundle)

    report_path = report or (bundle_dir / "_metrics.md")
    json_path = report_path.with_suffix(".json")

    payload = {
        "bundle": str(bundle_dir),
        "corpus": str(corpus_dir),
        "embedder": {"backend": meta.backend, "dim": meta.dim, "model": meta.model},
        "M1_coverage_residual": m1,
        "M1_image_coverage_residual": m1_image,
        "M1_image_figure_counts": fig_counts,
        "M3_g_evidence": m3_evidence,
        "M3_g_links": m3_links,
        "M5_hit_rate": m5,
        "M6_grounding": {
            "g1_anchoring": g.g1_anchoring,
            "g2_evidence_ok": g.g2_evidence_ok,
            "n_sentences": g.n_sentences,
            "n_markers": g.n_markers,
            "passes": g.passes,
        },
    }

    md_lines = [
        f"# Metrics — {bundle_dir.name}",
        "",
        f"corpus: `{corpus_dir}`  ",
        f"embedder: `{meta.backend}` (dim={meta.dim}, model={meta.model})",
        "",
        "## M1 — coverage residual",
        f"value: **{m1:.4g}** (lower is better)",
        "",
        "## M3 — g_evidence (modularity / spectral gap)",
        f"- modularity: {m3_evidence['modularity']:.4g}",
        f"- spectral_gap: {m3_evidence['spectral_gap']:.4g}",
        f"- n_nodes: {int(m3_evidence['n_nodes'])}",
        f"- n_edges: {int(m3_evidence['n_edges'])}",
        "",
        "## M3 — g_links (link-graph modularity)",
        f"- modularity: {m3_links['modularity']:.4g}",
        f"- spectral_gap: {m3_links['spectral_gap']:.4g}",
        f"- n_nodes: {int(m3_links['n_nodes'])}",
        f"- n_edges: {int(m3_links['n_edges'])}",
        "",
        "## M5 — hit rate",
        f"value: **{m5}**",
        "",
        "## M6 — grounding",
        f"- g1_anchoring: {g.g1_anchoring:.4g}",
        f"- g2_evidence_ok: {g.g2_evidence_ok:.4g}",
        f"- n_sentences: {g.n_sentences}",
        f"- n_markers: {g.n_markers}",
        f"- passes: {g.passes}",
        "",
    ]
    _atomic_write_text(report_path, "\n".join(md_lines))
    _atomic_write_text(json_path, json.dumps(_jsonable(payload), indent=2))

    from .eval.audit import write_audit

    write_audit(bundle, payload, out_path=bundle_dir / "_audit.md")
    typer.echo(
        f"M1={m1:.3f} M3_evid_Q={m3_evidence['modularity']:.3f} "
        f"M5={m5} G1={g.g1_anchoring:.3f} -> {report_path}"
    )


def _jsonable(obj):
    """Recursively replace NaN/Inf floats with None so JSON stays strict."""
    import math

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _atomic_write_text(path: Path, content: str) -> None:
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".eval-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


if __name__ == "__main__":
    app()
