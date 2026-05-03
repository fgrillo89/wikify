"""``wikify corpus ...`` — corpus build + read-only query surface.

Commands::

    corpus build <source> --out <corpus> [--mode additive|sync] [--parser ...]
    corpus refresh <corpus>
    corpus check [<corpus>] [--full]
    corpus list [docs|chunks|files] [--corpus <c>] [--doc <d>]
    corpus find "<query>" [--top-k <n>] [--text] [--by chunk|paper|author]
    corpus sample [--max <n>] [--strategy diverse] [--pagerank-weight W]
    corpus show <handle> [--full]
    corpus traverse <handle> --to <relation>
    corpus schema
    corpus repl

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
    format_chunk_handles,
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
    openalex: bool = typer.Option(
        True,
        "--openalex/--no-openalex",
        help=(
            "Enrich citation metadata via the OpenAlex API. ON by default; "
            "this issues network requests to api.openalex.org during refresh. "
            "Pass --no-openalex to skip Wave C and stay fully offline."
        ),
    ),
) -> None:
    """Parse, chunk, embed, and graph an input directory.

    Network: when --openalex is on (default), refresh hits api.openalex.org
    to canonicalise bib metadata and surface in-corpus citation matches.
    Set OPENALEX_EMAIL for the polite-pool rate limit (10 req/s).
    """
    from ..ingest.pipeline import ingest_corpus

    paths = ingest_corpus(
        source,
        out,
        max_workers=None if workers == 0 else workers,
        mode=mode,
        parser_backend=parser,
        refresh=not no_refresh,
        resolve_bibliography_doi=openalex,
    )
    typer.echo(f"corpus written to {paths.root}")


@app.command("rechunk")
def cmd_rechunk(
    corpus_dir: Path = typer.Argument(..., help="Corpus directory."),
    only_doc: list[str] = typer.Option(
        [],
        "--only-doc",
        help=(
            "Limit to specific doc id(s); pass multiple times. "
            "Useful when iterating on chunker behaviour against a few "
            "audit-flagged docs without rebuilding the whole corpus."
        ),
    ),
    workers: int = typer.Option(
        0,
        "--workers",
        help=(
            "Number of chunker worker processes (0 = auto, ~60%% of "
            "cores). Set to 1 to force serial execution."
        ),
    ),
    openalex: bool = typer.Option(
        False,
        "--openalex/--no-openalex",
        help=(
            "Re-run OpenAlex enrichment after rechunking. OFF by default "
            "because chunking changes do not affect bibliographies."
        ),
    ),
) -> None:
    """Re-chunk an existing corpus from saved markdown.

    Skips parsing entirely (no Marker, no Docling, no OCR). Reads
    each doc's persisted markdown and image sidecars, runs the
    universal HybridChunker, re-extracts equations / citations /
    figure refs, and rewrites the chunk-derived disk artefacts plus
    the SQLite store. Embeddings get rebuilt for every doc whose
    chunk ids changed (which is all of them when the chunker
    changes), so the wall-clock is dominated by chunking +
    embedding, not parsing.

    Use this when chunker logic, section detection, or boilerplate
    rules change. Use ``corpus build`` when source PDFs change.
    """
    from ..ingest.rechunk import rechunk_corpus

    paths = Corpus(root=corpus_dir)
    if not paths.docs_dir.exists():
        cli_error(
            EXIT_VALIDATION,
            error="not_a_corpus",
            message=f"{corpus_dir} has no docs/ directory",
        )
    summary = rechunk_corpus(
        paths,
        only_docs=only_doc or None,
        max_workers=workers if workers > 0 else None,
        resolve_bibliography_doi=openalex,
        cite_resolution="crossref" if openalex else "off",
    )
    typer.echo(
        f"rechunk complete: {summary['docs']} docs / "
        f"{summary['chunks']} chunks in "
        f"{summary['total_seconds']}s"
    )


@app.command("refresh")
def cmd_refresh(
    corpus_dir: Path = typer.Argument(...),
    openalex: bool = typer.Option(
        True,
        "--openalex/--no-openalex",
        help=(
            "Enrich citation metadata via the OpenAlex API. ON by default; "
            "this issues network requests to api.openalex.org. "
            "Pass --no-openalex to skip Wave C and stay fully offline."
        ),
    ),
) -> None:
    """Rebuild derived corpus artifacts (embeddings, graph, topics, ...).

    Network: when --openalex is on (default), Wave C of the refresh DAG
    hits api.openalex.org to canonicalise bib metadata and surface
    in-corpus citation matches. Set OPENALEX_EMAIL for the polite-pool
    rate limit (10 req/s).
    """
    from ..ingest.pipeline import refresh_corpus

    paths = Corpus(root=corpus_dir)
    refresh_corpus(paths, resolve_bibliography_doi=openalex)
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
            "(requires probing the SQLite graph store — slower)."
        ),
    ),
) -> None:
    """Report corpus health: doc/chunk counts, derived artifacts, field.

    The default form stays fast by skipping citation-index coverage.
    Pass ``--full`` to also report ``cite_index`` coverage (% of
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
    typer.echo(f"sqlite:      {summary['has_sqlite_store']}")
    if summary.get("has_sqlite_store"):
        typer.echo(f"  docs:      {summary.get('sqlite_n_docs', '?')}")
        typer.echo(f"  chunks:    {summary.get('sqlite_n_chunks', '?')}")
        typer.echo(f"  embeds:    {summary.get('sqlite_n_embeddings', '?')}")
        if "sqlite_n_edges" in summary:
            typer.echo(f"  edges:     {summary['sqlite_n_edges']}")
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
    query: str = typer.Argument("", help="Query string. Required unless --text mode."),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    top_k: int = typer.Option(8, "--top-k"),
    text: bool = typer.Option(False, "--text", help="Literal substring grep."),
    by: str = typer.Option(
        "chunk",
        "--by",
        help="Aggregate by chunk (default) or paper.",
    ),
    rank: str = typer.Option(
        "semantic",
        "--rank",
        help=(
            "Ranking metric: semantic | bm25 | hybrid | all | citation_count | "
            "pagerank. `all` runs semantic+bm25+text, RRF-fuses, dedupes, and "
            "tags each row with which channels matched."
        ),
    ),
    field: str = typer.Option(
        "chunk_text",
        "--field",
        help=(
            "Search field: chunk_text (default) or title. "
            "--field title runs a literal substring on Document.title; "
            "valid only with --by paper."
        ),
    ),
    fmt: str = typer.Option(
        "auto",
        "--format",
        help=(
            "Output format: auto (compact for TTY / quiet for pipe), "
            "quiet (handles only), compact (tab-separated columns), json."
        ),
    ),
    in_doc: str | None = typer.Option(
        None,
        "--in-doc",
        help=(
            "Scope chunk search to one document. Accepts any doc handle "
            "(short suffix, hex, or full id). BM25 / text get a cheap "
            "WHERE filter; vector search post-filters a wider pool."
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

    - ``--text`` does a literal substring grep over chunk text.
    - Otherwise semantic search; ``--by paper`` aggregates to documents
      and ``--rank citation_count|pagerank`` reorders the result.

    For query-less corpus sampling (the old ``--seed`` mode), use
    ``corpus sample`` — it owns the strategy + knobs surface.

    Compact output columns:

    - chunks (``--by chunk``):  ``score \\t cites=N \\t chunk-handle \\t doc-handle``
      where ``score`` is the semantic cosine (0..1, higher=closer; ``.``
      under ``--text``) and ``cites`` is the parent doc's in-corpus
      citation count.
    - papers (``--by paper``):  ``score \\t cites=N \\t n=K \\t doc-handle \\t title``
      where ``n`` is how many chunks of that paper matched the query.
    - metric-only ranking: ``cites=N \\t pr=X.XXXX \\t doc-handle \\t title``
      where ``pr`` is the corpus PageRank.
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt_resolved = _resolve_format_or_error(fmt)
    _validate_positive_int("top-k", top_k)
    if explain:
        _emit_find_explain(
            corpus,
            query=query,
            text=text,
            by=by,
            rank=rank,
            top_k=top_k,
        )
        return

    resolved_in_doc: str | None = None
    if in_doc is not None:
        try:
            resolved_in_doc = queries.resolve_doc_id(corpus, in_doc.removeprefix("doc:"))
        except queries.HandleNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_in_doc", message=str(exc))
    try:
        result = queries.find(
            corpus, query=query, by=by, rank=rank, top_k=top_k,
            text=text, field=field, in_doc=resolved_in_doc,
        )
    except queries.QueryError as exc:
        cli_error(EXIT_VALIDATION, error=exc.code, message=exc.message)

    kind = result["kind"]
    rows = result["rows"]
    if kind == "docs":
        _emit_doc_rows(
            [{**r, "score": None} for r in rows], fmt=fmt_resolved
        )
        return
    if kind == "authors":
        score_key = "best_score" if result.get("scored") else None
        _emit_author_rows(rows, fmt=fmt_resolved, score_key=score_key)
        return
    if kind == "papers":
        _emit_paper_rows(corpus, rows, fmt=fmt_resolved, rank=rank, top_k=top_k)
        return
    # kind == "chunks"
    via = (
        "corpus_find_text" if text else "corpus_find_semantic"
    )
    _emit_chunk_reads(
        _resolve_cwd_bundle(),
        (h.get("id", "") for h in rows),
        via=via,
    )
    score_key = "score" if result.get("scored") else None
    _emit_chunk_rows(corpus, rows, fmt=fmt_resolved, score_key=score_key)


@app.command("sample")
def cmd_sample(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    max_docs: int = typer.Option(
        20, "--max", help="Maximum number of docs to sample (must be > 0)."
    ),
    strategy: str = typer.Option(
        "diverse",
        "--strategy",
        help=(
            "Sampling strategy. Today: 'diverse' (greedy submodular over "
            "PageRank + coverage)."
        ),
    ),
    pagerank_weight: float = typer.Option(
        0.7,
        "--pagerank-weight",
        help=(
            "Trade-off between PageRank prior and submodular coverage gain "
            "(0.0=coverage only, 1.0=pagerank only). Used by 'diverse'."
        ),
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
    """Sample diverse / representative documents without a query.

    Use this to bootstrap exploration when you don't have a query but
    want a small set of central, mutually-distinct corpus entry points.

    Compact output columns: ``cites=N \\t pr=X.XXXX \\t doc-handle \\t title``
    (in-corpus citation count, PageRank, doc handle, title).
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt_resolved = _resolve_format_or_error(fmt)
    _validate_positive_int("max", max_docs)
    if explain:
        _emit_sample_explain(
            corpus,
            strategy=strategy,
            max_docs=max_docs,
            pagerank_weight=pagerank_weight,
        )
        return
    try:
        ids = queries.sample_docs(
            corpus,
            max_docs=max_docs,
            strategy=strategy,
            pagerank_weight=pagerank_weight,
        )
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_strategy", message=str(exc))
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
    """Print chunk-level search results in the chosen format.

    Chunk handles are disambiguated within the result set: bare short
    suffix when unique, ``chunk:<doc-short>/<chunk-short>`` when two
    chunks would otherwise share the same printed handle.
    """
    doc_ids_in_order: list[str] = []
    seen: set[str] = set()
    for h in hits:
        did = str(h.get("doc_id") or h.get("source_id") or "")
        if did and did not in seen:
            seen.add(did)
            doc_ids_in_order.append(did)
    metrics = queries.doc_metrics(corpus, doc_ids_in_order)
    chunk_handles = format_chunk_handles(
        (str(h.get("id", "")), str(h.get("doc_id") or h.get("source_id") or ""))
        for h in hits
    )
    if fmt == "json":
        items = []
        for h in hits:
            did = str(h.get("doc_id") or h.get("source_id") or "")
            cid = str(h.get("id", ""))
            items.append(
                {
                    **h,
                    "chunk_handle": chunk_handles.get(cid, format_handle("chunk", cid)),
                    "doc_handle": format_handle("doc", did) if did else "",
                    "citation_count": metrics.get(did, {}).get("citation_count", 0),
                }
            )
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for h in hits:
            cid = str(h.get("id", ""))
            typer.echo(chunk_handles.get(cid, format_handle("chunk", cid)))
        return
    for h in hits:
        cid = str(h.get("id", ""))
        did = str(h.get("doc_id") or h.get("source_id") or "")
        cites = metrics.get(did, {}).get("citation_count", 0)
        score_val = h.get(score_key) if score_key else None
        score_col = f"{float(score_val):.3f}" if score_val is not None else "."
        cols = [
            score_col,
            f"cites={cites}",
            chunk_handles.get(cid, format_handle("chunk", cid)),
            format_handle("doc", did) if did else "",
        ]
        if "modes" in h:
            cols.insert(1, _modes_badge(h["modes"]))
        typer.echo(format_row(cols))


def _modes_badge(modes: list[str]) -> str:
    """3-letter badge: s=semantic, b=bm25, t=text. Letter present == mode matched."""
    s = "s" if "semantic" in modes else "-"
    b = "b" if "bm25" in modes else "-"
    t = "t" if "text" in modes else "-"
    return f"via={s}{b}{t}"


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
    """Print doc-only rows (sampling and metric-only ranking)."""
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
    text: bool,
    by: str,
    rank: str,
    top_k: int,
) -> None:
    """Print a fluent-chain-style description of what `find` would do."""
    typer.echo(f"corpus: {corpus.root}")
    if text:
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


def _emit_sample_explain(
    corpus: Corpus,
    *,
    strategy: str,
    max_docs: int,
    pagerank_weight: float,
) -> None:
    typer.echo(f"corpus: {corpus.root}")
    typer.echo(
        f"chain:  sample_{strategy}(max_docs={max_docs}, "
        f"pagerank_weight={pagerank_weight}) -> top docs"
    )


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
        result = queries.show(corpus, handle=handle, full=full)
    except queries.QueryError as exc:
        cli_error(EXIT_VALIDATION, error=exc.code, message=exc.message)
    except AmbiguousHandleError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="ambiguous_handle",
            message=str(exc),
            matches=exc.matches,
        )

    kind = result["handle_kind"]

    if kind == "doc":
        doc = result["data"]
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
        chunk = result["data"]
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
        fig = result["data"]
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
        au = result["data"]
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
        eq = result["data"]
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


# ---------------------------------------------------------------- schema


metrics_app = typer.Typer(
    add_completion=False, help="Metric refresh / inspection over the SQLite store.",
)
app.add_typer(metrics_app, name="metrics")


@metrics_app.command("refresh")
def cmd_metrics_refresh(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    view: str = typer.Option(
        "corpus_citation",
        "--view",
        help=(
            "Graph view to refresh: corpus_citation | author_coauthor | "
            "chunk_citation | all."
        ),
    ),
) -> None:
    """Recompute global metrics (PageRank, h-index, degree centrality)."""
    from ..corpus.store.metrics import refresh_cheap_metrics
    from ..corpus.store.metrics_global import (
        VIEWS,
        refresh_h_index,
        refresh_view,
    )
    from ..corpus.store.routing import open_store

    corpus = _resolve_corpus(corpus_dir)
    store = open_store(corpus.root)
    try:
        refresh_cheap_metrics(store.con)
        if view == "all":
            written: dict[str, list[str]] = {}
            for v in VIEWS:
                written[v] = refresh_view(store.con, v)
            refresh_h_index(store.con)
            for vname, metrics in written.items():
                typer.echo(f"refreshed {vname}: {', '.join(metrics)}")
            typer.echo("refreshed author_h_index: h_index")
            return
        if view not in VIEWS:
            cli_error(
                EXIT_VALIDATION,
                error="bad_view",
                message=f"unknown view {view!r}; expected one of "
                f"{sorted(VIEWS)} or 'all'",
            )
        metrics = refresh_view(store.con, view)
        if view == "corpus_citation":
            refresh_h_index(store.con)
            metrics.append("h_index")
        typer.echo(f"refreshed {view}: {', '.join(metrics)}")
    finally:
        store.close()


@metrics_app.command("list")
def cmd_metrics_list(
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
) -> None:
    """List the registered graph views and their freshness."""
    from ..corpus.store.metrics_global import VIEWS, view_status
    from ..corpus.store.routing import open_store

    corpus = _resolve_corpus(corpus_dir)
    store = open_store(corpus.root)
    try:
        for v in VIEWS.values():
            status = view_status(store.con, v.name)
            badge = (status or {}).get("status", "stale")
            kinds = ", ".join(v.edge_kinds)
            metrics = ", ".join(v.metrics)
            typer.echo(f"{v.name:24s} [{badge}] kinds={kinds} metrics={metrics}")
    finally:
        store.close()


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
    schema = queries.SCHEMA
    if fmt == "json":
        typer.echo(json.dumps(schema, indent=2))
        return
    typer.echo("Node types:")
    for k, v in schema["node_types"].items():
        typer.echo(f"  {k:10s}  {v}")
    typer.echo("")
    typer.echo("Edge kinds:")
    for kind in schema["edge_kinds"]:
        typer.echo(f"  {kind}")
    typer.echo("")
    typer.echo("Traverse relations (corpus traverse <handle> --to <relation>):")
    for handle_kind, rels in schema["traverse_relations"].items():
        typer.echo(f"  {handle_kind+':':<8s} {' | '.join(rels)}")
    typer.echo("")
    typer.echo("Rank metrics:")
    for over, metrics in schema["rank_metrics"].items():
        typer.echo(f"  over {over+'s:':<8s} {' | '.join(metrics)}")
    typer.echo("")
    typer.echo("find modes:")
    for k, v in schema["find_modes"].items():
        typer.echo(f"  {k:14s} {v}")
    typer.echo("")
    typer.echo("sample strategies (corpus sample --strategy):")
    for k, v in schema["sample_strategies"].items():
        typer.echo(f"  {k:14s} {v}")
    typer.echo("")
    typer.echo(f"Formats: {' | '.join(schema['formats'])}")
    typer.echo("")
    typer.echo(f"Handles: {schema['handle_resolution']}")


# ---------------------------------------------------------------- similarity-walk


@app.command("similarity-walk")
def cmd_similarity_walk(
    query: str = typer.Argument(
        "", help="Concept to seed the walk on (omit when --from-chunk is set).",
    ),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    from_chunk: str | None = typer.Option(
        None, "--from-chunk",
        help=(
            "Seed from one chunk handle instead of a query; "
            "mutually exclusive with the positional query."
        ),
    ),
    depth: int = typer.Option(2, "--depth"),
    top_k: int = typer.Option(5, "--top-k", help="Seed count at hop 0 (query mode only)."),
    neighbors: int = typer.Option(3, "--neighbors", help="Per-chunk fanout per hop."),
    threshold: float = typer.Option(
        0.65, "--threshold",
        help="Cosine cut; below this, edges are dropped. Calibrated for jina-v2-small.",
    ),
    rank: str = typer.Option(
        "all", "--rank", help="Hop-0 search method (query mode only).",
    ),
    cross_doc_only: bool = typer.Option(
        True, "--cross-doc-only/--include-same-doc",
        help="Filter same-doc neighbours (default) or include them.",
    ),
    fmt: str = typer.Option("auto", "--format"),
) -> None:
    """Cosine-similarity walk over chunk vectors.

    Two seed modes (mutually exclusive):

    - positional `<query>` — seed via `find` with the chosen rank.
    - `--from-chunk <handle>` — seed from one chunk; no search step.

    Each hop expands every chunk in the frontier into up to
    `--neighbors` cosine-similar chunks above `--threshold`. Edges are
    typed `similar` with a score; chunks are deduped across paths.
    The vector matrix is loaded once per process; subsequent walks
    re-use it.
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt_resolved = _resolve_format_or_error(fmt)
    if (not query) and (not from_chunk):
        cli_error(
            EXIT_VALIDATION, error="bad_seed",
            message="provide a query (positional) or --from-chunk",
        )
    if query and from_chunk:
        cli_error(
            EXIT_VALIDATION, error="bad_seed",
            message="--from-chunk and a positional query are mutually exclusive",
        )
    if depth < 0:
        cli_error(EXIT_VALIDATION, error="bad_depth",
                  message=f"depth must be >= 0; got {depth}")
    try:
        out = queries.similarity_walk(
            corpus,
            query=query or None,
            from_chunk=from_chunk,
            depth=depth,
            top_k=top_k,
            neighbors=neighbors,
            threshold=threshold,
            rank=rank,
            cross_doc_only=cross_doc_only,
        )
    except queries.QueryError as exc:
        cli_error(EXIT_VALIDATION, error=exc.code, message=exc.message)

    if fmt_resolved == "json":
        typer.echo(json.dumps({"ok": True, **out}))
        return
    if fmt_resolved == "quiet":
        for c in out["chunks"].values():
            typer.echo(format_handle("chunk", c["id"]))
        return
    edges_by_dst = {e["dst_chunk"]: e for e in out["edges"]}
    for c in sorted(
        out["chunks"].values(),
        key=lambda r: (r.get("hop", 0), r.get("id", "")),
    ):
        cid = c["id"]
        did = c.get("doc_id", "")
        modes = c.get("modes") or []
        via = "via=" + "".join(
            m[0] if m in modes else "-" for m in ("semantic", "bm25", "text")
        )
        edge = edges_by_dst.get(cid)
        cite = ""
        if edge:
            cite = (
                f"  similar={edge['score']:.3f} <- chunk:{edge['src_chunk'][-12:]}"
            )
        typer.echo(
            format_row([
                f"  hop={c['hop']}",
                via,
                format_handle("chunk", cid),
                format_handle("doc", did) if did else "",
                cite,
            ])
        )


# ---------------------------------------------------------------- citation-walk


@app.command("citation-walk")
def cmd_citation_walk(
    query: str = typer.Argument(..., help="Concept to ground the walk on."),
    corpus_dir: Path | None = typer.Option(None, "--corpus"),
    depth: int = typer.Option(2, "--depth", help="Citation hops; 0 = seeds only."),
    top_k: int = typer.Option(5, "--top-k", help="Seed chunks at hop 0."),
    rank: str = typer.Option(
        "all", "--rank",
        help="Ranking method for seed + per-hop sub-search.",
    ),
    fmt: str = typer.Option("auto", "--format"),
) -> None:
    """Concept-grounded recursive citation walk.

    Hop 0: top chunks for *query* corpus-wide. For each, follow
    chunk_citations to in-corpus papers and pick that paper's best
    chunk for the same query (scoped to the doc). Recurse to *depth*
    hops, deduping chunks across paths.

    Output (compact): one row per chunk with hop and via tags, plus
    parent edges showing the citation marker that led there. JSON
    returns the full {seeds, edges, chunks} payload.
    """
    corpus = _resolve_corpus(corpus_dir)
    fmt_resolved = _resolve_format_or_error(fmt)
    _validate_positive_int("top-k", top_k)
    if depth < 0:
        cli_error(EXIT_VALIDATION, error="bad_depth",
                  message=f"depth must be >= 0; got {depth}")
    try:
        out = queries.citation_walk(
            corpus, query=query, depth=depth, top_k=top_k, rank=rank,
        )
    except queries.QueryError as exc:
        cli_error(EXIT_VALIDATION, error=exc.code, message=exc.message)

    if fmt_resolved == "json":
        typer.echo(json.dumps({"ok": True, **out}))
        return
    if fmt_resolved == "quiet":
        for c in out["chunks"].values():
            typer.echo(format_handle("chunk", c["id"]))
        return
    edges_by_dst = {e["dst_chunk"]: e for e in out["edges"]}
    for c in sorted(out["chunks"].values(), key=lambda r: (r.get("hop", 0), r.get("id", ""))):
        cid = c["id"]
        did = c.get("doc_id", "")
        modes = c.get("modes") or []
        via = (
            "via=" + "".join(
                m[0] if m in modes else "-"
                for m in ("semantic", "bm25", "text")
            )
        )
        edge = edges_by_dst.get(cid)
        prefix = f"  hop={c['hop']}"
        cite = ""
        if edge:
            cite = f"  cited-via={edge['marker']} <- chunk:{edge['src_chunk'][-12:]}"
        typer.echo(
            format_row([
                prefix,
                via,
                format_handle("chunk", cid),
                format_handle("doc", did) if did else "",
                cite,
            ])
        )


# ---------------------------------------------------------------- traverse


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

    if explain:
        _emit_traverse_explain(
            corpus, handle=handle, to=to, rank=rank, top_k=top_k
        )
        return

    try:
        result = queries.traverse(
            corpus,
            handle=handle,
            to=to,
            rank=(rank or None),
            top_k=top_k or None,
        )
    except queries.QueryError as exc:
        cli_error(EXIT_VALIDATION, error=exc.code, message=exc.message)
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

    _emit_traverse_rows(result["rows"], fmt=fmt_resolved)


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


def _pop_str_option(tokens: list[str], name: str, default: str | None = None) -> str | None:
    """Like ``_pop_int_option`` but for free-form strings."""
    if name not in tokens:
        return default
    idx = tokens.index(name)
    try:
        raw = tokens[idx + 1]
    except IndexError as exc:
        raise ReplError(f"{name} requires a value") from exc
    del tokens[idx : idx + 2]
    return raw


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
        "  sample [--max N|max=N] [--strategy diverse|strategy=diverse]"
        " [--pagerank-weight W|pagerank_weight=W]",
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

    if cmd == "sample":
        max_docs = _pop_int_option(args, "--max", _pop_key_int(args, "max", 20))
        strategy = _pop_str_option(
            args, "--strategy", _pop_key_value(args, "strategy") or "diverse"
        )
        pagerank_weight = _pop_float_option(
            args,
            "--pagerank-weight",
            _pop_key_float(args, "pagerank_weight", 0.7),
        )
        if args:
            raise ReplError(f"unknown sample args: {' '.join(args)}")
        try:
            ids = session.sample_docs(
                max_docs=max_docs,
                strategy=strategy,
                pagerank_weight=pagerank_weight,
            )
        except ValueError as exc:
            raise ReplError(str(exc)) from exc
        return ids, [], "corpus_repl_sample"

    raise ReplError(f"unknown command: {cmd}; type help")


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
