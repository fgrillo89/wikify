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
) -> None:
    """Run a distillation strategy on an ingested corpus."""
    if strategy not in STRATEGIES:
        raise typer.BadParameter(f"unknown strategy: {strategy}")
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

    extractor, writer = _wire_binding(binding, cache, meter)

    cfg = STRATEGIES[strategy](seed=seed)
    pipeline_run(
        corpus=CorpusPaths(root=corpus_dir),
        bundle=bundle,
        strategy=cfg,
        extractor=extractor,
        writer=writer,
        meter=meter,
        budget_haiku_eq=budget_haiku_eq,
        feed=feed,
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
        from .bindings.fake import FakeExtractor, FakeWriter

        return FakeExtractor(cache, meter), FakeWriter(meter)
    if name == "claude_code":
        from .bindings.claude_code import ClaudeCodeExtractor, ClaudeCodeWriter

        return ClaudeCodeExtractor(cache, meter), ClaudeCodeWriter(meter)
    raise typer.BadParameter(f"unknown binding: {name}")


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


if __name__ == "__main__":
    app()
