"""CLI entry point: python -m wikify.citestore"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from pathlib import Path

from .db import DatabaseManager
from .resolver import AsyncResolver

logger = logging.getLogger("citestore")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m wikify.citestore",
        description="Resolve academic citations via OpenAlex and store in SQLite.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=Path("citestore.db"),
        help="Path to SQLite database (default: citestore.db)",
    )
    p.add_argument(
        "--email",
        required=True,
        help="Email for OpenAlex polite pool (included in User-Agent)",
    )
    p.add_argument(
        "--dois",
        type=str,
        default="",
        help="Comma-separated DOIs to resolve",
    )
    p.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        help="JSON file with list of citation dicts (raw_text, doi, year, author_last_names)",
    )
    p.add_argument(
        "--no-expand",
        action="store_true",
        help="Skip depth-1 reference expansion",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory for .bib and .csv output (default: current dir)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent API requests (default: 3)",
    )
    p.add_argument(
        "--rate-limit",
        type=float,
        default=3.0,
        help="Requests per second (default: 3)",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args(argv)


def _build_citations(args: argparse.Namespace) -> list[dict]:
    """Build citation input list from CLI args."""
    citations: list[dict] = []

    if args.dois:
        for doi in args.dois.split(","):
            doi = doi.strip()
            if doi:
                citations.append({"doi": doi, "raw_text": doi})

    if args.input_file:
        with open(args.input_file) as f:
            data = json.load(f)
        if isinstance(data, list):
            citations.extend(data)
        else:
            print(f"Error: {args.input_file} should contain a JSON array", file=sys.stderr)
            sys.exit(1)

    return citations


def _progress(done: int, total: int, result: object) -> None:
    print(f"\r  Resolved {done}/{total}", end="", flush=True)


async def _run(args: argparse.Namespace) -> None:
    citations = _build_citations(args)
    if not citations:
        print("No citations to resolve. Provide --dois or an input file.", file=sys.stderr)
        sys.exit(1)

    print(f"Resolving {len(citations)} citations...")

    async with DatabaseManager(args.db) as db:
        resolver = AsyncResolver(
            db,
            email=args.email,
            max_concurrent=args.concurrency,
            requests_per_second=args.rate_limit,
            expand_references=not args.no_expand,
        )
        try:
            results = await resolver.resolve_batch(
                citations, progress_callback=_progress,
            )
        finally:
            await resolver.close()

        print()  # newline after progress

        # Summary
        resolved = sum(1 for r in results if r.work)
        by_level = {}
        for r in results:
            by_level[r.level] = by_level.get(r.level, 0) + 1
        print(f"Resolved: {resolved}/{len(results)}")
        for level in ("A", "B", "C", "miss"):
            if level in by_level:
                print(f"  Level {level}: {by_level[level]}")

        # Export .bib
        args.output_dir.mkdir(parents=True, exist_ok=True)
        bib_path = args.output_dir / "combined.bib"
        all_works = await db.get_all_works()
        with open(bib_path, "w", encoding="utf-8") as f:
            for w in all_works:
                if w.bibtex:
                    f.write(w.bibtex)
                    f.write("\n\n")
        print(f"Wrote {len(all_works)} entries to {bib_path}")

        # Export graph_summary.csv
        csv_path = args.output_dir / "graph_summary.csv"
        edges = await db.get_all_edges()
        # Build DOI -> title lookup
        title_map = {w.doi: w.title for w in all_works}
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["parent_doi", "child_doi", "parent_title", "child_title"])
            for parent, child in edges:
                writer.writerow([
                    parent,
                    child,
                    title_map.get(parent, ""),
                    title_map.get(child, ""),
                ])
        print(f"Wrote {len(edges)} edges to {csv_path}")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
