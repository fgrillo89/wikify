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
from collections.abc import Iterable
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..bundle.run.events import Event, append_event
from ..bundle.run.state import load_state
from ..corpus import queries
from ..ingest.pipeline import ingest_corpus, refresh_corpus
from ._helpers import EXIT_VALIDATION, cli_error

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


def _open_corpus(corpus_flag: Path | None) -> Corpus:
    if corpus_flag is None:
        cli_error(
            EXIT_VALIDATION,
            error="corpus_required",
            message="--corpus <path> is required for this command",
        )
    if not corpus_flag.is_dir():
        cli_error(
            EXIT_VALIDATION,
            error="not_a_directory",
            message=f"corpus path is not a directory: {corpus_flag}",
        )
    return Corpus(root=corpus_flag)


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
    paths = Corpus(root=corpus_dir)
    refresh_corpus(paths)
    typer.echo(f"refresh complete: {paths.root}")


@app.command("check")
def cmd_check(
    corpus_dir: Path = typer.Argument(..., help="Corpus directory."),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Report corpus health: doc/chunk counts, derived artifacts, field."""
    if not corpus_dir.is_dir():
        cli_error(
            EXIT_VALIDATION,
            error="not_a_directory",
            message=f"corpus path is not a directory: {corpus_dir}",
        )
    corpus = Corpus(root=corpus_dir)
    summary = queries.check_corpus(corpus)
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


# --------------------------------------------------------------- list


list_app = typer.Typer(add_completion=False, help="List corpus handles.")
app.add_typer(list_app, name="list")


@list_app.command("docs")
def cmd_list_docs(
    corpus_dir: Path = typer.Option(..., "--corpus"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print every doc id in the corpus."""
    corpus = _open_corpus(corpus_dir)
    ids = queries.list_doc_ids(corpus)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": ids}))
        return
    for did in ids:
        typer.echo(did)


@list_app.command("chunks")
def cmd_list_chunks(
    corpus_dir: Path = typer.Option(..., "--corpus"),
    doc_id: str = typer.Option(..., "--doc"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print chunk ids for one document."""
    corpus = _open_corpus(corpus_dir)
    chunks = queries.list_chunks_for_doc(corpus, doc_id)
    ids = [c.id for c in chunks]
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": ids}))
        return
    for cid in ids:
        typer.echo(cid)


@list_app.command("files")
def cmd_list_files(
    corpus_dir: Path = typer.Option(..., "--corpus"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print every file under the corpus root, relative."""
    corpus = _open_corpus(corpus_dir)
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
    corpus_dir: Path = typer.Option(..., "--corpus"),
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
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Search the corpus.

    With ``--seed``, return the greedy submodular seed doc list.
    With ``--text``, do a literal substring grep over chunk text.
    Otherwise do semantic search and return ranked chunks.
    """
    corpus = _open_corpus(corpus_dir)
    if seed:
        ids = queries.find_seeds(
            corpus, max_seeds=max_seeds, pagerank_weight=pagerank_weight
        )
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "items": ids}))
            return
        for did in ids:
            typer.echo(did)
        return
    if text:
        hits = queries.search_text(corpus, query, top_k=top_k)
        _emit_chunk_reads(
            _resolve_cwd_bundle(),
            (h.get("id", "") for h in hits),
            via="corpus_find_text",
        )
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "items": hits}))
            return
        for h in hits:
            typer.echo(f"{h['id']}  {h['doc_id']}  {h['preview']}")
        return
    if not query:
        cli_error(
            EXIT_VALIDATION,
            error="missing_query",
            message="`corpus find` requires a query, --seed, or --text mode",
        )
    hits = queries.search_chunks(corpus, query, top_k=top_k)
    _emit_chunk_reads(
        _resolve_cwd_bundle(),
        (h.get("id", "") for h in hits),
        via="corpus_find_semantic",
    )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": hits}))
        return
    for h in hits:
        score = h.get("score", 0.0)
        cid = h.get("id", "?")
        did = h.get("doc_id") or h.get("source_id") or "?"
        typer.echo(f"{score:.3f}  {cid}  {did}")


# ---------------------------------------------------------------- show


@app.command("show")
def cmd_show(
    handle: str = typer.Argument(..., help="doc:<id> or chunk:<id>"),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Dereference one handle and print its content."""
    corpus = _open_corpus(corpus_dir)
    try:
        kind, ident = queries.parse_handle(handle)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_handle", message=str(exc))

    if kind == "doc":
        doc = queries.get_doc(corpus, ident)
        if doc is None:
            cli_error(EXIT_VALIDATION, error="doc_not_found", id=ident)
        meta = doc.metadata or {}
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": True,
                        "id": doc.id,
                        "title": doc.title,
                        "kind": doc.kind,
                        "metadata": meta,
                        "n_chunks": doc.n_chunks,
                    }
                )
            )
            return
        typer.echo(f"id:       {doc.id}")
        typer.echo(f"title:    {doc.title or ''}")
        typer.echo(f"kind:     {doc.kind}")
        typer.echo(f"chunks:   {doc.n_chunks}")
        if "year" in meta:
            typer.echo(f"year:     {meta['year']}")
        if "authors" in meta:
            typer.echo(f"authors:  {len(meta['authors'] or [])}")
        return

    if kind == "chunk":
        chunk = queries.get_chunk(corpus, ident)
        if chunk is None:
            cli_error(EXIT_VALIDATION, error="chunk_not_found", id=ident)
        _emit_chunk_reads(
            _resolve_cwd_bundle(),
            [chunk.id],
            via="corpus_show_chunk",
            doc_id=chunk.doc_id,
        )
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": True,
                        "id": chunk.id,
                        "doc_id": chunk.doc_id,
                        "section_path": list(chunk.section_path or []),
                        "text": chunk.text if full else chunk.text[:500],
                    }
                )
            )
            return
        typer.echo(f"id:           {chunk.id}")
        typer.echo(f"doc:          {chunk.doc_id}")
        typer.echo(f"section_path: {chunk.section_path}")
        if full:
            typer.echo("---")
            typer.echo(chunk.text)
        else:
            typer.echo("---")
            typer.echo(chunk.text[:500])
        return

    cli_error(
        EXIT_VALIDATION,
        error="bad_handle_kind",
        message=f"unknown handle kind {kind!r}; use doc:<id> or chunk:<id>",
    )


__all__ = ["app"]
