"""FastMCP server: tool + resource registration for the corpus surface.

Tools: ``context_show`` / ``context_set`` for binding;
``corpus_find`` / ``corpus_traverse`` / ``corpus_show`` /
``corpus_sample`` / ``corpus_schema`` for read-side corpus access, plus
``wiki_find`` / ``wiki_show`` / ``wiki_traverse`` / ``wiki_schema`` for
read-side committed-wiki access.
Resources: ``wikify://corpus/{docs,chunks,figures,equations,authors}/...``
plus ``wikify://schemas/corpus``.

All tools call into :mod:`wikify.corpus.queries` — the same domain
APIs the CLI calls — so behaviour parity is enforced by construction.

Server staleness
----------------
The MCP server caches loaded modules for its process lifetime. After
wikify source edits, the running server keeps serving the old code until
it is restarted. Clients can detect staleness by comparing
``context_show().server_build.git_sha`` against the current HEAD sha
(``git rev-parse --short HEAD``). If the shas differ, restart the server
so it loads the updated modules.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from importlib.metadata import version as _pkg_version
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from ..corpus import queries
from ..corpus.handles import (
    AmbiguousHandleError,
    HandleNotFoundError,
    format_chunk_handles,
    format_handle,
    short_id,
)
from . import context
from .envelope import (
    author_item,
    chunk_item,
    chunk_row_item,
    chunk_uri,
    doc_item,
    doc_row_item,
    equation_item,
    err,
    figure_item,
    ok,
    traverse_row_item,
)


def _capture_git_sha() -> str:
    """Return the short HEAD sha, or ``"unknown"`` if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


# Captured once at module import (i.e. server startup). Intentionally
# frozen for the process lifetime so staleness is detectable.
_SERVER_BUILD: dict[str, str] = {
    "package_version": _pkg_version("wikify"),
    "git_sha": _capture_git_sha(),
    "started_at": datetime.now(timezone.utc).isoformat(),
}


def _enrich_chunk_rows(corpus, rows: list[dict]) -> None:
    """Populate ``preview`` and ``section_path`` on chunk-find rows.

    Mutates *rows* in place. One batch SQL round-trip when the SQLite
    store is available; per-row JSONL lookup as a fallback for
    hand-built fixtures. Existing values on the row are preserved.
    """
    if not rows:
        return
    cids = [str(r.get("id", "")) for r in rows if r.get("id")]
    if not cids:
        return
    try:
        from ..corpus.store.routing import open_store, sqlite_available
    except ImportError:
        return
    if sqlite_available(corpus.root):
        import json as _json
        store = open_store(corpus.root)
        try:
            placeholders = ",".join("?" * len(cids))
            fetched = {
                r["chunk_id"]: r for r in store.con.execute(
                    f"SELECT chunk_id, text, section_path_json, section_type, "
                    f"is_boilerplate "
                    f"FROM chunks WHERE chunk_id IN ({placeholders})",
                    cids,
                )
            }
        finally:
            store.close()
        for r in rows:
            cid = str(r.get("id", ""))
            row = fetched.get(cid)
            if row is None:
                continue
            if "text" not in r:
                r["text"] = row["text"] or ""
            if not r.get("preview"):
                r["preview"] = (row["text"] or "")[:240]
            if "section_path" not in r:
                try:
                    r["section_path"] = _json.loads(row["section_path_json"] or "[]")
                except (TypeError, ValueError):
                    r["section_path"] = []
            if "section_type" not in r:
                r["section_type"] = row["section_type"] or "body"
            if "is_boilerplate" not in r:
                r["is_boilerplate"] = bool(row["is_boilerplate"])
        return
    # Fallback: no sqlite store. Per-chunk JSONL lookup. Used only by
    # hand-built fixtures; production corpora always have sqlite.
    for r in rows:
        cid = str(r.get("id", ""))
        if not cid:
            continue
        chunk = queries.get_chunk(corpus, cid)
        if chunk is None:
            continue
        if "text" not in r:
            r["text"] = chunk.text or ""
        if not r.get("preview"):
            r["preview"] = (chunk.text or "")[:240]
        if "section_path" not in r:
            r["section_path"] = list(chunk.section_path or [])
        if "section_type" not in r:
            r["section_type"] = chunk.section_type or "body"
        if "is_boilerplate" not in r:
            r["is_boilerplate"] = bool(chunk.is_boilerplate)


def _mark_traversal_stubs(corpus, rows: list[dict]) -> None:
    """Flag source-type traversal rows whose doc has no chunks.

    Reference traversal results often contain graph-only stubs (no
    ``documents`` row, no chunks, zero PageRank) -- the agent shouldn't
    waste a ``corpus_show`` call on them. Mark via
    ``_doc_is_stub`` so the envelope item carries ``meta.is_stub=True``;
    rows are kept in the result list by default.
    """
    if not rows:
        return
    for r in rows:
        if r.get("type", "source") not in {"source", "doc", ""}:
            continue
        did = str(r.get("id") or r.get("doc_id") or "")
        if not did:
            continue
        try:
            doc = queries.get_doc(corpus, did)
        except (AmbiguousHandleError, HandleNotFoundError):
            doc = None
        if doc is None or int(doc.n_chunks or 0) == 0:
            r["_doc_is_stub"] = True


def _enrich_doc_rows(corpus, rows: list[dict]) -> None:
    """Attach cheap doc-level triage fields onto search/sample rows.

    Mutates each row to set ``_doc_year`` (from ``Document.metadata``),
    ``_doc_n_chunks`` (real document chunk count, distinct from a
    paper-search row's matched-chunk count), and ``_doc_abstract``.
    Stub docs (no chunks) get ``_doc_is_stub=True`` so traversal
    consumers can flag them without filtering.
    """
    if not rows:
        return
    for r in rows:
        did = str(r.get("doc_id") or r.get("id") or "")
        if not did:
            continue
        try:
            doc = queries.get_doc(corpus, did)
        except (AmbiguousHandleError, HandleNotFoundError):
            continue
        if doc is None:
            continue
        meta = doc.metadata or {}
        if "year" in meta:
            r["_doc_year"] = meta.get("year")
        r["_doc_n_chunks"] = int(doc.n_chunks or 0)
        if doc.abstract:
            r["_doc_abstract"] = doc.abstract
        if int(doc.n_chunks or 0) == 0:
            r["_doc_is_stub"] = True


def _disambiguate_chunk_items(rows: list[dict], items: list[dict]) -> dict[str, str]:
    """Rewrite each item's ``handle`` to a disambiguated chunk handle.

    Two distinct chunks whose short hash collides (boilerplate, repeat
    captions) must not print the same handle. Reuse the existing slash
    grammar from ``format_chunk_handles`` so the output is still
    pipe-safe and resolvable. When a row required disambiguation, the
    item's ``resource_uri`` also gets rewritten to point at the full
    chunk id -- the bare-short URI would otherwise dereference whichever
    chunk the resolver matched first. Returns the chunk_id -> handle
    map so callers (e.g. walk edges) can rewrite their own handle
    references.
    """
    handle_map = format_chunk_handles(
        (str(r.get("id", "")), str(r.get("doc_id", "") or ""))
        for r in rows
    )
    for r, item in zip(rows, items, strict=True):
        cid = str(r.get("id", ""))
        if cid not in handle_map:
            continue
        item["handle"] = handle_map[cid]
        # Caption chunks naturally carry a slash in their full id; that
        # slash is not collision-driven and the URI is unaffected. A
        # slash in the handle on a chunk whose full id has no slash
        # means ``format_chunk_handles`` escalated to disambiguate a
        # hash collision -- the bare-short URI would now dereference
        # whichever colliding chunk the resolver matched first, so
        # rewrite the URI to the unique full chunk id.
        slashy_handle = "/" in handle_map[cid].split(":", 1)[1]
        if slashy_handle and "/" not in cid:
            item["resource_uri"] = f"wikify://corpus/chunks/{cid}"
    return handle_map


def _shape_walk_chunks(
    corpus, chunk_rows
) -> tuple[list[dict], dict[str, str]]:
    """Render walk-result chunks as MCP envelope items.

    Each row carries ``id``, ``doc_id``, ``hop``, optional ``score``.
    Sort by ``hop`` then ``id`` for a deterministic stream, enrich with
    preview + section_path via ``_enrich_chunk_rows``, and emit a
    chunk-row item that preserves doc handle and score; the per-row
    ``hop`` lands under ``meta.hop``. Returns ``(items, handle_map)``
    where ``handle_map`` maps each chunk_id to its disambiguated
    handle so callers can rewrite edge endpoints.
    """
    rows = sorted(chunk_rows, key=lambda r: (r.get("hop", 0), r.get("id", "")))
    _enrich_chunk_rows(corpus, rows)
    items: list[dict] = []
    for r in rows:
        item = chunk_row_item(r, score=r.get("score"))
        meta = item.get("meta") or {}
        meta["hop"] = int(r.get("hop", 0))
        item["meta"] = meta
        items.append(item)
    handle_map = _disambiguate_chunk_items(rows, items)
    return items, handle_map


def _shape_find_items(corpus, result: dict, *,
                      include_text: bool = False) -> list[dict]:
    kind = result["kind"]
    rows = result["rows"]
    scored = result.get("scored")
    if kind == "chunks":
        _enrich_chunk_rows(corpus, rows)
        items = [
            chunk_row_item(
                r,
                score=(r.get("score") if scored else None),
                include_text=include_text,
            )
            for r in rows
        ]
        _disambiguate_chunk_items(rows, items)
        return items
    if kind == "papers":
        _enrich_doc_rows(corpus, rows)
        return [doc_row_item(r) for r in rows]
    if kind == "authors":
        return [author_item(r, in_search_mode=bool(scored)) for r in rows]
    if kind == "docs":
        _enrich_doc_rows(corpus, rows)
        return [doc_row_item(r) for r in rows]
    return []


def _shape_show_item(
    result: dict,
    *,
    section_index: list[dict] | None = None,
    text_segments: list[dict] | None = None,
    body_text: str | None = None,
) -> dict:
    kind = result["handle_kind"]
    data = result["data"]
    if kind == "doc":
        return doc_item(
            data,
            section_index=section_index,
            text_segments=text_segments,
            body_text=body_text,
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


def _flatten_segments(segments: list[dict]) -> str:
    """Join segments into one ordered body string, ``## <section>`` headers inlined.

    Section headers come from the last element of ``section_path`` when
    available; an empty path is rendered as a bare body block. Two
    blank lines separate sections so a downstream Markdown parser
    treats them as distinct.
    """
    parts: list[str] = []
    for seg in segments:
        path = seg.get("section_path") or []
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if path:
            header = path[-1] if len(path) == 1 else " > ".join(path)
            parts.append(f"## {header}\n\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


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
        snap["server_build"] = dict(_SERVER_BUILD)
        corpus = context.get_corpus()
        if corpus is not None:
            try:
                health = queries.check_corpus(corpus, full=False)
                health["rank_metrics"] = dict(queries.SCHEMA["rank_metrics"])
                snap["health"] = health
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
                          field: str = "chunk_text",
                          in_doc: str | None = None,
                          exclude_kinds: list[str] | None = None,
                          include_text: bool = False) -> dict:
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

        ``in_doc`` scopes a chunk search to one document. Accepts any
        doc handle form: ``doc:<short>``, the bare hash suffix, or a
        full id. Bad handles return a structured error.

        ``exclude_kinds`` drops chunks whose ``section_type`` is in
        the list (e.g. ``["references", "acknowledgments"]``). Useful
        for keeping bibliography and acknowledgments paragraphs out
        of content retrieval. Returned chunk rows carry ``meta.kind``
        so callers can see what they got.

        ``include_text=True`` inlines each chunk's full body text on
        its row under ``text`` (chunk rows only; no-op for ``by=paper``
        and ``by=author``). Saves the per-candidate
        ``corpus_show(handle, full=True)`` round trip vetters otherwise
        pay to read a hit's body.

        Paper rows include ``best_chunk_section`` so the agent can tell
        whether a hit came from the abstract vs. references without
        another round-trip.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        resolved_in_doc: str | None = None
        if in_doc is not None:
            ident = in_doc.strip()
            if ident.startswith("doc:"):
                ident = ident[len("doc:"):]
            try:
                resolved_in_doc = queries.resolve_doc_id(corpus, ident)
            except (AmbiguousHandleError, HandleNotFoundError) as exc:
                return err("bad_in_doc", str(exc))
        try:
            result = queries.find(
                corpus, query=query, by=by, rank=rank,
                top_k=top_k, text=text, field=field,
                in_doc=resolved_in_doc,
                exclude_kinds=exclude_kinds,
            )
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        return ok("corpus_find_result",
                  items=_shape_find_items(
                      corpus, result, include_text=include_text,
                  ))

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
        _mark_traversal_stubs(corpus, result["rows"])
        items = [traverse_row_item(r) for r in result["rows"]]
        return ok("corpus_traverse_result", items=items,
                  notes=[f"handle_kind={result['handle_kind']}"])

    @srv.tool()
    async def corpus_show(handle: str, full: bool = False,
                          include_text: bool = False,
                          sections: list[str] | None = None,
                          mode: str = "sections") -> dict:
        """Dereference one handle and return its content.

        For chunk handles, ``full=True`` returns the full chunk text.

        For doc handles, the result always carries the document's
        ``abstract`` (when available) and a ``meta.sections`` index so
        the agent sees the structure without an extra call. Set
        ``include_text=True`` to also include the body in document
        order. ``mode`` controls the shape:

        - ``"sections"`` (default): ``meta.text`` is a list of
          ``{section_path, text, chunk_handles, ord_range}`` segments
          — best when you may want only a subset.
        - ``"full"``: ``meta.body`` is one string with section headers
          inlined (``## <section>``), best for "summarise this paper".

        ``sections`` filters which sections to include
        (case-insensitive substring match; leading numbering like
        ``"I."``, ``"3.2"``, ``"A."`` is stripped before comparison).
        On no match, ``notes`` echo the available section paths so the
        caller can recover.

        Figure / equation / author handles are always returned in full.
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        if mode not in {"sections", "full"}:
            return err(
                "bad_mode",
                f"unknown mode {mode!r}; expected 'sections' | 'full'",
            )
        try:
            result = queries.show(corpus, handle=handle, full=full)
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        except (AmbiguousHandleError, HandleNotFoundError) as exc:
            return _handle_handle_lookup_error(exc)

        section_index: list[dict] | None = None
        text_segments: list[dict] | None = None
        body_text: str | None = None
        notes: list[str] = []
        if result["handle_kind"] == "doc":
            doc = result["data"]
            section_index = queries.doc_section_index(corpus, doc.id)
            if include_text:
                text = queries.read_doc_text(
                    corpus, doc.id, sections=sections,
                )
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
                if mode == "full":
                    body_text = _flatten_segments(text["segments"])
                else:
                    text_segments = text["segments"]
        return ok(
            "corpus_show_result",
            items=[_shape_show_item(
                result,
                section_index=section_index,
                text_segments=text_segments,
                body_text=body_text,
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
        raw_rows: list[dict] = []
        for did in ids:
            m = metrics.get(did, {})
            raw_rows.append({
                "doc_id": did,
                "title": "",
                "citation_count": m.get("citation_count", 0),
                "pagerank": m.get("pagerank", 0.0),
            })
        _enrich_doc_rows(corpus, raw_rows)
        for r in raw_rows:
            did = str(r.get("doc_id", ""))
            doc = queries.get_doc(corpus, did)
            if doc is not None:
                r["title"] = doc.title
        items = [doc_row_item(r) for r in raw_rows]
        return ok("corpus_sample_result", items=items,
                  notes=[f"strategy={strategy}",
                         f"pagerank_weight={pagerank_weight}"])

    @srv.tool()
    async def corpus_similarity_walk(query: str = "",
                                     from_chunk: str | None = None,
                                     depth: int = 2,
                                     top_k: int = 5,
                                     neighbors: int = 3,
                                     threshold: float = 0.65,
                                     rank: str = "all",
                                     cross_doc_only: bool = True) -> dict:
        """Recursive cosine-similarity walk over chunk vectors.

        Two seed modes (mutually exclusive): pass ``query`` to seed via
        ``corpus_find``-style chunk search at hop 0, or pass
        ``from_chunk`` to start from one chunk handle (no search).
        Each hop expands every frontier chunk into up to ``neighbors``
        cosine-similar chunks above ``threshold``. Edges are typed
        ``kind="similar"`` and carry a score; chunks are deduped across
        paths. ``cross_doc_only=True`` (default) drops same-doc
        neighbours -- adjacent paragraphs are usually trivially similar.

        Items are chunk rows with ``meta.hop`` and ``score``. The
        envelope also carries ``seeds`` (list of seed chunk handles)
        and ``edges`` (list of ``{src_chunk, dst_chunk, kind, score,
        hop}`` records with handle-shaped chunk references).
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            out = queries.similarity_walk(
                corpus,
                query=(query or None),
                from_chunk=from_chunk,
                depth=depth,
                top_k=top_k,
                neighbors=neighbors,
                threshold=threshold,
                rank=rank,
                cross_doc_only=cross_doc_only,
            )
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        items, handle_map = _shape_walk_chunks(corpus, out["chunks"].values())
        seeds = [
            handle_map.get(s["id"], format_handle("chunk", s["id"]))
            for s in out["seeds"]
        ]
        edges = [
            {
                "src_chunk": handle_map.get(
                    e["src_chunk"], format_handle("chunk", e["src_chunk"]),
                ),
                "dst_chunk": handle_map.get(
                    e["dst_chunk"], format_handle("chunk", e["dst_chunk"]),
                ),
                "kind": e["kind"],
                "score": e["score"],
                "hop": e["hop"],
            }
            for e in out["edges"]
        ]
        return ok(
            "corpus_similarity_walk_result",
            items=items,
            seeds=seeds,
            edges=edges,
        )

    @srv.tool()
    async def corpus_citation_walk(query: str,
                                   depth: int = 2,
                                   top_k: int = 5,
                                   rank: str = "all") -> dict:
        """Concept-grounded recursive citation walk.

        Hop 0: top-``top_k`` chunks for ``query`` corpus-wide. For each,
        follow ``chunk_citations`` to in-corpus papers and pick that
        paper's best chunk for the same query (scoped to the doc).
        Recurses to ``depth`` hops, deduping chunks across paths.

        Items are chunk rows with ``meta.hop``. The envelope also
        carries ``seeds`` (list of seed chunk handles) and ``edges``
        (list of ``{src_chunk, dst_chunk, dst_doc, marker, hop}``
        records with handle-shaped references).
        """
        try:
            corpus = context.require_corpus()
        except context.ContextError as exc:
            return err("no_corpus_bound", str(exc))
        try:
            out = queries.citation_walk(
                corpus, query=query, depth=depth, top_k=top_k, rank=rank,
            )
        except queries.QueryError as exc:
            return _handle_query_error(exc)
        items, handle_map = _shape_walk_chunks(corpus, out["chunks"].values())
        seeds = [
            handle_map.get(s["id"], format_handle("chunk", s["id"]))
            for s in out["seeds"]
        ]
        edges = [
            {
                "src_chunk": handle_map.get(
                    e["src_chunk"], format_handle("chunk", e["src_chunk"]),
                ),
                "dst_chunk": handle_map.get(
                    e["dst_chunk"], format_handle("chunk", e["dst_chunk"]),
                ),
                "dst_doc": format_handle("doc", e["dst_doc"]),
                "marker": e.get("marker", ""),
                "hop": e["hop"],
            }
            for e in out["edges"]
        ]
        return ok(
            "corpus_citation_walk_result",
            items=items,
            seeds=seeds,
            edges=edges,
        )

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

    # ------------------------------------------------------------- wiki

    def _wiki_page_item(row: dict) -> dict:
        slug = str(row.get("slug") or row.get("id") or "")
        title = str(row.get("title") or slug)
        return {
            "handle": f"page:{slug}",
            "type": "page",
            "title": title,
            "score": row.get("score"),
            "rank": None,
            "resource_uri": f"wikify://wiki/pages/{slug}",
            "preview": str(row.get("snippet") or "")[:240],
            "meta": {
                "kind": row.get("kind", ""),
                "page_id": row.get("page_id") or row.get("id") or "",
                "n_links": row.get("n_links"),
                "n_evidence": row.get("n_evidence"),
                "modes": row.get("modes"),
            },
        }

    def _wiki_category_item(row: dict) -> dict:
        cid = str(row.get("id") or "")
        return {
            "handle": f"category:{cid}",
            "type": "category",
            "title": str(row.get("title") or cid),
            "score": None,
            "rank": None,
            "resource_uri": f"wikify://wiki/categories/{cid}",
            "preview": str(row.get("description") or "")[:240],
            "meta": {
                "n_pages": row.get("n_pages", 0),
                "n_children": row.get("n_children", 0),
                "parent": row.get("parent", ""),
            },
        }

    def _wiki_evidence_item(row: dict) -> dict:
        chunk_id = str(row.get("chunk_id") or "")
        doc_id = str(row.get("doc_id") or "")
        return {
            "handle": f"chunk:{chunk_id}",
            "type": "evidence",
            "title": "",
            "score": None,
            "rank": None,
            "resource_uri": chunk_uri(chunk_id) if chunk_id else "",
            "preview": str(row.get("quote") or "")[:240],
            "meta": {
                "page_id": row.get("page_id", ""),
                "chunk_handle": f"chunk:{chunk_id}" if chunk_id else "",
                "doc_handle": format_handle("doc", doc_id) if doc_id else "",
            },
        }

    def _wiki_item(row: dict) -> dict:
        ntype = row.get("type")
        if ntype == "category":
            return _wiki_category_item(row)
        if ntype == "evidence":
            return _wiki_evidence_item(row)
        return _wiki_page_item(row)

    @srv.tool()
    async def wiki_find(query: str, mode: str = "hybrid",
                        top_k: int = 8) -> dict:
        """Search committed wiki pages in the bound bundle.

        ``mode`` is ``text`` | ``bm25`` | ``semantic`` | ``hybrid``.
        Hybrid searches the wiki SQLite store where available and falls
        back gracefully when vectors are missing.
        """
        try:
            bundle = context.require_bundle()
        except context.ContextError as exc:
            return err("no_bundle_bound", str(exc))
        from ..bundle.wiki import queries as wiki_queries

        try:
            rows = wiki_queries.find(bundle, query, mode=mode, top_k=top_k)
        except ValueError as exc:
            return err("bad_mode", str(exc))
        return ok("wiki_find_result", items=[_wiki_page_item(r) for r in rows])

    @srv.tool()
    async def wiki_show(handle: str, full: bool = False) -> dict:
        """Dereference a committed wiki page handle."""
        try:
            bundle = context.require_bundle()
        except context.ContextError as exc:
            return err("no_bundle_bound", str(exc))
        from ..bundle.wiki import queries as wiki_queries

        handle_clean = handle[len("page:"):] if handle.startswith("page:") else handle
        try:
            info = wiki_queries.show_page(bundle, handle=handle_clean)
        except wiki_queries.AmbiguousSlugError as exc:
            return err("ambiguous_handle", str(exc), matches=exc.matches)
        if info is None:
            return err("page_not_found", f"page not found: {handle}")
        text = str(info["text"])
        item = {
            "handle": f"page:{info['slug']}",
            "type": "page",
            "title": str(info["slug"]),
            "score": None,
            "rank": None,
            "resource_uri": f"wikify://wiki/pages/{info['slug']}",
            "preview": text[:240],
            "meta": {"kind": info["kind"], "path": info["path"]},
        }
        if full:
            item["text"] = text
        return ok("wiki_show_result", items=[item])

    @srv.tool()
    async def wiki_traverse(handle: str, to: str, top_k: int = 0) -> dict:
        """Traverse from a wiki page or category handle."""
        try:
            bundle = context.require_bundle()
        except context.ContextError as exc:
            return err("no_bundle_bound", str(exc))
        from ..bundle.wiki import queries as wiki_queries

        limit = top_k or None
        try:
            if handle.startswith("category:"):
                rows = wiki_queries.traverse_category(
                    bundle,
                    category_id=handle[len("category:"):],
                    relation=to,
                    top_k=limit,
                )
            else:
                handle_clean = handle[len("page:"):] if handle.startswith("page:") else handle
                info = wiki_queries.show_page(bundle, handle=handle_clean)
                if info is None:
                    return err("page_not_found", f"page not found: {handle}")
                rows = wiki_queries.traverse_page(
                    bundle,
                    slug=info["slug"],
                    relation=to,
                    top_k=limit,
                )
        except ValueError as exc:
            return err("bad_relation", str(exc))
        except wiki_queries.AmbiguousSlugError as exc:
            return err("ambiguous_handle", str(exc), matches=exc.matches)
        return ok("wiki_traverse_result", items=[_wiki_item(r) for r in rows])

    @srv.tool()
    async def wiki_schema() -> dict:
        """Describe the committed-wiki query surface."""
        return ok(
            "wiki_schema",
            items=[
                {
                    "find_modes": ["text", "bm25", "semantic", "hybrid"],
                    "page_relations": [
                        "links",
                        "linked-by",
                        "co-evidence",
                        "evidence",
                        "similar",
                        "see-also",
                        "category",
                        "categories",
                    ],
                    "category_relations": ["children", "parent", "pages"],
                }
            ],
        )

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
            "section_type": chunk.section_type or "body",
            "is_boilerplate": bool(chunk.is_boilerplate),
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
