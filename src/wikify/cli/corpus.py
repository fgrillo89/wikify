"""``wikify corpus ...`` — corpus build + read-only query surface.

Commands::

    corpus build <source> --out <corpus> [--mode additive|sync] [--parser ...]
    corpus refresh <corpus>
    corpus check [<corpus>]
    corpus list [docs|chunks|files] [--corpus <c>] [--doc <d>]
    corpus find "<query>" [--top-k <n>] [--text]
    corpus find --seed [--max <n>]
    corpus show <handle> [--detail|--full]

``--corpus`` overrides; otherwise required for read commands. ``build``
takes the source dir as a positional and writes to ``--out``.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..corpus import queries
from ..corpus.handles import (
    AmbiguousHandleError,
    HandleNotFoundError,
    format_handle,
)
from ..corpus.session import CorpusSearchSession
from ._format import FormatError, format_row, resolve_format
from ._helpers import EXIT_VALIDATION, cli_error

# ``wikify.ingest.pipeline`` (~250ms) and ``wikify.bundle.run.events``
# (~300ms) are deferred to first use — neither is needed for the
# read-only `find`/`show`/`traverse` commands that dominate agent
# usage. ``ingest_corpus`` / ``refresh_corpus`` are imported inside
# `cmd_build` / `cmd_refresh`; ``Event`` / ``append_event`` /
# ``load_state`` are imported inside `_emit_chunk_reads` (which
# returns early when there is no bundle context).


def _resolve_format_or_error(fmt: str) -> str:
    """Wrap :func:`resolve_format` so unknown values surface as a clean envelope."""
    try:
        return resolve_format(fmt)
    except FormatError as exc:
        cli_error(EXIT_VALIDATION, error="bad_format", message=str(exc))


def _resolve_simple_format(fmt: str, *, allowed: tuple[str, ...] = ("text", "json")) -> str:
    """Validate the small text|json picker used by ``schema``/``check``/``list``."""
    if fmt not in allowed:
        cli_error(
            EXIT_VALIDATION,
            error="bad_format",
            message=(
                f"unknown --format {fmt!r}; expected one of {', '.join(allowed)}"
            ),
        )
    return fmt


def _validate_positive_int(name: str, value: int) -> None:
    """Reject ``--top-k 0``, ``--max 0`` and negative values with a clean envelope."""
    if value <= 0:
        cli_error(
            EXIT_VALIDATION,
            error="bad_int",
            message=f"--{name} must be > 0; got {value}",
        )

app = typer.Typer(add_completion=False, help="Corpus build + read-only queries.")


def _resolve_cwd_bundle() -> Bundle | None:
    """Return the bundle rooted at cwd, or None if cwd is not a bundle.

    ``corpus find`` / ``corpus show`` reveal chunks to the agent; when
    they run inside a bundle dir, each surfaced chunk is recorded as a
    ``chunk_read`` event so M5 (eval hit-rate) has a producer.
    """
    try:
        return Bundle.open(Path.cwd())
    except FileNotFoundError:
        return None


def _emit_chunk_reads(
    bundle: Bundle | None,
    chunk_ids: Iterable[str],
    *,
    via: str,
    doc_id: str | None = None,
) -> None:
    """Append one ``chunk_read`` event per id when a bundle context exists."""
    if bundle is None:
        return
    # Lazy: ~300ms of jsonschema/etc. only paid when inside a bundle.
    from ..bundle.run.events import Event, append_event
    from ..bundle.run.state import load_state

    try:
        run_id = load_state(bundle).run_id
    except Exception:
        return
    for cid in chunk_ids:
        if not cid:
            continue
        append_event(
            bundle,
            Event(
                run_id=run_id,
                type="chunk_read",
                actor="cli",
                chunk_id=cid,
                doc_id=doc_id,
                data={"via": via},
            ),
        )


def _looks_like_corpus(path: Path) -> bool:
    """Heuristic: a directory with ``manifest.json`` and ``docs/`` is a corpus."""
    return (
        path.is_dir()
        and (path / "manifest.json").is_file()
        and (path / "docs").is_dir()
    )


def _autodetect_corpus(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default cwd) looking for a corpus root."""
    cur = (start or Path.cwd()).resolve()
    for cand in (cur, *cur.parents):
        if _looks_like_corpus(cand):
            return cand
    return None


def _resolve_corpus(corpus_flag: Path | None) -> Corpus:
    """Resolve a corpus path: explicit flag > ``WIKIFY_CORPUS`` env > cwd walk-up."""
    import os

    if corpus_flag is not None:
        if not corpus_flag.is_dir():
            cli_error(
                EXIT_VALIDATION,
                error="not_a_directory",
                message=f"corpus path is not a directory: {corpus_flag}",
            )
        return Corpus(root=corpus_flag)

    env_path = os.environ.get("WIKIFY_CORPUS")
    if env_path:
        p = Path(env_path)
        if p.is_dir():
            return Corpus(root=p)
        cli_error(
            EXIT_VALIDATION,
            error="bad_wikify_corpus_env",
            message=f"WIKIFY_CORPUS={env_path!r} is not a directory",
        )

    detected = _autodetect_corpus()
    if detected is not None:
        return Corpus(root=detected)

    cli_error(
        EXIT_VALIDATION,
        error="corpus_required",
        message=(
            "no corpus resolved. Pass --corpus <path>, set WIKIFY_CORPUS, "
            "or run from inside a corpus directory (one containing "
            "manifest.json and docs/)."
        ),
    )


# Back-compat alias retained for callers that already imported it.
def _open_corpus(corpus_flag: Path | None) -> Corpus:
    return _resolve_corpus(corpus_flag)


# ------------------------------------------------------------ build/refresh


@app.command("build")
def cmd_build(
    source: Path = typer.Argument(..., help="Source directory of documents."),
    out: Path = typer.Option(..., "--out", help="Output corpus directory."),
    mode: str = typer.Option("additive", "--mode", help="additive | sync"),
    parser: str = typer.Option(
        "default",
        "--parser",
        help="Parser backend: default|lite|marker|docling.",
    ),
    workers: int = typer.Option(0, "--workers"),
    no_refresh: bool = typer.Option(False, "--no-refresh"),
) -> None:
    """Parse, chunk, embed, and graph an input directory."""
    from ..ingest.pipeline import ingest_corpus

    paths = ingest_corpus(
        source,
        out,
        max_workers=None if workers == 0 else workers,
        mode=mode,
        parser_backend=parser,
        refresh=not no_refresh,
    )
    typer.echo(f"corpus written to {paths.root}")


@app.command("refresh")
def cmd_refresh(
    corpus_dir: Path = typer.Argument(...),
) -> None:
    """Rebuild derived corpus artifacts (embeddings, graph, topics, ...)."""
    from ..ingest.pipeline import refresh_corpus

    paths = Corpus(root=corpus_dir)
    refresh_corpus(paths)
    typer.echo(f"refresh complete: {paths.root}")


@app.command("check")
def cmd_check(
    corpus_dir: Path | None = typer.Argument(
        None, help="Corpus directory. Optional; falls back to WIKIFY_CORPUS or cwd."
    ),
    fmt: str = typer.Option("text", "--format"),
    full: bool = typer.Option(
        False,
        "--full",
        help=(
            "Also compute citation-marker indexing coverage "
            "(requires loading knowledge_graph.json — slower)."
        ),
    ),
) -> None:
    """Report corpus health: doc/chunk counts, derived artifacts, field.

    The default form stays under ~2s by skipping the knowledge-graph
    load. Pass ``--full`` to also report ``cite_index`` coverage (% of
    in-corpus sources whose ``ord_refs`` is populated — relevant for
    ``traverse <chunk> --to cited-in-corpus``).
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt = _resolve_simple_format(fmt)
    summary = queries.check_corpus(corpus, full=full)
    if fmt == "json":
        typer.echo(json.dumps(summary))
        return
    typer.echo(f"root:        {summary['root']}")
    typer.echo(f"docs:        {summary['n_docs']}")
    typer.echo(f"chunks:      {summary['n_chunks']}")
    typer.echo(f"vectors:     {summary['has_vectors']}")
    typer.echo(f"graph:       {summary['has_knowledge_graph']}")
    typer.echo(f"manifest:    {summary['has_manifest']}")
    if summary.get("field"):
        typer.echo(f"field:       {summary['field']}")
    if "ord_refs_coverage_pct" in summary:
        cov = summary["ord_refs_coverage_pct"]
        with_ord = summary.get("sources_with_ord_refs", 0)
        typer.echo(
            f"cite_index:  {with_ord}/{summary['n_docs']} docs "
            f"({cov}% coverage for `cited-in-corpus`)"
        )


# --------------------------------------------------------------- list


list_app = typer.Typer(add_completion=False, help="List corpus handles.")
app.add_typer(list_app, name="list")


@list_app.command("docs")
def cmd_list_docs(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    fmt: str = typer.Option("text", "--format"),
    long: bool = typer.Option(
        False,
        "--long",
        help="Emit full internal ids instead of short doc handles.",
    ),
) -> None:
    """Print every doc handle in the corpus.

    Defaults to short handles (``doc:<short>``) — directly usable as
    arguments to ``corpus show`` / ``corpus traverse``. ``--long``
    restores the legacy behaviour of emitting bare full internal ids.
    """
    corpus = _open_corpus(corpus_dir)
    fmt = _resolve_simple_format(fmt)
    ids = queries.list_doc_ids(corpus)
    if fmt == "json":
        if long:
            typer.echo(json.dumps({"ok": True, "items": ids}))
        else:
            typer.echo(
                json.dumps({"ok": True, "items": [format_handle("doc", d) for d in ids]})
            )
        return
    for did in ids:
        typer.echo(did if long else format_handle("doc", did))


@list_app.command("chunks")
def cmd_list_chunks(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    doc_id: str = typer.Option(..., "--doc"),
    fmt: str = typer.Option("text", "--format"),
    long: bool = typer.Option(
        False,
        "--long",
        help="Emit full internal ids instead of short chunk handles.",
    ),
) -> None:
    """Print chunk handles for one document.

    ``--doc`` accepts the short or full doc handle. Output defaults to
    short ``chunk:<short>`` handles; ``--long`` restores bare full ids.
    """
    corpus = _open_corpus(corpus_dir)
    fmt = _resolve_simple_format(fmt)
    try:
        full_doc = queries.resolve_doc_id(corpus, doc_id)
    except AmbiguousHandleError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="ambiguous_handle",
            message=str(exc),
            matches=exc.matches,
        )
    except HandleNotFoundError:
        cli_error(EXIT_VALIDATION, error="doc_not_found", id=doc_id)
    chunks = queries.list_chunks_for_doc(corpus, full_doc)
    ids = [c.id for c in chunks]
    if fmt == "json":
        if long:
            typer.echo(json.dumps({"ok": True, "items": ids}))
        else:
            typer.echo(
                json.dumps(
                    {"ok": True, "items": [format_handle("chunk", c) for c in ids]}
                )
            )
        return
    for cid in ids:
        typer.echo(cid if long else format_handle("chunk", cid))


@list_app.command("files")
def cmd_list_files(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print every file under the corpus root, relative."""
    corpus = _open_corpus(corpus_dir)
    fmt = _resolve_simple_format(fmt)
    files = queries.list_files(corpus)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": files}))
        return
    for f in files:
        typer.echo(f)


# ---------------------------------------------------------------- find


@app.command("find")
def cmd_find(
    query: str = typer.Argument("", help="Query string. Empty for --seed mode."),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    top_k: int = typer.Option(8, "--top-k"),
    seed: bool = typer.Option(False, "--seed", help="Greedy seed selection."),
    max_seeds: int = typer.Option(
        20, "--max", help="Max seed docs returned by --seed mode."
    ),
    pagerank_weight: float = typer.Option(
        0.7,
        "--pagerank-weight",
        help=(
            "Trade-off between PageRank prior and submodular coverage gain "
            "(0.0=coverage only, 1.0=pagerank only). Used only with --seed."
        ),
    ),
    text: bool = typer.Option(False, "--text", help="Literal substring grep."),
    by: str = typer.Option(
        "chunk",
        "--by",
        help="Aggregate by chunk (default) or paper.",
    ),
    rank: str = typer.Option(
        "semantic",
        "--rank",
        help="Ranking metric: semantic | citation_count | pagerank.",
    ),
    fmt: str = typer.Option(
        "auto",
        "--format",
        help=(
            "Output format: auto (compact for TTY / quiet for pipe), "
            "quiet (handles only), compact (tab-separated columns), json."
        ),
    ),
    explain: bool = typer.Option(
        False,
        "--explain",
        help="Print the resolved fluent-chain pseudocode and exit.",
    ),
) -> None:
    """Search the corpus.

    Modes:

    - ``--seed`` returns the greedy submodular seed doc list.
    - ``--text`` does a literal substring grep over chunk text.
    - Otherwise semantic search; ``--by paper`` aggregates to documents
      and ``--rank citation_count|pagerank`` reorders the result.

    Compact output columns:

    - chunks (``--by chunk``):  ``score \\t cites=N \\t chunk-handle \\t doc-handle``
      where ``score`` is the semantic cosine (0..1, higher=closer; ``.``
      under ``--text``) and ``cites`` is the parent doc's in-corpus
      citation count.
    - papers (``--by paper``):  ``score \\t cites=N \\t n=K \\t doc-handle \\t title``
      where ``n`` is how many chunks of that paper matched the query.
    - seeds / metric-only ranking: ``cites=N \\t pr=X.XXXX \\t doc-handle \\t title``
      where ``pr`` is the corpus PageRank.
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt_resolved = _resolve_format_or_error(fmt)
    _validate_positive_int("top-k", top_k)
    _validate_positive_int("max", max_seeds)
    if explain:
        _emit_find_explain(
            corpus,
            query=query,
            seed=seed,
            text=text,
            by=by,
            rank=rank,
            top_k=top_k,
            max_seeds=max_seeds,
            pagerank_weight=pagerank_weight,
        )
        return
    if seed:
        ids = queries.find_seeds(
            corpus, max_seeds=max_seeds, pagerank_weight=pagerank_weight
        )
        metrics = queries.doc_metrics(corpus, ids)
        _emit_doc_rows(
            [
                {
                    "doc_id": did,
                    "title": _doc_title(corpus, did),
                    "citation_count": metrics.get(did, {}).get("citation_count", 0),
                    "pagerank": metrics.get(did, {}).get("pagerank", 0.0),
                    "score": None,
                }
                for did in ids
            ],
            fmt=fmt_resolved,
        )
        return

    if rank not in {"semantic", "citation_count", "pagerank", "h_index", "n_papers"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_rank",
            message=(
                f"unknown --rank {rank!r}; expected "
                f"semantic | citation_count | pagerank | h_index | n_papers"
            ),
        )
    if by not in {"chunk", "paper", "author"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_by",
            message=f"unknown --by {by!r}; expected chunk | paper | author",
        )
    # --by chunk has no aggregation step, so a graph-metric rank can't be
    # applied. Reject loudly rather than silently honour --rank semantic.
    if by == "chunk" and rank != "semantic":
        cli_error(
            EXIT_VALIDATION,
            error="bad_rank_by_combo",
            message=(
                f"--rank {rank!r} only applies when chunks are aggregated "
                f"to a parent doc/author. Use --by paper or --by author, "
                f"or drop --rank to keep semantic order."
            ),
        )
    # --by author only meaningfully reranks by author-typed metrics.
    if by == "author" and rank == "pagerank":
        cli_error(
            EXIT_VALIDATION,
            error="bad_rank_by_combo",
            message=(
                "--rank pagerank does not apply to authors; use "
                "h_index | citation_count | n_papers."
            ),
        )
    # --by paper only meaningfully reranks by source-typed metrics.
    if by == "paper" and rank in {"h_index", "n_papers"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_rank_by_combo",
            message=(
                f"--rank {rank!r} does not apply to papers; use "
                f"citation_count | pagerank, or switch --by author."
            ),
        )

    # Pure metric ranking — ignore query, return top docs by graph metric.
    if rank in {"citation_count", "pagerank"} and not query and by != "author":
        rows = queries.rank_docs(corpus, by=rank, top_k=top_k)
        _emit_doc_rows(
            [{**r, "score": None} for r in rows],
            fmt=fmt_resolved,
        )
        return

    # Author-only modes: top authors by metric, or authors by query.
    if by == "author":
        if not query:
            metric = rank if rank in {"h_index", "citation_count", "n_papers"} else "h_index"
            rows = queries.rank_authors(corpus, by=metric, top_k=top_k)
            _emit_author_rows(rows, fmt=fmt_resolved, score_key=None)
            return
        rows = queries.search_authors(corpus, query, top_k=top_k)
        _emit_author_rows(rows, fmt=fmt_resolved, score_key="best_score")
        return

    # When re-ranking by graph metric, semantic top_k is too narrow:
    # the most-cited paper that mentions the query may sit lower in the
    # semantic ranking. Widen the candidate pool then truncate after rank.
    paper_pool = top_k if rank == "semantic" else max(top_k * 5, 30)

    if text:
        if by == "paper":
            papers = queries.search_papers(
                corpus, query, top_k=paper_pool, text=True
            )
            _emit_paper_rows(corpus, papers, fmt=fmt_resolved, rank=rank, top_k=top_k)
            return
        hits = queries.search_text(corpus, query, top_k=top_k)
        _emit_chunk_reads(
            _resolve_cwd_bundle(),
            (h.get("id", "") for h in hits),
            via="corpus_find_text",
        )
        _emit_chunk_rows(corpus, hits, fmt=fmt_resolved, score_key=None)
        return

    if not query:
        cli_error(
            EXIT_VALIDATION,
            error="missing_query",
            message="`corpus find` requires a query, --seed, or --text mode",
        )

    if by == "paper":
        papers = queries.search_papers(corpus, query, top_k=paper_pool)
        _emit_paper_rows(corpus, papers, fmt=fmt_resolved, rank=rank, top_k=top_k)
        return

    hits = queries.search_chunks(corpus, query, top_k=top_k)
    _emit_chunk_reads(
        _resolve_cwd_bundle(),
        (h.get("id", "") for h in hits),
        via="corpus_find_semantic",
    )
    _emit_chunk_rows(corpus, hits, fmt=fmt_resolved, score_key="score")


def _doc_title(corpus: Corpus, doc_id: str) -> str:
    doc = queries.get_doc(corpus, doc_id)
    return (doc.title or "") if doc is not None else ""


def _emit_chunk_rows(
    corpus: Corpus,
    hits: list[dict],
    *,
    fmt: str,
    score_key: str | None,
) -> None:
    """Print chunk-level search results in the chosen format."""
    doc_ids_in_order: list[str] = []
    seen: set[str] = set()
    for h in hits:
        did = str(h.get("doc_id") or h.get("source_id") or "")
        if did and did not in seen:
            seen.add(did)
            doc_ids_in_order.append(did)
    metrics = queries.doc_metrics(corpus, doc_ids_in_order)
    if fmt == "json":
        items = []
        for h in hits:
            did = str(h.get("doc_id") or h.get("source_id") or "")
            items.append(
                {
                    **h,
                    "chunk_handle": format_handle("chunk", str(h.get("id", ""))),
                    "doc_handle": format_handle("doc", did) if did else "",
                    "citation_count": metrics.get(did, {}).get("citation_count", 0),
                }
            )
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for h in hits:
            typer.echo(format_handle("chunk", str(h.get("id", ""))))
        return
    for h in hits:
        cid = str(h.get("id", ""))
        did = str(h.get("doc_id") or h.get("source_id") or "")
        cites = metrics.get(did, {}).get("citation_count", 0)
        score_val = h.get(score_key) if score_key else None
        score_col = f"{float(score_val):.3f}" if score_val is not None else "."
        typer.echo(
            format_row([
                score_col,
                f"cites={cites}",
                format_handle("chunk", cid),
                format_handle("doc", did) if did else "",
            ])
        )


def _emit_paper_rows(
    corpus: Corpus,
    papers: list[dict],
    *,
    fmt: str,
    rank: str,
    top_k: int,
) -> None:
    """Print paper-aggregated results, optionally re-ranked by metric."""
    doc_ids = [str(p.get("doc_id", "")) for p in papers]
    metrics = queries.doc_metrics(corpus, doc_ids)
    enriched = []
    for p in papers:
        did = str(p.get("doc_id", ""))
        m = metrics.get(did, {})
        enriched.append(
            {
                **p,
                "citation_count": m.get("citation_count", 0),
                "pagerank": m.get("pagerank", 0.0),
            }
        )
    if rank == "citation_count":
        enriched.sort(
            key=lambda r: (
                -int(r.get("citation_count", 0)),
                -float(r.get("best_score", 0.0)),
                str(r.get("doc_id", "")),
            )
        )
    elif rank == "pagerank":
        enriched.sort(
            key=lambda r: (
                -float(r.get("pagerank", 0.0)),
                -float(r.get("best_score", 0.0)),
                str(r.get("doc_id", "")),
            )
        )
    enriched = enriched[:top_k]
    if fmt == "json":
        items = [
            {
                **r,
                "doc_handle": format_handle("doc", str(r.get("doc_id", ""))),
                "best_chunk_handle": format_handle("chunk", str(r.get("best_chunk_id", ""))),
            }
            for r in enriched
        ]
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for r in enriched:
            typer.echo(format_handle("doc", str(r.get("doc_id", ""))))
        return
    for r in enriched:
        score = float(r.get("best_score", 0.0))
        cites = int(r.get("citation_count", 0))
        n_ch = int(r.get("n_chunks", 0))
        title = str(r.get("title", "") or "")
        typer.echo(
            format_row([
                f"{score:.3f}",
                f"cites={cites}",
                f"n={n_ch}",
                format_handle("doc", str(r.get("doc_id", ""))),
                title,
            ])
        )


def _emit_author_rows(rows: list[dict], *, fmt: str, score_key: str | None) -> None:
    """Print author rows in the chosen format.

    With ``score_key`` set (semantic-search-by-author mode), the
    ``n_papers`` value carries the per-query match count, not the
    author's total. To keep the column self-describing we render it as
    ``n_match=`` in compact mode; the JSON shape stays as ``n_papers``
    (with an extra ``n_match`` mirror field added in search mode).
    """
    in_search_mode = score_key is not None
    if fmt == "json":
        items = []
        for r in rows:
            item = {**r, "handle": format_handle("author", str(r.get("key", "")))}
            if in_search_mode:
                item["n_match"] = int(r.get("n_papers", 0))
            items.append(item)
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for r in rows:
            typer.echo(format_handle("author", str(r.get("key", ""))))
        return
    for r in rows:
        h = int(r.get("h_index", 0))
        cites = int(r.get("citation_count", 0))
        n_count = int(r.get("n_papers", 0))
        score = r.get(score_key) if score_key else None
        score_col = f"{float(score):.3f}" if score is not None else "."
        n_col = f"n_match={n_count}" if in_search_mode else f"n_papers={n_count}"
        cols = [
            score_col,
            f"h={h}",
            f"cites={cites}",
            n_col,
            format_handle("author", str(r.get("key", ""))),
            str(r.get("name", "") or ""),
        ]
        if not in_search_mode:
            cols = cols[1:]  # drop the score placeholder for metric-only lists
        typer.echo(format_row(cols))


def _emit_doc_rows(rows: list[dict], *, fmt: str) -> None:
    """Print doc-only rows (seed mode and metric-only ranking)."""
    if fmt == "json":
        items = [
            {**r, "doc_handle": format_handle("doc", str(r.get("doc_id", "")))}
            for r in rows
        ]
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for r in rows:
            typer.echo(format_handle("doc", str(r.get("doc_id", ""))))
        return
    for r in rows:
        cites = int(r.get("citation_count", 0))
        pr = float(r.get("pagerank", 0.0))
        title = str(r.get("title", "") or "")
        typer.echo(
            format_row([
                f"cites={cites}",
                f"pr={pr:.4f}",
                format_handle("doc", str(r.get("doc_id", ""))),
                title,
            ])
        )


# ---------------------------------------------------------------- explain helpers


def _emit_find_explain(
    corpus: Corpus,
    *,
    query: str,
    seed: bool,
    text: bool,
    by: str,
    rank: str,
    top_k: int,
    max_seeds: int,
    pagerank_weight: float,
) -> None:
    """Print a fluent-chain-style description of what `find` would do."""
    typer.echo(f"corpus: {corpus.root}")
    if seed:
        chain = (
            f"greedy_seed_select(max={max_seeds}, pagerank_weight={pagerank_weight}) "
            f"-> top docs"
        )
    elif text:
        chain = (
            f"all_chunks().filter(text contains {query!r}).take({top_k})"
            + (f" -> group_by_doc().take({top_k})" if by == "paper" else "")
        )
    elif rank in {"citation_count", "pagerank"} and not query:
        chain = f"sources().top({top_k}, by={rank!r})"
    else:
        pool = top_k if rank == "semantic" else max(top_k * 5, 30)
        if by == "paper":
            chain = (
                f"chunks().search({query!r}, top_k={pool}) "
                f"-> group_by_doc()"
                + (f" -> resort_by({rank!r})" if rank != "semantic" else "")
                + f" -> take({top_k})"
            )
        else:
            chain = f"chunks().search({query!r}, top_k={top_k})"
    typer.echo(f"chain:  {chain}")


def _emit_traverse_explain(
    corpus: Corpus,
    *,
    handle: str,
    to: str,
    rank: str,
    top_k: int,
) -> None:
    """Print a fluent-chain description of a traverse call."""
    typer.echo(f"corpus: {corpus.root}")
    suffix = ""
    if rank:
        suffix += f" -> top({top_k or 'unbounded'}, by={rank!r})"
    elif top_k:
        suffix += f" -> take({top_k})"
    kind, _, ident = handle.partition(":")
    entry = {"doc": "source", "chunk": "chunk", "author": "author"}.get(kind, kind)
    typer.echo(f"chain:  kg.{entry}({ident!r}).{to.replace('-', '_')}(){suffix}")


# ---------------------------------------------------------------- show


@app.command("show")
def cmd_show(
    handle: str = typer.Argument(..., help="doc:<id> or chunk:<id> (short or full)"),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    full: bool = typer.Option(False, "--full"),
    long: bool = typer.Option(
        False,
        "--long",
        help="Print the full internal id alongside the short handle.",
    ),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Dereference one handle and print its content.

    Handles accept short hash suffixes (``doc:5f92b0389ccd``) as well as
    full ids; ambiguous suffixes are reported with the candidate list.

    The ``id:`` line emits the canonical short handle (``doc:<short>``)
    so an agent can copy it straight back into ``corpus show`` /
    ``corpus traverse``. Pass ``--long`` to additionally print the full
    internal id used by ingestion.
    """
    corpus = _open_corpus(corpus_dir)
    fmt = _resolve_simple_format(fmt)
    try:
        kind, ident = queries.parse_handle(handle)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_handle", message=str(exc))

    if kind == "doc":
        try:
            doc = queries.get_doc(corpus, ident)
        except AmbiguousHandleError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="ambiguous_handle",
                message=str(exc),
                matches=exc.matches,
            )
        if doc is None:
            cli_error(EXIT_VALIDATION, error="doc_not_found", id=ident)
        meta = doc.metadata or {}
        if fmt == "json":
            payload = {
                "ok": True,
                "id": format_handle("doc", doc.id),
                "title": doc.title,
                "kind": doc.kind,
                "metadata": meta,
                "n_chunks": doc.n_chunks,
            }
            if long:
                payload["full_id"] = doc.id
            typer.echo(json.dumps(payload))
            return
        typer.echo(f"id:       {format_handle('doc', doc.id)}")
        if long:
            typer.echo(f"full_id:  {doc.id}")
        typer.echo(f"title:    {doc.title or ''}")
        typer.echo(f"kind:     {doc.kind}")
        typer.echo(f"chunks:   {doc.n_chunks}")
        if "year" in meta:
            typer.echo(f"year:     {meta['year']}")
        if "authors" in meta:
            typer.echo(f"authors:  {len(meta['authors'] or [])}")
        return

    if kind == "chunk":
        try:
            chunk = queries.get_chunk(corpus, ident)
        except AmbiguousHandleError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="ambiguous_handle",
                message=str(exc),
                matches=exc.matches,
            )
        if chunk is None:
            cli_error(EXIT_VALIDATION, error="chunk_not_found", id=ident)
        _emit_chunk_reads(
            _resolve_cwd_bundle(),
            [chunk.id],
            via="corpus_show_chunk",
            doc_id=chunk.doc_id,
        )
        if fmt == "json":
            payload = {
                "ok": True,
                "id": format_handle("chunk", chunk.id),
                "doc_id": format_handle("doc", chunk.doc_id),
                "section_path": list(chunk.section_path or []),
                "text": chunk.text if full else chunk.text[:500],
            }
            if long:
                payload["full_id"] = chunk.id
                payload["full_doc_id"] = chunk.doc_id
            typer.echo(json.dumps(payload))
            return
        typer.echo(f"id:           {format_handle('chunk', chunk.id)}")
        typer.echo(f"doc:          {format_handle('doc', chunk.doc_id)}")
        if long:
            typer.echo(f"full_id:      {chunk.id}")
            typer.echo(f"full_doc_id:  {chunk.doc_id}")
        typer.echo(f"section_path: {chunk.section_path}")
        if full:
            typer.echo("---")
            typer.echo(chunk.text)
        else:
            typer.echo("---")
            typer.echo(chunk.text[:500])
        return

    if kind == "figure":
        try:
            fig = queries.get_figure(corpus, ident)
        except AmbiguousHandleError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="ambiguous_handle",
                message=str(exc),
                matches=exc.matches,
            )
        if fig is None:
            cli_error(EXIT_VALIDATION, error="figure_not_found", id=ident)
        if fmt == "json":
            payload = {
                "ok": True,
                **fig,
                "id": format_handle("figure", fig["id"]),
                "doc_id": format_handle("doc", fig["source_id"]),
                "near_chunk_ids": [
                    format_handle("chunk", cid) for cid in fig["near_chunk_ids"]
                ],
            }
            if long:
                payload["full_id"] = fig["id"]
                payload["full_doc_id"] = fig["source_id"]
            typer.echo(json.dumps(payload))
            return
        typer.echo(f"id:        {format_handle('figure', fig['id'])}")
        typer.echo(f"doc:       {format_handle('doc', fig['source_id'])}")
        if long:
            typer.echo(f"full_id:   {fig['id']}")
            typer.echo(f"full_doc_id: {fig['source_id']}")
        if fig.get("page") is not None:
            typer.echo(f"page:      {fig['page']}")
        typer.echo(f"path:      {fig['path']}")
        typer.echo(f"caption:   {fig['caption']}")
        if fig["near_chunk_ids"]:
            handles = [
                format_handle("chunk", cid) for cid in fig["near_chunk_ids"][:5]
            ]
            typer.echo(f"near:      {' '.join(handles)}")
        return

    if kind == "author":
        try:
            au = queries.get_author(corpus, ident)
        except AmbiguousHandleError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="ambiguous_handle",
                message=str(exc),
                matches=exc.matches,
            )
        if au is None:
            cli_error(EXIT_VALIDATION, error="author_not_found", id=ident)
        if fmt == "json":
            payload = {
                "ok": True,
                **au,
                "id": format_handle("author", au["key"]),
            }
            if long:
                payload["full_key"] = au["key"]
            typer.echo(json.dumps(payload))
            return
        typer.echo(f"id:        {format_handle('author', au['key'])}")
        if long:
            typer.echo(f"full_key:  {au['key']}")
        if au["name"]:
            typer.echo(f"name:      {au['name']}")
        typer.echo(f"h_index:   {au['h_index']}")
        typer.echo(f"cites:     {au['citation_count']}")
        typer.echo(f"n_papers:  {au['n_papers']}")
        if au["top_coauthors"]:
            typer.echo("coauthors:")
            for ca in au["top_coauthors"]:
                handle = format_handle("author", ca["key"])
                name = ca.get("name", "") or ""
                typer.echo(
                    f"  h={ca['h_index']:<3d} cites={ca['citation_count']:<4d} "
                    f"{handle}  {name}"
                )
        return

    if kind == "equation":
        try:
            eq = queries.get_equation(corpus, ident)
        except AmbiguousHandleError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="ambiguous_handle",
                message=str(exc),
                matches=exc.matches,
            )
        if eq is None:
            cli_error(EXIT_VALIDATION, error="equation_not_found", id=ident)
        if fmt == "json":
            payload = {
                "ok": True,
                **eq,
                "id": format_handle("equation", eq["id"]),
                "doc_id": format_handle("doc", eq["source_id"]),
            }
            if long:
                payload["full_id"] = eq["id"]
                payload["full_doc_id"] = eq["source_id"]
            typer.echo(json.dumps(payload))
            return
        typer.echo(f"id:        {format_handle('equation', eq['id'])}")
        typer.echo(f"doc:       {format_handle('doc', eq['source_id'])}")
        if long:
            typer.echo(f"full_id:   {eq['id']}")
            typer.echo(f"full_doc_id: {eq['source_id']}")
        typer.echo(f"kind:      {eq['kind']}")
        typer.echo(f"chemical:  {eq['is_chemical']}")
        if eq["label"]:
            typer.echo(f"label:     {eq['label']}")
        typer.echo("---")
        typer.echo(eq["latex"])
        return

    cli_error(
        EXIT_VALIDATION,
        error="bad_handle_kind",
        message=(
            f"unknown handle kind {kind!r}; use doc:<id>, chunk:<id>, "
            f"figure:<id>, or equation:<id>"
        ),
    )


# ---------------------------------------------------------------- schema


_CORPUS_SCHEMA: dict = {
    "node_types": {
        "source": "A document. Handle: doc:<id-or-short>.",
        "chunk": "A text chunk inside a doc. Handle: chunk:<id-or-short>.",
        "author": "A paper author. Handle: author:<lastname-initials key>.",
        "section": "A section of a doc.",
        "figure": "An image with caption. Handle: figure:<doc-short>/<stem>.",
        "equation": "A math or chemical equation. Handle: equation:<id>.",
    },
    "edge_kinds": [
        "CITES",            # source -> source
        "AUTHORED_BY",      # source -> author
        "COLLABORATED",     # author <-> author
        "CONTAINS_SECTION", # source -> section
        "CONTAINS_CHUNK",   # source -> chunk
        "CHUNK_IN_SECTION", # chunk -> section
        "CONTAINS_FIGURE",  # source -> figure
        "CONTAINS_EQUATION",# source -> equation
        "FIGURE_NEAR_CHUNK",# figure <-> chunk
        "EQUATION_IN_CHUNK",# equation -> chunk
    ],
    "traverse_relations": {
        "doc": ["cited-by", "references", "chunks", "figures", "equations", "authors"],
        "chunk": ["source", "cited-in-corpus", "figures", "equations"],
        "author": ["sources", "coauthors"],
    },
    "rank_metrics": {
        "source": ["citation_count", "pagerank"],
        "author": ["h_index", "citation_count", "n_papers"],
    },
    "find_modes": {
        "--by chunk":  "Rank chunks (default).",
        "--by paper":  "Aggregate chunk hits to papers.",
        "--by author": "Aggregate chunk hits to authors.",
        "--seed":      "Greedy submodular seed selection.",
        "--text":      "Literal substring grep over chunk text.",
    },
    "formats": ["auto", "quiet", "compact", "json"],
    "handle_resolution": (
        "Short forms: doc/chunk/equation accept the trailing 8-12 hex; "
        "figure accepts <doc-short>/<stem>. Author accepts case-insensitive "
        "unique prefix. Ambiguous matches return an error with candidates."
    ),
}


@app.command("schema")
def cmd_schema(
    fmt: str = typer.Option(
        "text", "--format", help="Output format: text | json."
    ),
) -> None:
    """Self-describe the corpus CLI surface: nodes, edges, relations, metrics.

    Run this once to learn the available verbs and relations without
    grepping source.
    """
    fmt = _resolve_simple_format(fmt)
    if fmt == "json":
        typer.echo(json.dumps(_CORPUS_SCHEMA, indent=2))
        return
    typer.echo("Node types:")
    for k, v in _CORPUS_SCHEMA["node_types"].items():
        typer.echo(f"  {k:10s}  {v}")
    typer.echo("")
    typer.echo("Edge kinds:")
    for kind in _CORPUS_SCHEMA["edge_kinds"]:
        typer.echo(f"  {kind}")
    typer.echo("")
    typer.echo("Traverse relations (corpus traverse <handle> --to <relation>):")
    for handle_kind, rels in _CORPUS_SCHEMA["traverse_relations"].items():
        typer.echo(f"  {handle_kind+':':<8s} {' | '.join(rels)}")
    typer.echo("")
    typer.echo("Rank metrics:")
    for over, metrics in _CORPUS_SCHEMA["rank_metrics"].items():
        typer.echo(f"  over {over+'s:':<8s} {' | '.join(metrics)}")
    typer.echo("")
    typer.echo("find modes:")
    for k, v in _CORPUS_SCHEMA["find_modes"].items():
        typer.echo(f"  {k:14s} {v}")
    typer.echo("")
    typer.echo(f"Formats: {' | '.join(_CORPUS_SCHEMA['formats'])}")
    typer.echo("")
    typer.echo(f"Handles: {_CORPUS_SCHEMA['handle_resolution']}")


# ---------------------------------------------------------------- traverse


_DOC_RELATIONS = {
    "cited-by", "references", "chunks", "figures", "equations", "authors",
}
_CHUNK_RELATIONS = {"source", "cited-in-corpus", "figures", "equations"}
_AUTHOR_RELATIONS = {"sources", "coauthors"}


@app.command("traverse")
def cmd_traverse(
    handle: str = typer.Argument(..., help="doc:<id> or chunk:<id> (short or full)"),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    to: str = typer.Option(
        ...,
        "--to",
        help=(
            "Relation to traverse. doc handles: cited-by | references | "
            "chunks | figures | equations. chunk handles: source | "
            "cited-in-corpus | figures | equations."
        ),
    ),
    rank: str = typer.Option(
        "",
        "--rank",
        help="Optional rank for source results: citation_count | pagerank.",
    ),
    top_k: int = typer.Option(0, "--top-k", help="Limit (0 = no limit)."),
    fmt: str = typer.Option(
        "auto",
        "--format",
        help=(
            "Output format: auto (compact for TTY / quiet for pipe), "
            "quiet (handles only), compact (tab-separated columns), json."
        ),
    ),
    explain: bool = typer.Option(
        False,
        "--explain",
        help="Print the resolved fluent-chain pseudocode and exit.",
    ),
) -> None:
    """Traverse one hop from a handle and print the resulting handles.

    The result is always handles (one per line in ``quiet`` mode) so it
    pipes directly into ``corpus show`` or another ``corpus traverse``
    call. ``--rank`` only applies when the relation produces sources.

    Compact output columns:

    - source rows:   ``cites=N \\t pr=X.XXXX \\t doc-handle \\t title``
      where ``cites`` is in-corpus citation count and ``pr`` is PageRank.
    - chunk rows:    ``chunk-handle \\t doc-handle``.
    - figure rows:   ``page=N \\t figure-handle \\t caption \\t path``
      The ``path`` is corpus-relative; agents may pass it directly to
      Read for visual ingestion or compose a markdown image link.
    - equation rows: ``kind \\t label \\t equation-handle \\t latex``
      where ``kind`` is ``math`` / ``chem`` / ``named``.
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt_resolved = _resolve_format_or_error(fmt)
    if top_k < 0:
        cli_error(
            EXIT_VALIDATION,
            error="bad_top_k",
            message=f"--top-k must be >= 0 (0 means unlimited); got {top_k}",
        )
    rank_resolved: str | None = rank or None
    top_k_resolved: int | None = top_k if top_k > 0 else None

    if explain:
        _emit_traverse_explain(
            corpus, handle=handle, to=to, rank=rank, top_k=top_k
        )
        return

    try:
        kind, ident = queries.parse_handle(handle)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_handle", message=str(exc))

    try:
        if kind == "doc":
            if to not in _DOC_RELATIONS:
                cli_error(
                    EXIT_VALIDATION,
                    error="bad_relation",
                    message=(
                        f"unknown doc relation {to!r}; expected "
                        f"{' | '.join(sorted(_DOC_RELATIONS))}"
                    ),
                )
            full_id = queries.resolve_doc_id(corpus, ident)
            rows = queries.traverse_doc(
                corpus,
                doc_id=full_id,
                relation=to,
                rank=rank_resolved,
                top_k=top_k_resolved,
            )
        elif kind == "author":
            if to not in _AUTHOR_RELATIONS:
                cli_error(
                    EXIT_VALIDATION,
                    error="bad_relation",
                    message=(
                        f"unknown author relation {to!r}; expected "
                        f"{' | '.join(sorted(_AUTHOR_RELATIONS))}"
                    ),
                )
            full_key = queries.resolve_author_key(corpus, ident)
            rows = queries.traverse_author(
                corpus,
                key=full_key,
                relation=to,
                rank=rank_resolved,
                top_k=top_k_resolved,
            )
        elif kind == "chunk":
            if to not in _CHUNK_RELATIONS:
                cli_error(
                    EXIT_VALIDATION,
                    error="bad_relation",
                    message=(
                        f"unknown chunk relation {to!r}; expected "
                        f"{' | '.join(sorted(_CHUNK_RELATIONS))}"
                    ),
                )
            full_id = queries.resolve_chunk_id(corpus, ident)
            rows = queries.traverse_chunk(
                corpus,
                chunk_id=full_id,
                relation=to,
                rank=rank_resolved,
                top_k=top_k_resolved,
            )
        else:
            cli_error(
                EXIT_VALIDATION,
                error="bad_handle_kind",
                message=f"unknown handle kind {kind!r}; use doc:<id> or chunk:<id>",
            )
    except AmbiguousHandleError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="ambiguous_handle",
            message=str(exc),
            matches=exc.matches,
        )
    except HandleNotFoundError as exc:
        cli_error(EXIT_VALIDATION, error="handle_not_found", message=str(exc))
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_relation", message=str(exc))

    _emit_traverse_rows(rows, fmt=fmt_resolved)


_HANDLE_KIND_BY_TYPE = {
    "chunk": "chunk",
    "figure": "figure",
    "equation": "equation",
    "author": "author",
}


def _emit_traverse_rows(rows: list[dict], *, fmt: str) -> None:
    """Print traverse output. Type per row determines handle kind."""
    if fmt == "json":
        items = []
        for r in rows:
            ntype = r.get("type", "source")
            handle_kind = _HANDLE_KIND_BY_TYPE.get(ntype, "doc")
            items.append({**r, "handle": format_handle(handle_kind, str(r.get("id", "")))})
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for r in rows:
            ntype = r.get("type", "source")
            handle_kind = _HANDLE_KIND_BY_TYPE.get(ntype, "doc")
            typer.echo(format_handle(handle_kind, str(r.get("id", ""))))
        return
    for r in rows:
        ntype = r.get("type", "source")
        if ntype == "chunk":
            cid = str(r.get("id", ""))
            did = str(r.get("doc_id", ""))
            typer.echo(
                format_row([
                    format_handle("chunk", cid),
                    format_handle("doc", did) if did else "",
                ])
            )
        elif ntype == "figure":
            page = r.get("page")
            page_col = f"page={page}" if page is not None else "page=?"
            caption = str(r.get("caption", "") or "").replace("\n", " ")[:120]
            path = str(r.get("path", "") or "")
            typer.echo(
                format_row([
                    page_col,
                    format_handle("figure", str(r.get("id", ""))),
                    caption,
                    path,
                ])
            )
        elif ntype == "equation":
            kind_col = (
                "chem" if r.get("is_chemical")
                else (str(r.get("kind", "") or "math"))
            )
            label = str(r.get("label", "") or "")
            latex = str(r.get("latex", "") or "").replace("\n", " ")[:120]
            typer.echo(
                format_row([
                    kind_col,
                    label,
                    format_handle("equation", str(r.get("id", ""))),
                    latex,
                ])
            )
        elif ntype == "author":
            h = int(r.get("h_index", 0))
            cites = int(r.get("citation_count", 0))
            n_papers = int(r.get("n_papers", 0))
            typer.echo(
                format_row([
                    f"h={h}",
                    f"cites={cites}",
                    f"n_papers={n_papers}",
                    format_handle("author", str(r.get("id", ""))),
                    str(r.get("name", "") or ""),
                ])
            )
        else:
            cites = int(r.get("citation_count", 0))
            pr = float(r.get("pagerank", 0.0))
            title = str(r.get("title", "") or "")
            typer.echo(
                format_row([
                    f"cites={cites}",
                    f"pr={pr:.4f}",
                    format_handle("doc", str(r.get("id", ""))),
                    title,
                ])
            )


# ---------------------------------------------------------------- repl


class ReplExitError(Exception):
    """Signal a clean interactive-session exit."""


class ReplError(Exception):
    """User-facing REPL command error."""


def _pop_flag(tokens: list[str], name: str) -> bool:
    if name not in tokens:
        return False
    tokens.remove(name)
    return True


def _pop_int_option(tokens: list[str], name: str, default: int) -> int:
    if name not in tokens:
        return default
    idx = tokens.index(name)
    try:
        raw = tokens[idx + 1]
    except IndexError as exc:
        raise ReplError(f"{name} requires an integer value") from exc
    del tokens[idx : idx + 2]
    try:
        return int(raw)
    except ValueError as exc:
        raise ReplError(f"{name} requires an integer value") from exc


def _pop_key_value(tokens: list[str], key: str) -> str | None:
    prefix = f"{key}="
    for token in list(tokens):
        if token.startswith(prefix):
            tokens.remove(token)
            return token[len(prefix):]
    return None


def _pop_key_int(tokens: list[str], key: str, default: int) -> int:
    raw = _pop_key_value(tokens, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ReplError(f"{key}= requires an integer value") from exc


def _pop_key_float(tokens: list[str], key: str, default: float) -> float:
    raw = _pop_key_value(tokens, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ReplError(f"{key}= requires a number") from exc


def _pop_float_option(tokens: list[str], name: str, default: float) -> float:
    if name not in tokens:
        return default
    idx = tokens.index(name)
    try:
        raw = tokens[idx + 1]
    except IndexError as exc:
        raise ReplError(f"{name} requires a number") from exc
    del tokens[idx : idx + 2]
    try:
        return float(raw)
    except ValueError as exc:
        raise ReplError(f"{name} requires a number") from exc


def _render_find_hits(hits: list[dict]) -> list[str]:
    lines: list[str] = []
    for hit in hits:
        score = hit.get("score", 0.0)
        cid = hit.get("id", "?")
        did = hit.get("doc_id") or hit.get("source_id") or "?"
        lines.append(f"{score:.3f}  {cid}  {did}")
    return lines


def _render_text_hits(hits: list[dict]) -> list[str]:
    return [
        f"{hit['id']}  {hit['doc_id']}  {hit['preview']}"
        for hit in hits
    ]


def _render_doc_hits(hits: list[dict]) -> list[str]:
    lines: list[str] = []
    for hit in hits:
        lines.append(
            f"{hit.get('best_score', 0.0):.3f}  "
            f"n={hit.get('n_chunks', 0)}  "
            f"{hit.get('doc_id', '?')}  "
            f"best={hit.get('best_chunk_id', '?')}  "
            f"{hit.get('title', '')}"
        )
    return lines


def _render_doc(session: CorpusSearchSession, doc_id: str) -> list[str]:
    doc = session.get_doc(doc_id)
    if doc is None:
        raise ReplError(f"doc not found: {doc_id}")
    meta = doc.metadata or {}
    lines = [
        f"id:       {doc.id}",
        f"title:    {doc.title or ''}",
        f"kind:     {doc.kind}",
        f"chunks:   {doc.n_chunks}",
    ]
    if "year" in meta:
        lines.append(f"year:     {meta['year']}")
    if "authors" in meta:
        lines.append(f"authors:  {len(meta['authors'] or [])}")
    return lines


def _render_chunk(
    session: CorpusSearchSession,
    chunk_id: str,
    *,
    full: bool,
) -> list[str]:
    chunk = session.get_chunk(chunk_id)
    if chunk is None:
        raise ReplError(f"chunk not found: {chunk_id}")
    return [
        f"id:           {chunk.id}",
        f"doc:          {chunk.doc_id}",
        f"section_path: {chunk.section_path}",
        "---",
        chunk.text if full else chunk.text[:500],
    ]


def _repl_help() -> list[str]:
    return [
        "commands:",
        "  find [--text] [--top-k N|top=N] <query>",
        "  find-papers [--text] [--top-k N|top=N] [pool=N] <query>",
        "  show <doc:id|chunk:id> [--full]",
        "  list docs",
        "  list chunks --doc <doc_id>|doc=<doc_id>",
        "  seed [--max N|max=N] [--pagerank-weight W|pagerank_weight=W]",
        "  help",
        "  exit",
    ]


def _run_repl_line(
    session: CorpusSearchSession,
    line: str,
) -> tuple[list[str], list[str], str]:
    """Run one line-oriented corpus command.

    Returns ``(output_lines, surfaced_chunk_ids, via)`` so the CLI wrapper can
    emit telemetry without coupling the reusable session to bundle state.
    """
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        raise ReplError(str(exc)) from exc
    if not tokens:
        return [], [], "corpus_repl_empty"

    cmd, args = tokens[0], tokens[1:]
    if cmd in {"exit", "quit"}:
        raise ReplExitError
    if cmd == "help":
        return _repl_help(), [], "corpus_repl_help"

    if cmd == "find":
        text = _pop_flag(args, "--text")
        top_k = _pop_int_option(args, "--top-k", _pop_key_int(args, "top", 8))
        if not args:
            raise ReplError("find requires a query")
        query = " ".join(args)
        if text:
            hits = session.search_text(query, top_k=top_k)
            return (
                _render_text_hits(hits),
                [hit.get("id", "") for hit in hits],
                "corpus_repl_find_text",
            )
        hits = session.search_semantic(query, top_k=top_k)
        return (
            _render_find_hits(hits),
            [hit.get("id", "") for hit in hits],
            "corpus_repl_find_semantic",
        )

    if cmd in {"find-papers", "papers"}:
        text = _pop_flag(args, "--text")
        top_k = _pop_int_option(args, "--top-k", _pop_key_int(args, "top", 8))
        chunk_pool = _pop_key_int(args, "pool", max(top_k * 5, top_k))
        if not args:
            raise ReplError("find-papers requires a query")
        query = " ".join(args)
        hits = (
            session.search_docs_text(query, top_k=top_k, chunk_pool=chunk_pool)
            if text else
            session.search_docs_semantic(query, top_k=top_k, chunk_pool=chunk_pool)
        )
        chunk_ids = [
            cid
            for hit in hits
            for cid in (hit.get("chunk_ids") or [])
            if cid
        ]
        return (
            _render_doc_hits(hits),
            chunk_ids,
            "corpus_repl_find_papers_text" if text else "corpus_repl_find_papers_semantic",
        )

    if cmd == "show":
        full = _pop_flag(args, "--full") or _pop_flag(args, "full")
        if len(args) != 1:
            raise ReplError("show requires exactly one handle")
        kind, ident = queries.parse_handle(args[0])
        if kind == "doc":
            return _render_doc(session, ident), [], "corpus_repl_show_doc"
        if kind == "chunk":
            return (
                _render_chunk(session, ident, full=full),
                [ident],
                "corpus_repl_show_chunk",
            )
        raise ReplError(f"unknown handle kind {kind!r}; use doc:<id> or chunk:<id>")

    if cmd == "list":
        if not args:
            raise ReplError("list requires docs or chunks")
        kind = args.pop(0)
        if kind == "docs":
            return session.list_docs(), [], "corpus_repl_list_docs"
        if kind == "chunks":
            doc_id = ""
            if "--doc" in args:
                idx = args.index("--doc")
                try:
                    doc_id = args[idx + 1]
                except IndexError as exc:
                    raise ReplError("--doc requires a doc id") from exc
            else:
                doc_id = _pop_key_value(args, "doc") or ""
            if not doc_id and args:
                doc_id = args[0]
            if not doc_id:
                raise ReplError("list chunks requires --doc <doc_id>")
            return session.list_chunks(doc_id), [], "corpus_repl_list_chunks"
        raise ReplError("list supports docs or chunks")

    if cmd == "seed":
        max_seeds = _pop_int_option(args, "--max", _pop_key_int(args, "max", 20))
        pagerank_weight = _pop_float_option(
            args,
            "--pagerank-weight",
            _pop_key_float(args, "pagerank_weight", 0.7),
        )
        if args:
            raise ReplError(f"unknown seed args: {' '.join(args)}")
        return (
            session.find_seeds(max_seeds=max_seeds, pagerank_weight=pagerank_weight),
            [],
            "corpus_repl_seed",
        )

    raise ReplError(f"unknown command: {cmd}; type help")


@app.command("serve")
def cmd_serve(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    port: int = typer.Option(
        0, "--port", help="TCP port to bind on 127.0.0.1 (0 = OS-assigned)."
    ),
) -> None:
    """Start an HTTP server hosting the corpus CLI in a single warm process.

    Bound to one corpus. The first stdout line is
    ``WIKIFY_CORPUS_SERVER=http://127.0.0.1:<port>`` so callers can
    capture it. The recommended pattern is::

        export WIKIFY_CORPUS_SERVER=$( \
            wikify corpus serve | head -1 | cut -d= -f2- )

    Set ``WIKIFY_CORPUS_SERVER`` in subsequent shells to route every
    ``wikify corpus …`` call through this server, collapsing
    per-call latency from ~1.2-5s to ~10-100ms once warm.

    Foreground only in this phase — background it via your shell
    (``wikify corpus serve --corpus X &``) or a process manager.
    """
    from ..serve import run_server

    corpus = _resolve_corpus(corpus_dir)
    run_server(corpus.root, port=port if port > 0 else None)


@app.command("repl")
def cmd_repl(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    prompt: str = typer.Option("wikify-corpus> ", "--prompt"),
) -> None:
    """Open a line-oriented corpus query session.

    The process keeps corpus docs/chunks warm immediately and keeps the
    embedding model/vector graph warm after the first semantic ``find``.
    """
    corpus = _open_corpus(corpus_dir)
    session = CorpusSearchSession(corpus)
    bundle = _resolve_cwd_bundle()
    typer.echo(
        f"ready corpus={corpus.root} docs={session.n_docs} "
        f"chunks={session.n_chunks}"
    )
    while True:
        try:
            line = input(prompt)
        except EOFError:
            typer.echo("")
            break
        try:
            lines, chunk_ids, via = _run_repl_line(session, line)
        except ReplExitError:
            break
        except ReplError as exc:
            typer.echo(f"error: {exc}", err=True)
            continue
        _emit_chunk_reads(bundle, chunk_ids, via=via)
        for out in lines:
            typer.echo(out)


__all__ = ["app"]
