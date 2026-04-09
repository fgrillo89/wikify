"""Thin Typer CLI. Wires bindings into strategies; contains no business logic."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import typer

from .distill.pipeline import run as pipeline_run
from .distill.strategies import STRATEGIES
from .infra.cache import ExtractCache
from .infra.cost_meter import CostMeter
from .ingest.refresh import ingest_corpus
from .paths import BundlePaths, CorpusPaths

app = typer.Typer(add_completion=False, help="wikify_simple CLI")


_BUDGET_TABLE = {"0.1x": 5_000.0, "1x": 50_000.0, "3x": 150_000.0}


@app.command()
def ingest(
    input_dir: Path = typer.Argument(...),
    output_dir: Path = typer.Option(Path("data/corpus"), "--out"),
) -> None:
    """Parse, chunk, embed and graph an input directory."""
    paths = ingest_corpus(input_dir, output_dir)
    typer.echo(f"corpus written to {paths.root}")


@app.command()
def distill(
    strategy: str = typer.Option(..., "--strategy", help="E | M | X"),
    binding: str = typer.Option("fake", "--binding", help="fake | claude_code"),
    budget: str = typer.Option("1x", "--budget"),
    seed: int = typer.Option(0, "--seed"),
    corpus_dir: Path = typer.Option(Path("data/corpus"), "--corpus"),
    out_dir: Path = typer.Option(Path("data/wikis"), "--out"),
    cache_dir: Path = typer.Option(Path("data/cache/extract"), "--cache"),
    feed: bool = typer.Option(
        False,
        "--feed",
        help="Incremental mode: reuse --out as an existing bundle and merge",
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
        "wiki_concept",
        "--artifact",
        help="Artifact template to layer into the writer prompt",
    ),
) -> None:
    """Run a distillation strategy on an ingested corpus."""
    from .prompts import available_artifact_templates, available_field_guides

    if strategy not in STRATEGIES:
        raise typer.BadParameter(f"unknown strategy: {strategy}")
    if field is None:
        from .distill.field_detect import detect_field

        field = detect_field(CorpusPaths(root=corpus_dir))
        typer.echo(f"auto-detected field: {field}")
    if field not in available_field_guides():
        raise typer.BadParameter(f"unknown field {field!r}; available: {available_field_guides()}")
    if artifact not in available_artifact_templates():
        raise typer.BadParameter(
            f"unknown artifact {artifact!r}; available: {available_artifact_templates()}"
        )
    if binding == "claude_code" and os.environ.get("WIKIFY_SIMPLE_ALLOW_NETWORK") != "1":
        raise typer.BadParameter("live binding requires WIKIFY_SIMPLE_ALLOW_NETWORK=1")
    if budget in _BUDGET_TABLE:
        budget_haiku_eq = _BUDGET_TABLE[budget]
    else:
        budget_haiku_eq = float(budget)

    if feed:
        # Reuse the supplied out_dir *as* the bundle dir. No timestamp suffix.
        run_id = f"{strategy}_{budget}_seed{seed}_feed"
        bundle = BundlePaths(root=out_dir)
    else:
        run_id = (
            f"{strategy}_{budget}_seed{seed}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        )
        bundle = BundlePaths(root=out_dir / run_id)
    bundle.ensure()

    cache = ExtractCache(root=cache_dir)
    meter = CostMeter(
        budget_haiku_eq=budget_haiku_eq,
        run_id=run_id,
        events_path=bundle.calls_path,
    )

    extractor, writer, editor, compactor = _wire_binding(binding, cache, meter)

    cfg = STRATEGIES[strategy](seed=seed)
    cfg.field_name = field
    cfg.artifact_name = artifact
    pipeline_run(
        corpus=CorpusPaths(root=corpus_dir),
        bundle=bundle,
        strategy=cfg,
        extractor=extractor,
        writer=writer,
        meter=meter,
        budget_haiku_eq=budget_haiku_eq,
        feed=feed,
        editor=editor,
        compactor=compactor,
    )
    snap_path = bundle.run_path
    if snap_path.exists():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        typer.echo(
            f"bundle written to {bundle.root} "
            f"(n_cached_skipped={snap.get('n_cached_skipped', 0)}, "
            f"n_new_extracted={snap.get('n_new_extracted', 0)}, feed={feed})"
        )
    else:
        typer.echo(f"bundle written to {bundle.root}")


def _wire_binding(name: str, cache: ExtractCache, meter: CostMeter):
    if name == "fake":
        from .bindings.fake import FakeCompactor, FakeEditor, FakeExtractor, FakeWriter

        return FakeExtractor(cache, meter), FakeWriter(meter), FakeEditor(), FakeCompactor()
    if name == "claude_code":
        from .bindings.claude_code import ClaudeCodeExtractor, ClaudeCodeWriter

        # Editor + compactor use the same dispatcher pattern as extractor/writer.
        # For now, fall back to fake editor/compactor until claude_code bindings
        # are implemented for these roles.
        from .bindings.fake import FakeCompactor, FakeEditor

        return (
            ClaudeCodeExtractor(cache, meter),
            ClaudeCodeWriter(meter),
            FakeEditor(),
            FakeCompactor(),
        )
    raise typer.BadParameter(f"unknown binding: {name}")


@app.command("persona-generate")
def persona_generate(
    corpus_dir: Path = typer.Option(Path("data/corpus"), "--corpus"),
    field: str = typer.Option("generic", "--field"),
    binding: str = typer.Option("fake", "--binding", help="fake | claude_code"),
) -> None:
    """Generate and persist the corpus persona at <corpus>/persona.txt."""
    from .distill.persona import generate_corpus_persona
    from .store.corpus import list_documents

    corpus = CorpusPaths(root=corpus_dir)
    docs = list_documents(corpus)
    complete = None
    if binding == "claude_code":
        if os.environ.get("WIKIFY_SIMPLE_ALLOW_NETWORK") != "1":
            raise typer.BadParameter("live binding requires WIKIFY_SIMPLE_ALLOW_NETWORK=1")
        from .bindings.claude_code import make_persona_complete

        complete = make_persona_complete()
    elif binding != "fake":
        raise typer.BadParameter(f"unknown binding: {binding}")
    text = generate_corpus_persona(
        corpus=corpus,
        sample_docs=docs,
        complete=complete,
        field=field,
    )
    typer.echo(f"persona written to {corpus.persona_path} ({len(text)} chars)")


@app.command()
def query(
    question: str = typer.Argument(...),
    bundle_dir: Path = typer.Option(..., "--bundle"),
    binding: str = typer.Option("fake", "--binding", help="fake | claude_code"),
    model: str = typer.Option("haiku", "--model"),
    corpus_dir: Path = typer.Option(Path("data/corpus"), "--corpus"),
    out_root: Path = typer.Option(Path("data/queries"), "--out"),
) -> None:
    """Ask a question against a wiki bundle; write the answer to data/queries/."""
    from .distill.query import run as query_run
    from .infra.embedding import embed_texts

    bundle = BundlePaths(root=bundle_dir)
    corpus = CorpusPaths(root=corpus_dir)
    if binding == "fake":
        from .bindings.fake import FakeQuerier

        querier = FakeQuerier()
    elif binding == "claude_code":
        if os.environ.get("WIKIFY_SIMPLE_ALLOW_NETWORK") != "1":
            raise typer.BadParameter("live binding requires WIKIFY_SIMPLE_ALLOW_NETWORK=1")
        from .bindings.claude_code import ClaudeCodeQuerier

        meter = CostMeter(
            budget_haiku_eq=1e9,
            run_id="query",
            events_path=Path("data/queries/_calls.jsonl"),
        )
        querier = ClaudeCodeQuerier(meter)
    else:
        raise typer.BadParameter(f"unknown binding: {binding}")

    answer = query_run(
        bundle=bundle,
        corpus=corpus,
        question=question,
        querier=querier,
        embed=embed_texts,
        model_id=model,
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


@app.command("field-detect")
def field_detect_cmd(
    corpus_dir: Path = typer.Option(Path("data/corpus"), "--corpus"),
) -> None:
    """Auto-detect the most likely field for a corpus and print the top scores."""
    from .distill.field_detect import detect_field, detect_field_scores

    corpus = CorpusPaths(root=corpus_dir)
    chosen = detect_field(corpus)
    scores = detect_field_scores(corpus)
    typer.echo(f"field: {chosen}")
    typer.echo("top scores:")
    for name, score in scores[:5]:
        typer.echo(f"  {name}: {score}")


@app.command("html")
def html(
    bundle_dir: Path = typer.Option(..., "--bundle"),
    out_dir: Path | None = typer.Option(None, "--out"),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
) -> None:
    """Render a wiki bundle to a static HTML site (legacy renderer port)."""
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
    from .eval import metrics
    from .eval.bundle import load_bundle
    from .infra.embedding import embedder_for
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

    report_path = report or (bundle_dir / "_metrics.md")
    json_path = report_path.with_suffix(".json")

    payload = {
        "bundle": str(bundle_dir),
        "corpus": str(corpus_dir),
        "embedder": {"backend": meta.backend, "dim": meta.dim, "model": meta.model},
        "M1_coverage_residual": m1,
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
    import os
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
