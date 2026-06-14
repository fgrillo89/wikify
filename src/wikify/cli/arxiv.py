"""``wikify arxiv ...`` -- exhaustive arXiv category harvester.

Two resumable phases that stage PDFs for ``corpus build``::

    arxiv identify --category cs.LG [--category cs.AI] [--set <raw>] --out <dir>
    arxiv download --out <dir> [--concurrency 4] [--rate 4.0]
    arxiv status   --out <dir>

``identify`` walks the complete OAI-PMH set(s) and writes ``manifest.jsonl``
+ ``harvest_state.json``. ``download`` fetches each pending PDF concurrently.
Both resume from on-disk state after an interruption.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ._helpers import EXIT_VALIDATION, cli_error

app = typer.Typer(add_completion=False, help="Exhaustive arXiv category harvester.")


def _resolve_format(fmt: str) -> str:
    if fmt not in ("text", "json"):
        cli_error(
            EXIT_VALIDATION,
            error="bad_format",
            message=f"unknown --format {fmt!r}; expected text or json",
        )
    return fmt


def _emit(fmt: str, payload: dict, text_lines: list[str]) -> None:
    if fmt == "json":
        typer.echo(json.dumps({**payload, "ok": True}))
    else:
        for line in text_lines:
            typer.echo(line)


@app.command("scout")
def cmd_scout(
    query: str = typer.Argument(..., help="Search query, e.g. 'all:machine learning'."),
    max_results: int = typer.Option(200, "--max", help="Number of top hits to sample."),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Sample a query and show its primary-category distribution.

    Use this to discover which arXiv categories a topic occupies, then
    harvest those categories with 'arxiv identify'. Network: one Query-API
    request to export.arxiv.org.
    """
    fmt = _resolve_format(fmt)
    from ..sources import arxiv as arxiv_src

    if max_results <= 0:
        cli_error(EXIT_VALIDATION, error="bad_int", message="--max must be > 0")

    report = arxiv_src.scout(query, max_results=max_results)
    payload = {
        "query": report.query,
        "total_results": report.total_results,
        "sampled": report.sampled,
        "primary_histogram": report.primary_histogram,
    }
    lines = [
        f"query matches ~{report.total_results} papers; sampled top {report.sampled}",
        "primary category distribution:",
    ]
    lines += [
        f"  {row['count']:>5}  {row['category']:<16} (set: {row['setspec']})"
        for row in report.primary_histogram
    ]
    mappable = [r for r in report.primary_histogram if r["setspec"]][:6]
    if mappable:
        cats = " ".join(f"--category {r['category']}" for r in mappable)
        lines.append(f"suggested: wikify arxiv identify {cats} --out <dir>")
    _emit(fmt, payload, lines)


@app.command("identify")
def cmd_identify(
    out: Path = typer.Option(..., "--out", help="Staging directory for manifest + PDFs."),
    category: list[str] = typer.Option(
        None, "--category", help="arXiv category, e.g. cs.LG (repeatable)."
    ),
    set_spec: list[str] = typer.Option(
        None, "--set", help="Raw OAI-PMH setSpec, e.g. physics:cond-mat:mtrl-sci (repeatable)."
    ),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Harvest the complete OAI-PMH record set for the given categories.

    Network: serial OAI-PMH requests to oaipmh.arxiv.org (1 req / 3 s).
    Resumable: re-running continues from harvest_state.json.
    """
    fmt = _resolve_format(fmt)
    from ..sources import arxiv as arxiv_src

    try:
        sets: list[str] = [arxiv_src.setspec_for_category(c) for c in (category or [])]
    except arxiv_src.UnknownArxivCategoryError as exc:
        cli_error(EXIT_VALIDATION, error="unknown_category", message=str(exc))
    sets += list(set_spec or [])
    if not sets:
        cli_error(
            EXIT_VALIDATION,
            error="no_sets",
            message="provide at least one --category or --set",
        )

    try:
        report = arxiv_src.harvest(sets, out)
    except arxiv_src.HarvestStateMismatchError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="state_mismatch",
            message=(
                f"{arxiv_src.state_path(out)} was created for different categories; "
                "use a new --out, or pass the same --category/--set to resume"
            ),
            stored=exc.stored_sets,
            requested=exc.requested_sets,
        )
    payload = {
        "harvested": report.harvested,
        "complete_list_size": report.complete_list_size,
        "resumed": report.resumed,
        "already_done": report.already_done,
        "manifest": str(arxiv_src.manifest_path(out)),
        "out": str(out),
    }
    state = "already complete" if report.already_done else (
        "resumed" if report.resumed else "fresh"
    )
    _emit(fmt, payload, [
        f"harvested {report.harvested} records ({state})"
        + (f" of ~{report.complete_list_size}" if report.complete_list_size else ""),
        f"manifest: {payload['manifest']}",
    ])


@app.command("download")
def cmd_download(
    out: Path = typer.Option(..., "--out", help="Staging directory holding manifest.jsonl."),
    concurrency: int = typer.Option(
        None, "--concurrency", help="Max simultaneous PDF downloads (default: 4)."
    ),
    rate: float = typer.Option(
        None, "--rate", help="Max PDF requests per second (default: 4)."
    ),
    allow_partial: bool = typer.Option(
        False, "--allow-partial", help="Exit 0 even if some PDFs fail."
    ),
    allow_incomplete_harvest: bool = typer.Option(
        False, "--allow-incomplete-harvest",
        help="Download even if 'identify' has not finished harvesting.",
    ),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Download every pending PDF in the manifest (resumable).

    Network: concurrent GETs to export.arxiv.org/pdf, capped by
    --concurrency and --rate (default ~4 req/s, arXiv's PDF-friendly rate).
    Transient 429/503 responses back off and retry. Skips PDFs already on
    disk; re-run to resume after interruption. Refuses to run until the
    harvest is complete (pass --allow-incomplete-harvest to override), and
    exits non-zero if any PDF fails (pass --allow-partial to override). The
    failed list is always in the output.
    """
    fmt = _resolve_format(fmt)
    from ..sources import arxiv as arxiv_src

    if concurrency is not None and concurrency <= 0:
        cli_error(EXIT_VALIDATION, error="bad_int", message="--concurrency must be > 0")
    if rate is not None and rate <= 0:
        cli_error(EXIT_VALIDATION, error="bad_rate", message="--rate must be > 0")
    if not arxiv_src.manifest_path(out).exists():
        cli_error(
            EXIT_VALIDATION,
            error="no_manifest",
            message=f"no manifest at {arxiv_src.manifest_path(out)}; run 'arxiv identify' first",
        )

    state = arxiv_src.read_state(out)
    harvest_done = bool(state and state.get("done"))
    if not harvest_done and not allow_incomplete_harvest:
        cli_error(
            EXIT_VALIDATION,
            error="harvest_incomplete",
            message=(
                "harvest is not complete; finish 'arxiv identify' first, or pass "
                "--allow-incomplete-harvest to download the partial manifest"
            ),
            harvest_done=harvest_done,
        )

    kwargs = {}
    if concurrency is not None:
        kwargs["concurrency"] = concurrency
    if rate is not None:
        kwargs["rate"] = rate
    report = arxiv_src.download_all(out, **kwargs)
    payload = {
        "downloaded": report.downloaded,
        "skipped": report.skipped,
        "failed": report.failed,
        "harvest_done": harvest_done,
        "out": str(out),
    }
    if report.failed and not allow_partial:
        cli_error(
            EXIT_VALIDATION,
            error="download_incomplete",
            message=(
                f"{len(report.failed)} PDF(s) failed; re-run to resume, or pass "
                "--allow-partial to proceed"
            ),
            **payload,
        )
    _emit(fmt, payload, [
        f"downloaded {report.downloaded}, skipped {report.skipped}, "
        f"failed {len(report.failed)}",
        f"out: {out}",
    ])


@app.command("status")
def cmd_status(
    out: Path = typer.Option(..., "--out", help="Staging directory holding manifest.jsonl."),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Report manifest progress (done / pending / failed) for the staging dir."""
    fmt = _resolve_format(fmt)
    from ..sources import arxiv as arxiv_src

    summary = arxiv_src.status_summary(out)
    _emit(fmt, summary, [
        f"total {summary['total']}: done {summary['done']}, "
        f"pending {summary['pending']}, failed {summary['failed']}",
        f"harvest_done: {summary['harvest_done']}"
        + (f" (~{summary['complete_list_size']} in set)"
           if summary["complete_list_size"] else ""),
    ])
