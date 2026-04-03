"""Profile one epoch of the wiki pipeline on a subset of papers.

Instruments every LLM call with timing and token tracking. Produces a
detailed breakdown of where time and tokens are spent per pass.

Usage (from a terminal with ANTHROPIC_API_KEY set):
    uv run python benchmarks/profile_epoch.py --papers 50
    uv run python benchmarks/profile_epoch.py --papers 50 --dry-run   # mock LLM calls

The script patches wikify.llm.client.complete and complete_json to
intercept every call, then runs a single epoch via run_epoch().
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Token/timing tracker ────────────────────────────────────────────────────


class LLMTracker:
    """Intercept and track all LLM calls."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.calls: list[dict] = []
        self._original_complete = None
        self._original_complete_json = None

    def install(self):
        """Monkey-patch wikify.llm.client to track calls."""
        import wikify.llm.client as client

        self._original_complete = client.complete
        self._original_complete_json = client.complete_json

        tracker = self

        def tracked_complete(messages, model=None, temperature=0.3, max_tokens=4096, use_cache=True):
            input_chars = sum(len(m.get("content", "")) for m in messages)
            input_tokens_est = input_chars // 4
            model_used = model or "default"

            start = time.monotonic()

            if tracker.dry_run:
                # Return a plausible mock response
                result = tracker._mock_response(messages, model_used)
                output_chars = len(result)
            else:
                result = tracker._original_complete(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, use_cache=use_cache,
                )
                output_chars = len(result)

            elapsed = time.monotonic() - start
            output_tokens_est = output_chars // 4

            tracker.calls.append({
                "model": model_used,
                "input_tokens": input_tokens_est,
                "output_tokens": output_tokens_est,
                "latency_s": round(elapsed, 3),
                "cached": False,
                "type": "complete",
            })

            return result

        def tracked_complete_json(messages, model=None, temperature=0.3, max_tokens=4096):
            input_chars = sum(len(m.get("content", "")) for m in messages)
            input_tokens_est = input_chars // 4
            model_used = model or "default"

            start = time.monotonic()

            if tracker.dry_run:
                result = tracker._mock_json_response(messages, model_used)
                output_chars = len(json.dumps(result))
            else:
                result = tracker._original_complete_json(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens,
                )
                output_chars = len(json.dumps(result)) if result else 0

            elapsed = time.monotonic() - start
            output_tokens_est = output_chars // 4

            tracker.calls.append({
                "model": model_used,
                "input_tokens": input_tokens_est,
                "output_tokens": output_tokens_est,
                "latency_s": round(elapsed, 3),
                "cached": False,
                "type": "complete_json",
            })

            return result

        client.complete = tracked_complete
        client.complete_json = tracked_complete_json
        logger.info("LLM tracker installed (dry_run=%s)", self.dry_run)

    def uninstall(self):
        """Restore original functions."""
        import wikify.llm.client as client
        if self._original_complete:
            client.complete = self._original_complete
        if self._original_complete_json:
            client.complete_json = self._original_complete_json

    def _mock_response(self, messages, model):
        """Return a plausible mock for complete() calls."""
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")

        if "YES or NO" in user_msg:
            return "YES"
        if "Wikipedia-style article" in user_msg:
            return (
                "## Definition\nMock concept definition.\n\n"
                "## Mechanism / Process\nMock mechanism.\n\n"
                "## Key Facts\n- Fact 1\n- Fact 2\n\n"
                "## In This Corpus\nMock corpus context.\n\n"
                "## Relationships\n| Related | Relation | Notes |\n|---|---|---|\n\n"
                "## Open Questions\n- Question 1\n"
            )
        if "domain" in user_msg.lower() and "persona" in user_msg.lower():
            return "You are a senior materials scientist specializing in thin film deposition."
        return "Mock LLM response for profiling."

    def _mock_json_response(self, messages, model):
        """Return a plausible mock for complete_json() calls."""
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")

        if "Extract named concepts" in user_msg:
            return [
                {"name": "Mock Concept A", "type": "technique", "aliases": ["MCA"], "definition": "A mock technique."},
                {"name": "Mock Concept B", "type": "material", "aliases": [], "definition": "A mock material."},
            ]
        if "coherent domain" in user_msg.lower() or "form a coherent" in user_msg.lower():
            return {"coherent": True, "label": "Mock Domain", "scope": "Mock scope", "core_concepts": ["Mock Concept A"], "split_proposal": None}
        if "merge" in user_msg.lower() and "community" in user_msg.lower():
            return {"merge": False, "reason": "Distinct topics"}
        if "YES" in user_msg or "NO" in user_msg:
            return "NO"
        return []

    def report(self) -> dict:
        """Generate a summary report."""
        by_model: dict[str, dict] = defaultdict(lambda: {
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_latency": 0.0,
        })

        for call in self.calls:
            m = by_model[call["model"]]
            m["calls"] += 1
            m["input_tokens"] += call["input_tokens"]
            m["output_tokens"] += call["output_tokens"]
            m["total_latency"] += call["latency_s"]

        total_calls = len(self.calls)
        total_input = sum(c["input_tokens"] for c in self.calls)
        total_output = sum(c["output_tokens"] for c in self.calls)
        total_latency = sum(c["latency_s"] for c in self.calls)

        # Cost estimation (Anthropic pricing as of 2025)
        cost = 0.0
        for model_name, stats in by_model.items():
            if "haiku" in model_name:
                cost += stats["input_tokens"] * 0.80 / 1_000_000
                cost += stats["output_tokens"] * 4.00 / 1_000_000
            elif "sonnet" in model_name:
                cost += stats["input_tokens"] * 3.00 / 1_000_000
                cost += stats["output_tokens"] * 15.00 / 1_000_000
            else:
                cost += stats["input_tokens"] * 3.00 / 1_000_000
                cost += stats["output_tokens"] * 15.00 / 1_000_000

        return {
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_latency_s": round(total_latency, 1),
            "estimated_cost_usd": round(cost, 4),
            "by_model": dict(by_model),
        }


# ── Epoch runner with profiling ─────────────────────────────────────────────


def run_profiled_epoch(n_papers: int, dry_run: bool) -> dict:
    """Run one epoch on a subset of papers with full profiling."""
    from sqlmodel import func, select

    from wikify.store.db import get_session
    from wikify.store.models import Paper

    # Select N papers
    with get_session() as session:
        papers = list(
            session.exec(
                select(Paper)
                .where(Paper.origin == "corpus")
                .order_by(func.random())
                .limit(n_papers)
            ).all()
        )
    paper_ids = [p.id for p in papers]
    logger.info("Selected %d papers for profiled epoch", len(paper_ids))

    # Install tracker
    tracker = LLMTracker(dry_run=dry_run)
    tracker.install()

    # Time each pass
    timings: dict[str, float] = {}

    try:
        # ── Pass 1: Concept Discovery ────────────────────────────────────
        from wikify.wiki.concepts import clear_staged_extractions, discover_concepts

        t0 = time.monotonic()
        epoch = 999  # test epoch
        clear_staged_extractions(epoch)
        concepts = discover_concepts(paper_ids, epoch)
        timings["pass_1_discovery"] = round(time.monotonic() - t0, 1)
        pass1_calls = len(tracker.calls)
        logger.info(
            "Pass 1: %d concepts, %d LLM calls, %.1fs",
            len(concepts), pass1_calls, timings["pass_1_discovery"],
        )

        # ── Pass 2: Graph Building ───────────────────────────────────────
        from wikify.wiki.concept_graph import (
            build_concept_graph,
            classify_node_roles,
            extract_relations,
            save_relations,
            score_importance,
            update_concept_importance,
        )

        t0 = time.monotonic()
        graph = build_concept_graph("", epoch)
        scores = score_importance(graph)
        update_concept_importance(scores)
        roles = classify_node_roles(graph, scores)
        relations = extract_relations(graph, epoch)
        save_relations(relations, epoch)
        timings["pass_2_graph"] = round(time.monotonic() - t0, 1)
        pass2_calls = len(tracker.calls) - pass1_calls
        logger.info(
            "Pass 2: %d nodes, %d edges, %d LLM calls, %.1fs",
            graph.number_of_nodes(), graph.number_of_edges(),
            pass2_calls, timings["pass_2_graph"],
        )

        # ── Pass 2b: Domain Discovery ────────────────────────────────────
        from wikify.wiki.domains import discover_domains

        t0 = time.monotonic()
        pre_2b = len(tracker.calls)
        clusters = discover_domains(graph, epoch)
        timings["pass_2b_domains"] = round(time.monotonic() - t0, 1)
        pass2b_calls = len(tracker.calls) - pre_2b
        logger.info(
            "Pass 2b: %d domains, %d LLM calls, %.1fs",
            len(clusters), pass2b_calls, timings["pass_2b_domains"],
        )

        # ── Pass 3: Article Writing (sample only) ────────────────────────
        from wikify.wiki.article import should_write_full, write_concept_article
        from wikify.wiki.builder import article_path, write_article
        from wikify.wiki.concepts import list_concepts
        from wikify.wiki.mapreduce import HAIKU_MODEL, map_chunks_to_topic

        t0 = time.monotonic()
        pre_3 = len(tracker.calls)
        all_concepts = list_concepts(min_importance=0.0)
        all_concepts.sort(key=lambda c: c.importance, reverse=True)

        # Only write articles for top 10 concepts to keep manageable
        max_articles = min(10, len(all_concepts))
        articles_written = 0
        wiki_dir = Path("data/wiki")
        wiki_dir.mkdir(parents=True, exist_ok=True)

        for concept in all_concepts[:max_articles]:
            if concept.article_status != "none":
                continue
            try:
                neighbor_ids = list(graph.neighbors(concept.id)) if concept.id in graph else []
                with get_session() as session:
                    neighbors = [
                        session.get(type(concept), nid)
                        for nid in neighbor_ids
                        if session.get(type(concept), nid) is not None
                    ]

                body = write_concept_article(concept, neighbors, "", None)

                fpath = article_path(wiki_dir, "concepts", concept.id)
                write_article(fpath, concept.name, body, [], [], "stub", "")
                articles_written += 1
            except Exception:
                logger.exception("Pass 3: failed on %s", concept.name)

        timings["pass_3_articles"] = round(time.monotonic() - t0, 1)
        pass3_calls = len(tracker.calls) - pre_3
        logger.info(
            "Pass 3: %d articles written, %d LLM calls, %.1fs",
            articles_written, pass3_calls, timings["pass_3_articles"],
        )

        # ── Pass 4: Cross-linking ────────────────────────────────────────
        from wikify.wiki.linker import cross_link_articles

        t0 = time.monotonic()
        cross_refs = cross_link_articles(wiki_dir, sitemap=None)
        timings["pass_4_crosslink"] = round(time.monotonic() - t0, 1)
        logger.info("Pass 4: %d cross-refs, %.1fs", cross_refs, timings["pass_4_crosslink"])

        # ── Pass 5: Index ────────────────────────────────────────────────
        from wikify.wiki.builder import generate_wiki_index

        t0 = time.monotonic()
        generate_wiki_index(wiki_dir)
        timings["pass_5_index"] = round(time.monotonic() - t0, 1)
        logger.info("Pass 5: index rebuilt, %.1fs", timings["pass_5_index"])

    finally:
        tracker.uninstall()

    # ── Report ───────────────────────────────────────────────────────────
    llm_report = tracker.report()
    total_time = sum(timings.values())

    report = {
        "timestamp": datetime.now().isoformat(),
        "papers": len(paper_ids),
        "dry_run": dry_run,
        "concepts_discovered": len(concepts),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "domains_discovered": len(clusters),
        "articles_written": articles_written,
        "cross_refs": cross_refs,
        "timings": timings,
        "total_time_s": round(total_time, 1),
        "llm": llm_report,
    }

    return report


def print_report(report: dict) -> None:
    """Pretty-print the profiling report."""
    mode = "DRY RUN" if report["dry_run"] else "LIVE"
    print(f"\n{'=' * 65}")
    print(f"  Epoch Profile ({mode}) - {report['papers']} papers")
    print(f"{'=' * 65}")
    print(f"  Concepts discovered:  {report['concepts_discovered']}")
    print(f"  Graph:                {report['graph_nodes']} nodes, {report['graph_edges']} edges")
    print(f"  Domains:              {report['domains_discovered']}")
    print(f"  Articles written:     {report['articles_written']}")
    print(f"  Cross-refs added:     {report['cross_refs']}")
    print()

    print("  Timing breakdown:")
    total = report["total_time_s"]
    for pass_name, secs in report["timings"].items():
        pct = secs / total * 100 if total > 0 else 0
        bar = "#" * int(pct / 2)
        print(f"    {pass_name:25s}  {secs:7.1f}s  ({pct:4.1f}%)  {bar}")
    print(f"    {'TOTAL':25s}  {total:7.1f}s")
    print()

    llm = report["llm"]
    print("  LLM usage:")
    print(f"    Total calls:        {llm['total_calls']}")
    print(f"    Input tokens:       {llm['total_input_tokens']:,}")
    print(f"    Output tokens:      {llm['total_output_tokens']:,}")
    print(f"    Total tokens:       {llm['total_tokens']:,}")
    print(f"    LLM latency:        {llm['total_latency_s']:.1f}s")
    print(f"    Estimated cost:     ${llm['estimated_cost_usd']:.4f}")
    print()

    print("  By model:")
    for model_name, stats in llm["by_model"].items():
        print(f"    {model_name}:")
        print(f"      calls={stats['calls']}, in={stats['input_tokens']:,}, out={stats['output_tokens']:,}, latency={stats['total_latency']:.1f}s")
    print(f"{'=' * 65}\n")


def main():
    parser = argparse.ArgumentParser(description="Profile one epoch")
    parser.add_argument("--papers", type=int, default=50, help="Number of papers")
    parser.add_argument("--dry-run", action="store_true", help="Mock LLM calls")
    args = parser.parse_args()

    report = run_profiled_epoch(args.papers, args.dry_run)
    print_report(report)

    # Save report
    out_path = Path("benchmarks/results/epoch_profile.json")
    out_path.write_text(json.dumps(report, indent=2))
    logger.info("Report saved to %s", out_path)


if __name__ == "__main__":
    main()
