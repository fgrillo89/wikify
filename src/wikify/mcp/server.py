"""FastMCP server: tool + resource registration for the corpus surface.

Tools: ``context_show`` / ``context_set`` for binding;
``corpus_find`` / ``corpus_traverse`` / ``corpus_show`` /
``corpus_sample`` / ``corpus_schema`` for read-side corpus access.
Resources: ``wikify://corpus/{docs,chunks,figures,equations,authors}/...``
plus ``wikify://schemas/corpus``.

All tools call into :mod:`wikify.corpus.queries` — the same domain
APIs the CLI calls — so behaviour parity is enforced by construction.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP, Image

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


def _shape_show_item(
    result: dict,
    *,
    section_index: list[dict] | None = None,
    text_segments: list[dict] | None = None,
) -> dict:
    kind = result["handle_kind"]
    data = result["data"]
    if kind == "doc":
        return doc_item(
            data,
            section_index=section_index,
            text_segments=text_segments,
        )
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
        """Show the current corpus/bundle binding plus a corpus health summary.

        When a corpus is bound, the snapshot also carries doc/chunk
        counts, derived-artifact presence, and detected field. This
        folds in the use case of ``wikify corpus check`` so the agent
        does not need a separate tool call to verify the binding is
        usable.
        """
        snap = context.snapshot()
        corpus = context.get_corpus()
        if corpus is not None:
            try:
                snap["health"] = queries.check_corpus(corpus, full=False)
            except Exception as exc:
                snap["health_error"] = str(exc)
        return ok("context", items=[snap])

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
                          text: bool = False,
                          field: str = "chunk_text") -> dict:
        """Search the corpus.

        ``by`` is ``chunk`` | ``paper`` | ``author``. ``rank`` is
        ``semantic`` (default) or a graph metric (``citation_count``,
        ``pagerank``, ``h_index``, ``n_papers``). With no query and a
        graph metric, the whole population is ranked.

        ``text=True`` switches the chunk match from semantic to literal
        substring grep. ``field='title'`` runs a literal substring
        search over ``Document.title`` instead — use with ``by='paper'``
        for "papers whose title mentions X" (vs. "papers whose body
        mentions X" via the default ``field='chunk_text'``).

        Paper rows include ``best_chunk_section`` so the agent can tell
        whether a hit came from the abstract vs. references without
        another round-trip.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            result = queries.find(
                corpus, query=query, by=by, rank=rank,
                top_k=top_k, text=text, field=field,
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
    async def corpus_show(handle: str, full: bool = False,
                          include_text: bool = False,
                          sections: list[str] | None = None) -> dict:
        """Dereference one handle and return its content.

        For chunk handles, ``full=True`` returns the full chunk text.

        For doc handles, the result always carries the document's
        ``abstract`` (when available) and a ``meta.sections`` index so
        the agent sees the structure without an extra call. Set
        ``include_text=True`` to also include the body, grouped by
        section in document order, under ``meta.text``. ``sections``
        filters which sections to include (case-insensitive
        prefix-or-substring match against ``section_path[0]``); leave
        unset to include everything when ``include_text`` is on.

        Figure / equation / author handles are always returned in full.
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

        section_index: list[dict] | None = None
        text_segments: list[dict] | None = None
        notes: list[str] = []
        if result["handle_kind"] == "doc":
            doc = result["data"]
            section_index = queries.doc_section_index(corpus, doc.id)
            if include_text:
                text = queries.read_doc_text(
                    corpus, doc.id, sections=sections,
                )
                text_segments = text["segments"]
                if sections:
                    matched = text["matched_section_paths"]
                    if not matched:
                        available = " | ".join(
                            " > ".join(p) for p in text["available_section_paths"]
                        ) or "(none)"
                        notes.append(
                            "section filter matched no sections; "
                            f"available: {available}"
                        )
                    else:
                        notes.append(
                            "matched sections: "
                            + " | ".join(" > ".join(p) for p in matched)
                        )
        return ok(
            "corpus_show_result",
            items=[_shape_show_item(
                result,
                section_index=section_index,
                text_segments=text_segments,
            )],
            notes=notes or None,
        )

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

    @srv.tool()
    async def corpus_image(handle: str):
        """Fetch the binary image for a ``figure:`` handle.

        Returns the raw image bytes as an MCP ImageContent block, so
        the model can see the figure during reasoning. The
        ``corpus_show`` tool returns figure metadata (caption, page,
        path) — call this after picking a handle to also pull the
        pixels.

        On error returns a regular envelope (``ok=False, code=...``)
        instead of raising, so the agent gets a structured failure.
        """
        from pathlib import Path as _Path

        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            kind, ident = queries.parse_handle(handle)
        except ValueError as exc:
            return err("bad_handle", str(exc))
        if kind != "figure":
            return err(
                "bad_handle_kind",
                f"corpus_image expects a figure: handle; got {kind!r}",
            )
        try:
            fig = queries.get_figure(corpus, ident)
        except (AmbiguousHandleError, HandleNotFoundError) as exc:
            return _handle_handle_lookup_error(exc)
        if fig is None:
            return err("figure_not_found", f"figure not found: {ident}")
        rel = (fig.get("path") or "").replace("\\", "/")
        if not rel:
            return err(
                "image_path_missing",
                f"figure {ident} has no on-disk path",
            )
        path = _Path(corpus.root) / rel
        if not path.is_file():
            return err(
                "image_missing_on_disk",
                f"image file does not exist: {path}",
            )
        return Image(path=path)

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
