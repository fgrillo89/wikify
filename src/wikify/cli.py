"""Thin Typer CLI. Wires the deterministic + skill-driven sub-apps and
keeps the corpus/eval/render commands. Model-calling subcommands
(`distill`, `campaign`, `study`, `persona-generate`, `maintenance`,
`query`) and their dispatch backing have been retired — the
skill-driven path under `wikify session/kg/extract/draft/validate/
bundle/meter` replaces them.
"""

import json
import os
from pathlib import Path

import typer

from .cli_cmds import bundle as bundle_cli
from .cli_cmds import draft as draft_cli
from .cli_cmds import extract as extract_cli
from .cli_cmds import kg as kg_cli
from .cli_cmds import meter as meter_cli
from .cli_cmds import session as session_cli
from .cli_cmds import validate as validate_cli
from .ingest.pipeline import ingest_corpus, refresh_corpus
from .paths import BundlePaths, CorpusPaths

app = typer.Typer(add_completion=False, help="wikify CLI")
app.add_typer(session_cli.app, name="session")
app.add_typer(kg_cli.app, name="kg")
app.add_typer(extract_cli.app, name="extract")
app.add_typer(draft_cli.app, name="draft")
app.add_typer(validate_cli.app, name="validate")
app.add_typer(bundle_cli.app, name="bundle")
app.add_typer(meter_cli.app, name="meter")


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
        help=(
            "Parser backend. 'default' uses Marker for PDF and Docling for "
            ".docx/.pptx/.html — best quality, GPU-bound. 'lite' uses the "
            "lightweight built-ins (pymupdf4llm / python-docx / python-pptx / "
            "trafilatura); pick this for CI, small ingests, or any "
            "no-GPU environment. 'marker' and 'docling' are single-backend "
            "overrides. The header line printed at ingest start shows the "
            "accepted extensions for the selected backend."
        ),
    ),
    no_refresh: bool = typer.Option(
        False,
        "--no-refresh",
        help="Skip derived-artifact rebuild (embeddings, graph, topics, etc.).",
    ),
    openalex: bool = typer.Option(
        False,
        "--openalex",
        help="Enable OpenAlex bulk resolution + depth-1 reference expansion.",
    ),
    cite_resolution: str = typer.Option(
        "crossref",
        "--cite-resolution",
        help=(
            "Citation DOI resolution tier. "
            "'off' = heuristic parse only; "
            "'crossref' = CrossRef batch (default, fast); "
            "'full' = CrossRef + doi.org fallback (slow on cold caches)."
        ),
    ),
    no_format_dedup: bool = typer.Option(
        False,
        "--no-format-dedup",
        help=(
            "Disable same-stem format dedup. By default, when a source "
            "directory contains `paper.pdf` and `paper.docx` with matching "
            "stems the pipeline parses only the higher-ranked format "
            "(pdf > docx > pptx > html > ...). Pass this flag to keep all "
            "copies — useful when same-stem files genuinely are different "
            "documents."
        ),
    ),
) -> None:
    """Parse, chunk, embed and graph an input directory."""
    if cite_resolution not in {"off", "crossref", "full"}:
        raise typer.BadParameter(
            f"--cite-resolution must be off|crossref|full, got {cite_resolution!r}"
        )
    paths = ingest_corpus(
        input_dir,
        output_dir,
        max_workers=None if workers == 0 else workers,
        mode=mode,
        parser_backend=parser,
        refresh=not no_refresh,
        resolve_bibliography_doi=openalex,
        cite_resolution=cite_resolution,
        dedup_same_stem=not no_format_dedup,
    )
    typer.echo(f"corpus written to {paths.root}")


@app.command()
def refresh(
    corpus_dir: Path = typer.Argument(..., help="Path to the corpus directory."),
    openalex: bool = typer.Option(
        False,
        "--openalex",
        help="Enable OpenAlex bulk resolution + depth-1 reference expansion.",
    ),
    cite_resolution: str = typer.Option(
        "crossref",
        "--cite-resolution",
        help=(
            "Citation DOI resolution tier. "
            "'off' = heuristic parse only; "
            "'crossref' = CrossRef batch (default, fast); "
            "'full' = CrossRef + doi.org fallback (slow on cold caches)."
        ),
    ),
) -> None:
    """Rebuild derived artifacts (embeddings, graph, topics, etc.)."""
    if cite_resolution not in {"off", "crossref", "full"}:
        raise typer.BadParameter(
            f"--cite-resolution must be off|crossref|full, got {cite_resolution!r}"
        )
    paths = CorpusPaths(root=corpus_dir)
    refresh_corpus(
        paths,
        resolve_bibliography_doi=openalex,
        cite_resolution=cite_resolution,
    )
    typer.echo(f"refresh complete: {paths.root}")


@app.command("field-detect")
def field_detect_cmd(
    corpus_dir: Path = typer.Option(Path("data/corpora"), "--corpus"),
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


@app.command("trace")
def trace(
    bundle_dir: Path = typer.Option(..., "--bundle"),
    format: str = typer.Option("stats", "--format", help="stats | json | timeline"),
) -> None:
    """Analyse KG exploration trace from a distill run."""
    from .eval.trace_replay import exploration_timeline, load_trace, replay_stats

    bundle = BundlePaths(root=bundle_dir)
    trace_path = bundle.meta_dir / "kg_trace.jsonl"
    if not trace_path.exists():
        typer.echo(f"no trace file at {trace_path}")
        raise typer.Exit(1)

    entries = load_trace(trace_path)
    if format == "json":
        typer.echo(json.dumps([{
            "timestamp": e.timestamp, "caller": e.caller,
            "method": e.method, "args": e.args,
            "input_count": e.input_count, "output_count": e.output_count,
        } for e in entries], indent=2))
    elif format == "timeline":
        for step in exploration_timeline(entries):
            typer.echo(
                f"[{step['step']:4d}] {step['caller']:12s} "
                f"{step['method']:20s} {step['in']:>5d} -> {step['out']:>5d}  "
                f"{', '.join(step['sample'][:3])}"
            )
    else:
        stats = replay_stats(entries)
        typer.echo(f"total calls: {stats['total_calls']}")
        for caller, n in sorted(stats["calls_by_caller"].items()):
            methods = stats["methods_by_caller"].get(caller, [])
            typer.echo(f"  {caller}: {n} calls ({', '.join(methods)})")
        typer.echo(f"unique nodes visited: {stats['unique_nodes_visited']}")
        typer.echo(f"unique queries: {stats['unique_queries']}")
        if stats["queries"]:
            typer.echo("top queries:")
            for q in stats["queries"][:10]:
                typer.echo(f"  - {q}")


@app.command("sample-claims")
def sample_claims_cmd(
    bundle_dir: Path = typer.Option(..., "--bundle"),
    n: int = typer.Option(100, "--n", help="Number of claims to sample"),
    out: Path | None = typer.Option(None, "--out", help="Output JSON path"),
) -> None:
    """Sample factual claims from a bundle for human evaluation."""
    from .eval.claim_sampler import sample_claims, save_sample

    claims = sample_claims(bundle_dir, n=n)
    target = out or (bundle_dir / "_meta" / "claim_sample.json")
    save_sample(claims, target)
    typer.echo(f"sampled {len(claims)} claims -> {target}")


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
    from .store.corpus import all_chunks
    from .store.vectors import load_vectors
    from .store.vectors_meta import read_meta
    from .store.wiki_bundle import load_bundle

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

    caption_ids: list[str] = [
        c.id for c in chunks
        if c.section_path and c.section_path[0] == "__image__"
    ]
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
