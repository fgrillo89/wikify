"""FastMCP server: tool + resource registration for the corpus surface.

Phase 1 ships the corpus tools (``corpus_find``, ``corpus_traverse``,
``corpus_show``, ``corpus_sample``, ``corpus_schema``) and the
``context_show`` / ``context_set`` binding helpers, plus the
``wikify://corpus/...`` resource templates. Wiki, bundle, mutations,
and ingest/render/eval ship in later phases.

All tools call into :mod:`wikify.corpus.queries` — the same domain
APIs the CLI calls — so behavior parity is enforced by construction.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..corpus import queries
from ..corpus.handles import (
    AmbiguousHandleError,
    HandleNotFoundError,
    short_id,
)
from . import context
from .envelope import (
    author_item,
    chunk_item,
    chunk_row_item,
    doc_item,
    doc_row_item,
    equation_item,
    err,
    figure_item,
    ok,
    traverse_row_item,
)


def _shape_find_items(result: dict) -> list[dict]:
    kind = result["kind"]
    rows = result["rows"]
    scored = result.get("scored")
    if kind == "chunks":
        return [
            chunk_row_item(r, score=(r.get("score") if scored else None))
            for r in rows
        ]
    if kind == "papers":
        return [doc_row_item(r) for r in rows]
    if kind == "authors":
        return [author_item(r, in_search_mode=bool(scored)) for r in rows]
    if kind == "docs":
        return [doc_row_item(r) for r in rows]
    return []


def _shape_show_item(result: dict) -> dict:
    kind = result["handle_kind"]
    data = result["data"]
    if kind == "doc":
        return doc_item(data)
    if kind == "chunk":
        return chunk_item(data, full=bool(result.get("full")))
    if kind == "figure":
        return figure_item(data)
    if kind == "equation":
        return equation_item(data)
    if kind == "author":
        return author_item(data)
    raise RuntimeError(f"unknown handle_kind {kind!r}")


def _handle_query_error(exc: queries.QueryError) -> dict:
    return err(exc.code, exc.message)


def _handle_handle_lookup_error(exc: Exception) -> dict:
    if isinstance(exc, AmbiguousHandleError):
        return err(
            "ambiguous_handle",
            str(exc),
            matches=list(exc.matches),
        )
    return err("handle_not_found", str(exc))


def build_server() -> FastMCP:
    """Construct (but do not run) the wikify FastMCP server.

    The factory shape exists so tests can call decorated tool functions
    directly without standing up a stdio loop. Production callers do
    ``build_server().run("stdio")``.
    """
    srv = FastMCP("wikify")

    # ------------------------------------------------------------- context

    @srv.tool()
    async def context_show() -> dict:
        """Show the current corpus/bundle binding."""
        return ok("context", items=[context.snapshot()])

    @srv.tool()
    async def context_set(corpus_path: str | None = None,
                          bundle_path: str | None = None,
                          clear_bundle: bool = False) -> dict:
        """Bind or rebind the active corpus and/or bundle.

        Pass ``corpus_path`` and/or ``bundle_path`` to change the
        binding. ``clear_bundle=True`` drops the bundle binding while
        keeping the corpus binding.
        """
        try:
            context.bind(
                corpus_path=corpus_path,
                bundle_path=bundle_path,
                clear_bundle=clear_bundle,
            )
        except (context.ContextError, FileNotFoundError) as exc:
            return err("bad_context", str(exc))
        return ok("context", items=[context.snapshot()])

    # ----------------------------------------------------------- find/traverse/show

    @srv.tool()
    async def corpus_find(query: str = "", by: str = "chunk",
                          rank: str = "semantic", top_k: int = 8,
                          text: bool = False) -> dict:
        """Search the corpus.

        ``by`` is ``chunk`` | ``paper`` | ``author``. ``rank`` is
        ``semantic`` (default) or one of the graph metrics advertised
        by ``corpus_schema`` (``citation_count``, ``pagerank``,
        ``h_index``, ``n_papers``). With no query and a graph metric,
        the whole population is ranked. ``text=True`` switches to a
        literal substring grep.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            result = queries.find(
                corpus, query=query, by=by, rank=rank,
                top_k=top_k, text=text,
            )
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        return ok("corpus_find_result", items=_shape_find_items(result))

    @srv.tool()
    async def corpus_traverse(handle: str, to: str, rank: str = "",
                              top_k: int = 0) -> dict:
        """Traverse one hop from a handle.

        ``handle`` is ``doc:<id>`` | ``chunk:<id>`` | ``author:<key>``.
        ``to`` is a relation advertised by ``corpus_schema``
        (e.g. ``cited-by``, ``references``, ``chunks``, ``figures``,
        ``equations``, ``authors``, ``source``, ``cited-in-corpus``,
        ``sources``, ``coauthors``). ``top_k=0`` means unlimited.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            result = queries.traverse(
                corpus, handle=handle, to=to,
                rank=(rank or None),
                top_k=top_k or None,
            )
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        except (AmbiguousHandleError, HandleNotFoundError) as exc:
            return _handle_handle_lookup_error(exc)
        items = [traverse_row_item(r) for r in result["rows"]]
        return ok("corpus_traverse_result", items=items,
                  notes=[f"handle_kind={result['handle_kind']}"])

    @srv.tool()
    async def corpus_show(handle: str, full: bool = False) -> dict:
        """Dereference one handle and return its content.

        ``full=True`` returns full chunk text on chunk handles; doc /
        figure / equation / author payloads are always full.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            result = queries.show(corpus, handle=handle, full=full)
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        except (AmbiguousHandleError, HandleNotFoundError) as exc:
            return _handle_handle_lookup_error(exc)
        return ok("corpus_show_result", items=[_shape_show_item(result)])

    @srv.tool()
    async def corpus_sample(strategy: str = "diverse", max_docs: int = 20,
                            pagerank_weight: float = 0.7) -> dict:
        """Sample documents without a query.

        Strategies are advertised by ``corpus_schema``. Today only
        ``diverse`` (greedy submodular over PageRank + coverage) is
        implemented.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        if max_docs <= 0:
            return err("bad_max_docs", f"max_docs must be > 0; got {max_docs}")
        try:
            ids = queries.sample_docs(
                corpus, max_docs=max_docs, strategy=strategy,
                pagerank_weight=pagerank_weight,
            )
        except ValueError as exc:
            return err("bad_strategy", str(exc))
        metrics = queries.doc_metrics(corpus, ids)
        rows: list[dict] = []
        for did in ids:
            doc = queries.get_doc(corpus, did)
            title = doc.title if doc is not None else ""
            m = metrics.get(did, {})
            rows.append(
                doc_row_item({
                    "doc_id": did,
                    "title": title,
                    "citation_count": m.get("citation_count", 0),
                    "pagerank": m.get("pagerank", 0.0),
                })
            )
        return ok("corpus_sample_result", items=rows,
                  notes=[f"strategy={strategy}",
                         f"pagerank_weight={pagerank_weight}"])

    @srv.tool()
    async def corpus_schema() -> dict:
        """Self-describe the corpus query surface."""
        return ok("corpus_schema", items=[queries.SCHEMA])

    # ------------------------------------------------------------ resources

    def _resolve_doc_payload(ident: str) -> dict[str, Any]:
        corpus = context.require_corpus()
        doc = queries.get_doc(corpus, ident)
        if doc is None:
            raise queries.QueryError("doc_not_found", f"doc not found: {ident}")
        return {
            "id": doc.id,
            "handle": f"doc:{short_id(doc.id)}",
            "title": doc.title,
            "kind": doc.kind,
            "metadata": doc.metadata or {},
            "n_chunks": doc.n_chunks,
            "abstract": doc.abstract or "",
        }

    def _resolve_chunk_payload(ident: str) -> dict[str, Any]:
        corpus = context.require_corpus()
        chunk = queries.get_chunk(corpus, ident)
        if chunk is None:
            raise queries.QueryError(
                "chunk_not_found", f"chunk not found: {ident}"
            )
        return {
            "id": chunk.id,
            "handle": f"chunk:{short_id(chunk.id)}",
            "doc_handle": f"doc:{short_id(chunk.doc_id)}",
            "section_path": list(chunk.section_path or []),
            "text": chunk.text,
        }

    def _resolve_figure_payload(doc_short: str, stem: str) -> dict[str, Any]:
        corpus = context.require_corpus()
        ident = f"{doc_short}/{stem}"
        fig = queries.get_figure(corpus, ident)
        if fig is None:
            raise queries.QueryError(
                "figure_not_found", f"figure not found: {ident}"
            )
        return {
            "id": fig["id"],
            "handle": f"figure:{short_id(fig['id'])}",
            "doc_handle": f"doc:{short_id(fig['source_id'])}",
            "caption": fig["caption"],
            "page": fig["page"],
            "path": fig["path"],
            "near_chunk_handles": [
                f"chunk:{short_id(cid)}" for cid in fig["near_chunk_ids"]
            ],
        }

    def _resolve_equation_payload(ident: str) -> dict[str, Any]:
        corpus = context.require_corpus()
        eq = queries.get_equation(corpus, ident)
        if eq is None:
            raise queries.QueryError(
                "equation_not_found", f"equation not found: {ident}"
            )
        return {
            "id": eq["id"],
            "handle": f"equation:{short_id(eq['id'])}",
            "doc_handle": f"doc:{short_id(eq['source_id'])}",
            "latex": eq["latex"],
            "label": eq["label"],
            "kind": eq["kind"],
            "is_chemical": eq["is_chemical"],
        }

    def _resolve_author_payload(ident: str) -> dict[str, Any]:
        corpus = context.require_corpus()
        au = queries.get_author(corpus, ident)
        if au is None:
            raise queries.QueryError(
                "author_not_found", f"author not found: {ident}"
            )
        return {
            "key": au["key"],
            "handle": f"author:{au['key'].replace(' ', '_')}",
            "name": au["name"],
            "h_index": au["h_index"],
            "citation_count": au["citation_count"],
            "n_papers": au["n_papers"],
            "top_coauthors": au["top_coauthors"],
        }

    @srv.resource(
        "wikify://corpus/docs/{ident}",
        mime_type="application/json",
        description="Full document record (id, title, metadata, n_chunks).",
    )
    def doc_resource(ident: str) -> dict[str, Any]:
        return _resolve_doc_payload(ident)

    @srv.resource(
        "wikify://corpus/chunks/{ident}",
        mime_type="application/json",
        description="Full chunk text + section path.",
    )
    def chunk_resource(ident: str) -> dict[str, Any]:
        return _resolve_chunk_payload(ident)

    @srv.resource(
        "wikify://corpus/figures/{doc_short}/{stem}",
        mime_type="application/json",
        description="Figure record: caption, page, on-disk path, near chunks.",
    )
    def figure_resource(doc_short: str, stem: str) -> dict[str, Any]:
        return _resolve_figure_payload(doc_short, stem)

    @srv.resource(
        "wikify://corpus/equations/{ident}",
        mime_type="application/json",
        description="Equation record: latex, label, kind, chemical flag.",
    )
    def equation_resource(ident: str) -> dict[str, Any]:
        return _resolve_equation_payload(ident)

    @srv.resource(
        "wikify://corpus/authors/{ident}",
        mime_type="application/json",
        description=(
            "Author profile: name, h_index, citation_count, n_papers, "
            "top coauthors."
        ),
    )
    def author_resource(ident: str) -> dict[str, Any]:
        return _resolve_author_payload(ident)

    @srv.resource(
        "wikify://schemas/corpus",
        mime_type="application/json",
        description="Self-describing schema of the corpus query surface.",
    )
    def corpus_schema_resource() -> dict[str, Any]:
        return queries.SCHEMA

    return srv
