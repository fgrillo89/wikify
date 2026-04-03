"""Benchmark: haiku vs local ONNX models for concept extraction (Pass 1).

Selects representative chunks from the corpus, runs concept extraction
with haiku (ground truth) and local ONNX models, then measures precision,
recall, JSON validity, and latency.

Usage:
    uv run python benchmarks/concept_extraction_benchmark.py --haiku-only     # generate ground truth
    uv run python benchmarks/concept_extraction_benchmark.py --model phi3.5    # benchmark Phi-3.5
    uv run python benchmarks/concept_extraction_benchmark.py --model qwen2.5   # benchmark Qwen2.5
    uv run python benchmarks/concept_extraction_benchmark.py --compare         # compare all results
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from sqlmodel import func, select

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BENCHMARK_DIR = Path("benchmarks/results")
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

# Model paths (ONNX, relative to project root)
MODEL_PATHS = {
    "phi3.5": "data/cache/models/phi-3.5-mini-instruct-onnx/gpu/gpu-int4-awq-block-128",
}

# The extraction prompt (same as wiki/concepts.py::_extract_from_chunk)
EXTRACTION_PROMPT = (
    "Extract named concepts from the following text excerpt.\n\n"
    "Previously extracted concepts from earlier sections of this source: none. "
    "Do not re-extract these unless this section adds new information about them.\n\n"
    "Return a JSON array -- and ONLY the JSON array, no prose -- where each element has:\n"
    '  "name":       canonical display name (e.g. "Atomic Layer Deposition")\n'
    '  "type":       one of: technique | material | phenomenon | method | theory | dataset\n'
    '  "aliases":    list of abbreviations / alternate names (may be empty list)\n'
    '  "definition": one-sentence definition (max 25 words)\n\n'
    "Include only concepts that are clearly named and domain-specific. "
    "Skip generic terms like 'experiment', 'data', 'result'.\n\n"
    "--- TEXT ---\n"
    "{chunk_text}\n"
    "--- END TEXT ---"
)

VALID_TYPES = {"technique", "material", "phenomenon", "method", "theory", "dataset"}


# ── Chunk selection ──────────────────────────────────────────────────────────


def select_benchmark_chunks(n: int = 20) -> list[dict]:
    """Select n representative chunks spanning diverse sections and papers."""
    from wikify.store.db import get_session
    from wikify.store.models import Chunk

    # Target distribution: body, methods, results, introduction, abstract
    section_targets = {
        "body": 6,
        "methods": 4,
        "results": 4,
        "introduction": 3,
        "abstract": 3,
    }

    selected: list[dict] = []

    with get_session() as session:
        for section_type, count in section_targets.items():
            chunks = list(
                session.exec(
                    select(Chunk)
                    .where(Chunk.section_type == section_type)
                    .where(func.length(Chunk.content) > 200)
                    .order_by(func.random())
                    .limit(count)
                ).all()
            )
            for c in chunks:
                selected.append(
                    {
                        "chunk_id": c.id,
                        "paper_id": c.paper_id,
                        "section_type": c.section_type,
                        "content": c.content,
                        "token_count": c.token_count,
                    }
                )

    logger.info("Selected %d benchmark chunks across %d section types", len(selected), len(section_targets))
    return selected


# ── Extraction runners ───────────────────────────────────────────────────────


def run_haiku_extraction(chunks: list[dict]) -> list[dict]:
    """Run concept extraction with haiku (ground truth)."""
    from wikify.llm.client import complete_json

    results = []
    haiku = "claude-haiku-4-5-20251001"

    for i, chunk in enumerate(chunks):
        prompt = EXTRACTION_PROMPT.format(chunk_text=chunk["content"])
        start = time.monotonic()

        try:
            raw = complete_json(
                messages=[{"role": "user", "content": prompt}],
                model=haiku,
                temperature=0.1,
                max_tokens=1024,
            )
            elapsed = time.monotonic() - start
            json_valid = True
        except Exception as exc:
            logger.warning("Haiku failed on chunk %d: %s", i, exc)
            raw = []
            elapsed = time.monotonic() - start
            json_valid = False

        if not isinstance(raw, list):
            raw = []
            json_valid = False

        concepts = _normalize_concepts(raw)

        results.append(
            {
                "chunk_id": chunk["chunk_id"],
                "section_type": chunk["section_type"],
                "concepts": concepts,
                "raw_concept_count": len(raw),
                "json_valid": json_valid,
                "latency_s": round(elapsed, 3),
            }
        )
        logger.info(
            "  [haiku] chunk %d/%d: %d concepts, %.2fs",
            i + 1,
            len(chunks),
            len(concepts),
            elapsed,
        )

    return results


def run_onnx_extraction(chunks: list[dict], model_key: str) -> list[dict]:
    """Run concept extraction with a local ONNX model."""
    from wikify.llm.onnx_provider import OnnxProvider

    model_path = MODEL_PATHS.get(model_key)
    if not model_path or not Path(model_path).exists():
        logger.error("Model path not found: %s", model_path)
        logger.error("Available models: %s", list(MODEL_PATHS.keys()))
        return []

    provider = OnnxProvider(model_path)

    results = []
    for i, chunk in enumerate(chunks):
        prompt = EXTRACTION_PROMPT.format(chunk_text=chunk["content"])
        start = time.monotonic()

        try:
            raw = provider.complete_json(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.1,
            )
            elapsed = time.monotonic() - start
            json_valid = True
        except Exception as exc:
            logger.warning("ONNX (%s) failed on chunk %d: %s", model_key, i, exc)
            raw = []
            elapsed = time.monotonic() - start
            json_valid = False

        if not isinstance(raw, list):
            raw = []
            json_valid = False

        concepts = _normalize_concepts(raw)

        results.append(
            {
                "chunk_id": chunk["chunk_id"],
                "section_type": chunk["section_type"],
                "concepts": concepts,
                "raw_concept_count": len(raw),
                "json_valid": json_valid,
                "latency_s": round(elapsed, 3),
            }
        )
        logger.info(
            "  [%s] chunk %d/%d: %d concepts, %.2fs",
            model_key,
            i + 1,
            len(chunks),
            len(concepts),
            elapsed,
        )

    return results


def _normalize_concepts(raw: list) -> list[dict]:
    """Normalize extracted concepts for comparison."""
    concepts = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        ctype = (item.get("type") or "").strip().lower()
        if ctype not in VALID_TYPES:
            ctype = ""
        concepts.append(
            {
                "name": name.lower(),
                "type": ctype,
                "aliases": [
                    str(a).strip().lower()
                    for a in (item.get("aliases") or [])
                    if isinstance(a, str) and str(a).strip()
                ],
            }
        )
    return concepts


# ── Comparison ───────────────────────────────────────────────────────────────


def compare_results(ground_truth_file: str, candidate_file: str) -> dict:
    """Compare candidate extraction results against haiku ground truth."""
    gt = json.loads(Path(ground_truth_file).read_text())
    cand = json.loads(Path(candidate_file).read_text())

    gt_by_chunk = {r["chunk_id"]: r for r in gt}
    cand_by_chunk = {r["chunk_id"]: r for r in cand}

    total_precision_hits = 0
    total_precision_attempts = 0
    total_recall_hits = 0
    total_recall_attempts = 0
    json_valid_count = 0
    total_latency_gt = 0.0
    total_latency_cand = 0.0
    chunk_count = 0

    for chunk_id in gt_by_chunk:
        if chunk_id not in cand_by_chunk:
            continue

        gt_result = gt_by_chunk[chunk_id]
        cand_result = cand_by_chunk[chunk_id]

        gt_names = {c["name"] for c in gt_result["concepts"]}
        cand_names = {c["name"] for c in cand_result["concepts"]}

        # Also check aliases for fuzzy matching
        gt_all = set()
        for c in gt_result["concepts"]:
            gt_all.add(c["name"])
            gt_all.update(c.get("aliases", []))

        cand_all = set()
        for c in cand_result["concepts"]:
            cand_all.add(c["name"])
            cand_all.update(c.get("aliases", []))

        # Precision: what fraction of candidate concepts are real (in GT)?
        for cn in cand_names:
            total_precision_attempts += 1
            if cn in gt_names or cn in gt_all:
                total_precision_hits += 1

        # Recall: what fraction of GT concepts were found by candidate?
        for gn in gt_names:
            total_recall_attempts += 1
            if gn in cand_names or gn in cand_all:
                total_recall_hits += 1

        if cand_result["json_valid"]:
            json_valid_count += 1

        total_latency_gt += gt_result["latency_s"]
        total_latency_cand += cand_result["latency_s"]
        chunk_count += 1

    precision = total_precision_hits / total_precision_attempts if total_precision_attempts > 0 else 0.0
    recall = total_recall_hits / total_recall_attempts if total_recall_attempts > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    json_validity = json_valid_count / chunk_count if chunk_count > 0 else 0.0
    avg_latency_gt = total_latency_gt / chunk_count if chunk_count > 0 else 0.0
    avg_latency_cand = total_latency_cand / chunk_count if chunk_count > 0 else 0.0
    speedup = avg_latency_gt / avg_latency_cand if avg_latency_cand > 0 else 0.0

    report = {
        "chunks_compared": chunk_count,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "json_validity": round(json_validity, 3),
        "avg_latency_gt_s": round(avg_latency_gt, 3),
        "avg_latency_cand_s": round(avg_latency_cand, 3),
        "speedup_vs_haiku": round(speedup, 2),
        "pass_threshold": precision >= 0.85 and recall >= 0.75 and json_validity >= 0.95,
    }

    return report


def print_report(report: dict, model_name: str) -> None:
    """Pretty-print a comparison report."""
    print(f"\n{'=' * 60}")
    print(f"  Concept Extraction Benchmark: {model_name} vs Haiku")
    print(f"{'=' * 60}")
    print(f"  Chunks compared:    {report['chunks_compared']}")
    print(f"  Precision:          {report['precision']:.1%}  (target: >= 85%)")
    print(f"  Recall:             {report['recall']:.1%}  (target: >= 75%)")
    print(f"  F1:                 {report['f1']:.1%}")
    print(f"  JSON validity:      {report['json_validity']:.1%}  (target: >= 95%)")
    print(f"  Avg latency (haiku): {report['avg_latency_gt_s']:.2f}s")
    print(f"  Avg latency (local): {report['avg_latency_cand_s']:.2f}s")
    print(f"  Speedup:            {report['speedup_vs_haiku']:.1f}x")
    passed = report["pass_threshold"]
    status = "PASS" if passed else "FAIL"
    print(f"  Overall:            {status}")
    print(f"{'=' * 60}\n")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Concept extraction benchmark")
    parser.add_argument("--haiku-only", action="store_true", help="Generate ground truth only")
    parser.add_argument("--model", type=str, help="Local model to benchmark (phi3.5, qwen2.5)")
    parser.add_argument("--compare", action="store_true", help="Compare all available results")
    parser.add_argument("--chunks", type=int, default=20, help="Number of chunks to benchmark")
    args = parser.parse_args()

    chunks_file = BENCHMARK_DIR / "benchmark_chunks.json"

    if args.compare:
        gt_file = BENCHMARK_DIR / "haiku_results.json"
        if not gt_file.exists():
            logger.error("No ground truth found. Run --haiku-only first.")
            return

        for model_key in MODEL_PATHS:
            cand_file = BENCHMARK_DIR / f"{model_key}_results.json"
            if cand_file.exists():
                report = compare_results(str(gt_file), str(cand_file))
                print_report(report, model_key)

                report_file = BENCHMARK_DIR / f"{model_key}_report.json"
                report_file.write_text(json.dumps(report, indent=2))
            else:
                logger.info("No results for %s (run --model %s first)", model_key, model_key)
        return

    # Select or load chunks
    if chunks_file.exists():
        chunks = json.loads(chunks_file.read_text())
        logger.info("Loaded %d cached benchmark chunks", len(chunks))
    else:
        chunks = select_benchmark_chunks(args.chunks)
        chunks_file.write_text(json.dumps(chunks, indent=2))
        logger.info("Saved %d benchmark chunks to %s", len(chunks), chunks_file)

    if args.haiku_only or not args.model:
        logger.info("\n--- Running haiku extraction (ground truth) ---")
        haiku_results = run_haiku_extraction(chunks)
        out_file = BENCHMARK_DIR / "haiku_results.json"
        out_file.write_text(json.dumps(haiku_results, indent=2))
        logger.info("Haiku results saved to %s", out_file)

        total_concepts = sum(r["raw_concept_count"] for r in haiku_results)
        avg_latency = sum(r["latency_s"] for r in haiku_results) / len(haiku_results)
        logger.info(
            "Haiku summary: %d chunks, %d total concepts, %.2fs avg latency",
            len(haiku_results),
            total_concepts,
            avg_latency,
        )

    if args.model:
        if args.model not in MODEL_PATHS:
            logger.error("Unknown model: %s. Available: %s", args.model, list(MODEL_PATHS.keys()))
            return

        logger.info("\n--- Running %s extraction ---", args.model)
        onnx_results = run_onnx_extraction(chunks, args.model)
        out_file = BENCHMARK_DIR / f"{args.model}_results.json"
        out_file.write_text(json.dumps(onnx_results, indent=2))
        logger.info("%s results saved to %s", args.model, out_file)

        # Auto-compare if ground truth exists
        gt_file = BENCHMARK_DIR / "haiku_results.json"
        if gt_file.exists():
            report = compare_results(str(gt_file), str(out_file))
            print_report(report, args.model)

            report_file = BENCHMARK_DIR / f"{args.model}_report.json"
            report_file.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
