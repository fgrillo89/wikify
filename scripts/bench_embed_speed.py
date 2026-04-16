"""Benchmark embedder throughput on already-ingested chunks.

Skips parsing / ingest; just loads chunk texts from an existing corpus and
times each model end-to-end on the same inputs.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("WIKIFY_EMBEDDER", "fastembed")

MODELS: list[tuple[str, str]] = [
    ("MiniLM-L6", "sentence-transformers/all-MiniLM-L6-v2"),
    ("jina-v2-small", "jinaai/jina-embeddings-v2-small-en"),
]


def _load_texts(corpus_root: Path) -> list[str]:
    import json

    texts: list[str] = []
    for jsonl in sorted((corpus_root / "chunks").glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            texts.append(obj["text"])
    return texts


def _run(label: str, model: str, texts: list[str], batch_size: int | None) -> dict:
    import wikify.embedding as e

    e._fe_model = None
    e._fe_model_id = None
    os.environ["WIKIFY_EMBED_MODEL"] = model

    cfg = e.model_config(model)
    bs = batch_size if batch_size is not None else cfg.batch_size

    t_load_start = time.monotonic()
    _ = e.embed_passages(texts[:1], batch_size=bs)
    t_load = time.monotonic() - t_load_start

    t0 = time.monotonic()
    arr = e.embed_passages(texts, batch_size=bs)
    t1 = time.monotonic() - t0

    backend = e.current_backend()

    return {
        "label": label,
        "model": model,
        "dim": backend["dim"],
        "max_tok": backend["max_tokens"],
        "batch": bs,
        "n_texts": len(texts),
        "load_s": t_load,
        "embed_s": t1,
        "rate_per_s": len(texts) / t1 if t1 > 0 else float("inf"),
        "vec_MB": arr.nbytes / (1024 * 1024),
    }


def _print(rows: list[dict]) -> None:
    cols = [
        ("label", "model", 15),
        ("dim", "dim", 5),
        ("max_tok", "max_tok", 8),
        ("batch", "batch", 6),
        ("n_texts", "n", 6),
        ("load_s", "load_s", 8),
        ("embed_s", "embed_s", 8),
        ("rate_per_s", "/sec", 8),
        ("vec_MB", "MB", 6),
    ]
    header = " ".join(h[1].rjust(h[2]) for h in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        parts = []
        for key, _, w in cols:
            val = r.get(key)
            if isinstance(val, float):
                parts.append(f"{val:{w}.2f}")
            elif isinstance(val, int):
                parts.append(f"{val:{w}d}")
            else:
                parts.append(str(val).rjust(w)[-w:])
        print(" ".join(parts))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, required=True,
                    help="existing corpus root containing chunks/*.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cpu", action="store_true",
                    help="force CPU execution (skip CUDA/DML auto-select)")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="override per-model default batch size")
    args = ap.parse_args()

    # Force providers BEFORE importing any wikify module that loads fastembed.
    if args.cpu:
        import wikify.embedding as e
        e._onnx_providers = lambda: ["CPUExecutionProvider"]

    texts = _load_texts(args.corpus)
    if args.limit > 0:
        texts = texts[: args.limit]
    print(f"loaded {len(texts)} chunks from {args.corpus}", file=sys.stderr)

    rows = []
    for label, model in MODELS:
        print(f"[{label}] {model}", file=sys.stderr)
        row = _run(label, model, texts, batch_size=args.batch_size)
        rows.append(row)
        print(
            f"[{label}] load={row['load_s']:.1f}s "
            f"embed={row['embed_s']:.1f}s "
            f"rate={row['rate_per_s']:.1f}/s",
            file=sys.stderr,
        )

    print()
    _print(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
