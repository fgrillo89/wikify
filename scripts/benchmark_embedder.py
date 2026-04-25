"""Benchmark two embedder models on the same corpus.

Runs ingest twice (once per model), captures embed time, chunk count, and
vector file size, and optionally evaluates coverage residual and concept
recall against an existing wiki bundle. Prints a side-by-side table.

Examples
--------
# Minimal: just ingest metrics on a sources directory
uv run python scripts/benchmark_embedder.py \\
    --sources corpus_sources/mvp20 \\
    --out /tmp/embedder_bench

# Full: include eval metrics against a bundle
uv run python scripts/benchmark_embedder.py \\
    --sources corpus_sources/mvp20 \\
    --out /tmp/embedder_bench \\
    --bundle wiki_bundles/mvp20 \\
    --topics atomic-layer-deposition precursor nucleation
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

MODELS: list[tuple[str, str]] = [
    ("MiniLM", "sentence-transformers/all-MiniLM-L6-v2"),
    ("nomic-Q", "nomic-ai/nomic-embed-text-v1.5-Q"),
]


def _run_one(label: str, model: str, sources: Path, corpus_root: Path) -> dict:
    os.environ["WIKIFY_EMBED_MODEL"] = model
    os.environ["WIKIFY_EMBEDDER"] = "fastembed"
    if corpus_root.exists():
        shutil.rmtree(corpus_root)

    # Imports happen inside the function so each run re-reads env vars and
    # picks up a fresh embedder module state.
    for mod in list(sys.modules):
        if mod.startswith("wikify."):
            del sys.modules[mod]
    from wikify.embedding import current_backend
    from wikify.ingest.pipeline import ingest_corpus
    from wikify.corpus.chunks import read_chunks
    from wikify.corpus.vectors import read_vector_store

    backend = current_backend()

    t0 = time.monotonic()
    paths = ingest_corpus(sources, corpus_root, max_workers=1)
    elapsed = time.monotonic() - t0

    chunks = list(read_chunks(paths))
    vectors = read_vector_store(paths)
    vec_mb = 0.0
    if paths.vectors_path.exists():
        vec_mb = paths.vectors_path.stat().st_size / (1024 * 1024)

    return {
        "label": label,
        "model": model,
        "dim": backend["dim"],
        "max_tokens": backend["max_tokens"],
        "n_chunks": len(chunks),
        "n_vectors": len(vectors.ids) if vectors.ids else 0,
        "embed_seconds": elapsed,
        "vectors_mb": vec_mb,
        "corpus": paths,
    }


def _run_eval(result: dict, bundle_path: Path, topics: list[str]) -> dict:
    """Optional: add coverage_residual + concept_recall to ``result``."""
    for mod in list(sys.modules):
        if mod.startswith("wikify."):
            del sys.modules[mod]
    from wikify.embedding import embedder_for
    from wikify.eval.metrics import concept_recall, coverage_residual
    from wikify.corpus.chunks import read_chunks
    from wikify.corpus.vectors_meta import read_meta
    from wikify.bundle.wiki.page import load_bundle

    corpus = result["corpus"]
    meta = read_meta(corpus.vectors_path)
    embed_passage = embedder_for(meta.backend, meta.model, mode="passage")
    embed_query = embedder_for(meta.backend, meta.model, mode="query")

    bundle = load_bundle(bundle_path)
    chunks = list(read_chunks(corpus))
    chunk_embeds = embed_passage([c.text for c in chunks])

    residual = coverage_residual(bundle, chunk_embeds, embed_passage)
    result["coverage_residual"] = residual

    if topics:
        topic_embeds = embed_query(topics)
        recall = concept_recall(bundle, topics, topic_embeds, embed_query)
        result["concept_recall"] = recall
    return result


def _print_table(rows: list[dict]) -> None:
    cols = [
        ("label", "model"),
        ("model", "hf name"),
        ("dim", "dim"),
        ("max_tokens", "max_tok"),
        ("n_chunks", "chunks"),
        ("embed_seconds", "embed_s"),
        ("vectors_mb", "vec_MB"),
        ("coverage_residual", "cov_res"),
        ("concept_recall", "recall"),
    ]
    header = " | ".join(h[1].rjust(12) for h in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        parts = []
        for key, _ in cols:
            val = r.get(key)
            if val is None:
                parts.append("".rjust(12))
            elif isinstance(val, float):
                parts.append(f"{val:12.3f}")
            elif isinstance(val, int):
                parts.append(f"{val:12d}")
            else:
                parts.append(str(val).rjust(12)[-12:])
        print(" | ".join(parts))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, required=True,
        help="Scratch directory for per-model ingest output (wiped each run).",
    )
    ap.add_argument(
        "--bundle", type=Path, default=None,
        help="Existing wiki bundle to score with eval metrics (optional).",
    )
    ap.add_argument(
        "--topics", nargs="*", default=[],
        help="Topic strings for concept_recall (requires --bundle).",
    )
    args = ap.parse_args()

    if not args.sources.exists():
        print(f"sources not found: {args.sources}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for label, model in MODELS:
        corpus_root = args.out / label
        print(f"[{label}] {model} -> {corpus_root}", file=sys.stderr)
        row = _run_one(label, model, args.sources, corpus_root)
        if args.bundle is not None:
            try:
                row = _run_eval(row, args.bundle, args.topics)
            except Exception as exc:  # noqa: BLE001
                print(f"[{label}] eval skipped: {exc}", file=sys.stderr)
        rows.append(row)
        print(
            f"[{label}] chunks={row['n_chunks']} "
            f"embed={row['embed_seconds']:.1f}s "
            f"vectors={row['vectors_mb']:.1f} MB",
            file=sys.stderr,
        )

    print()
    _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
