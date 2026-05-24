"""``wikify wiki ...`` — committed wiki layer for wiki bundles.

Subcommands::

    wiki list [articles|people|files] [--run]
    wiki find "<query>" [--run] [--text]
    wiki show <handle> [--run] [--full]
    wiki build indexes [--run]
    wiki check [--run]
    wiki commit <concept> [--run] [--ensure-projections]
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import typer

from ..api import Bundle
from ..bundle.run.lock import LockHeldError
from ..bundle.wiki.commit import CommitGateError, commit_page
from ..bundle.wiki.derived import rebuild_graph, rebuild_index, rebuild_vectors
from ..bundle.wiki.navigation import (
    NavigationError,
    build_navigation_context,
    navigation_is_fresh,
    navigation_path,
    write_navigation,
)
from ..bundle.wiki.queries import (
    AmbiguousSlugError,
    list_articles,
    list_files,
    list_people,
    show_page,
    traverse_category,
    traverse_page,
)
from ..bundle.wiki.queries import (
    find as find_wiki,
)
from ..bundle.wiki.session import WikiSearchSession
from ._format import FormatError, format_row, resolve_format
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error
from ._io import _clean_slug_arg


def _resolve_format_or_error(fmt: str) -> str:
    """Wrap :func:`resolve_format` so unknown values surface as a clean envelope."""
    try:
        return resolve_format(fmt)
    except FormatError as exc:
        cli_error(EXIT_VALIDATION, error="bad_format", message=str(exc))

app = typer.Typer(add_completion=False, help="Committed wiki layer.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
    """Resolve a bundle: explicit ``--run`` > ``WIKIFY_BUNDLE`` env > cwd."""
    import os

    if run_flag is not None:
        try:
            return Bundle.open(run_flag)
        except FileNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))

    env_path = os.environ.get("WIKIFY_BUNDLE")
    if env_path:
        try:
            return Bundle.open(Path(env_path))
        except FileNotFoundError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="bad_wikify_bundle_env",
                message=f"WIKIFY_BUNDLE={env_path!r} is not a bundle: {exc}",
            )

    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except FileNotFoundError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=(
                f"no bundle resolved (cwd={cwd}). Pass --run <bundle>, set "
                f"WIKIFY_BUNDLE, or run from inside a bundle. cause: {exc}"
            ),
        )


# --------------------------------------------------------------- list


list_app = typer.Typer(add_completion=False, help="List wiki handles.")
app.add_typer(list_app, name="list")


@list_app.callback(invoke_without_command=True)
def cmd_list_default(
    ctx: typer.Context,
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    bundle = _resolve_bundle(run)
    items = []
    for slug in list_articles(bundle):
        items.append({"slug": slug, "kind": "article"})
    for slug in list_people(bundle):
        items.append({"slug": slug, "kind": "person"})
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for it in items:
        typer.echo(f"{it['kind']:<8}  {it['slug']}")


@list_app.command("articles")
def cmd_list_articles(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    items = list_articles(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for s in items:
        typer.echo(s)


@list_app.command("people")
def cmd_list_people(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    items = list_people(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for s in items:
        typer.echo(s)


@list_app.command("files")
def cmd_list_files(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    files = list_files(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": files}))
        return
    for f in files:
        typer.echo(f)


# --------------------------------------------------------------- find


@app.command("find")
def cmd_find(
    query: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    top_k: int = typer.Option(20, "--top-k"),
    text: bool = typer.Option(False, "--text"),
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        help="Search mode: text | bm25 | semantic | hybrid.",
    ),
    fmt: str = typer.Option(
        "auto",
        "--format",
        help=(
            "Output format: auto (compact for TTY / quiet for pipe), "
            "quiet (handles only), compact (tab-separated columns), json."
        ),
    ),
) -> None:
    """Search committed pages.

    Compact output columns: ``kind \\t page-handle \\t snippet``.
    """
    bundle = _resolve_bundle(run)
    if text:
        mode = "text"
    try:
        hits = find_wiki(bundle, query, mode=mode, top_k=top_k)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_find_mode", message=str(exc))
    fmt_resolved = _resolve_format_or_error(fmt)
    if fmt_resolved == "json":
        typer.echo(json.dumps({"ok": True, "mode": mode, "items": hits}))
        return
    if fmt_resolved == "quiet":
        for h in hits:
            typer.echo(f"page:{h['slug']}")
        return
    for h in hits:
        typer.echo(format_row([h["kind"], f"page:{h['slug']}", h["snippet"]]))


# --------------------------------------------------------------- show


@app.command("show")
def cmd_show(
    handle: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    handle_clean = handle[len("page:"):] if handle.startswith("page:") else handle
    try:
        info = show_page(bundle, handle=handle_clean)
    except AmbiguousSlugError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="ambiguous_handle",
            message=str(exc),
            matches=exc.matches,
        )
    if info is None:
        cli_error(EXIT_VALIDATION, error="page_not_found", handle=handle)
    if fmt == "json":
        body_payload = info["text"] if full else info["text"][:500]
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "slug": info["slug"],
                    "kind": info["kind"],
                    "path": info["path"],
                    "text": body_payload,
                }
            )
        )
        return
    typer.echo(f"slug:  {info['slug']}")
    typer.echo(f"kind:  {info['kind']}")
    typer.echo(f"path:  {info['path']}")
    typer.echo("---")
    typer.echo(info["text"] if full else info["text"][:500])


# --------------------------------------------------------------- build


@app.command("build")
def cmd_build(
    kind: str = typer.Argument(..., help="indexes | graph | vectors"),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Rebuild a derived projection.

    Three kinds:

    - ``indexes``  — derived/index.json (page list)
    - ``graph``    — derived/graph.json (cite-edge wiki graph)
    - ``vectors``  — derived/vectors.npz (per-page embeddings)
    """
    bundle = _resolve_bundle(run)
    if kind == "indexes":
        p = rebuild_index(bundle)
    elif kind == "graph":
        p = rebuild_graph(bundle)
    elif kind == "vectors":
        p = rebuild_vectors(bundle)
    else:
        cli_error(
            EXIT_VALIDATION,
            error="bad_build_kind",
            message=f"`wiki build {kind}` not recognised; use indexes|graph|vectors",
        )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "path": str(p)}))
        return
    typer.echo(f"{kind}: {p}")


@app.command("navigation-context")
def cmd_navigation_context(
    run: Path | None = typer.Option(None, "--run"),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Destination JSON path. Defaults to derived/navigation_context.json.",
    ),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Write page metadata for an organizer agent."""
    bundle = _resolve_bundle(run)
    payload = build_navigation_context(bundle)
    target = out if out is not None else bundle.derived_dir / "navigation_context.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "path": str(target),
                    "pages": len(payload.get("pages", [])),
                }
            )
        )
        return
    typer.echo(f"navigation_context: {target}")
    typer.echo(f"pages: {len(payload.get('pages', []))}")


@app.command("apply-navigation")
def cmd_apply_navigation(
    path: Path = typer.Argument(..., help="Agent-authored navigation JSON."),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Validate and persist derived/navigation.json."""
    bundle = _resolve_bundle(run)
    if not path.is_file():
        cli_error(EXIT_VALIDATION, error="navigation_not_found", path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="bad_navigation_json",
            message=f"{path} is not valid JSON: {exc}",
        )
    try:
        written = write_navigation(bundle, payload)
    except NavigationError as exc:
        cli_error(EXIT_VALIDATION, error="bad_navigation", message=str(exc))
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "path": str(written)}))
        return
    typer.echo(f"navigation: {written}")


# --------------------------------------------------------------- check


@app.command("check")
def cmd_check(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Sanity-check the committed wiki: page count, derived/index.json freshness."""
    bundle = _resolve_bundle(run)
    n_articles = len(list_articles(bundle))
    n_people = len(list_people(bundle))
    has_index = bundle.derived_index_path.exists()
    nav_path = navigation_path(bundle)
    has_navigation = nav_path.exists()
    navigation_fresh = navigation_is_fresh(bundle)
    summary = {
        "ok": True,
        "articles": n_articles,
        "people": n_people,
        "has_derived_index": has_index,
        "has_navigation": has_navigation,
        "navigation_fresh": navigation_fresh,
    }
    if fmt == "json":
        typer.echo(json.dumps(summary))
        return
    typer.echo(f"articles:           {n_articles}")
    typer.echo(f"people:             {n_people}")
    typer.echo(f"derived/index.json: {has_index}")
    typer.echo(f"navigation.json:    {has_navigation}")
    typer.echo(f"navigation fresh:   {navigation_fresh}")


# --------------------------------------------------------------- commit


@app.command("commit")
def cmd_commit(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    ensure_projections: bool = typer.Option(False, "--ensure-projections"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Promote a validated response to wiki/articles/<slug>.md or wiki/people/<slug>.md."""
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    try:
        result = commit_page(
            bundle, slug=concept, ensure_projections=ensure_projections
        )
    except CommitGateError as exc:
        cli_error(EXIT_VALIDATION, error="commit_gate", message=str(exc))
    except LockHeldError as exc:
        cli_error(
            EXIT_LOCK_HELD,
            error="lock_held",
            message=str(exc),
            owner=exc.owner,
            acquired_at=exc.acquired_at,
        )
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "page_id": result.page_id,
                    "kind": result.kind,
                    "slug": result.slug,
                    "path": str(result.page_path.relative_to(bundle.root)).replace("\\", "/"),
                }
            )
        )
        return
    typer.echo(f"committed {result.page_id} -> {result.page_path}")


# --------------------------------------------------------------- schema


_WIKI_SCHEMA: dict = {
    "node_types": {
        "page": "A committed wiki page (article or person). Handle: page:<slug>.",
        "evidence": "An evidence entry attached to a page (chunk_id + doc_id + quote).",
        "category": "A navigation category/group. Handle: category:<id>.",
    },
    "edge_kinds": [
        "LINKS_TO",       # page -> page
        "CO_EVIDENCE",    # page <-> page (shared evidence doc_id)
        "HAS_EVIDENCE",   # page -> evidence
        "SIMILAR",        # page <-> page (cosine over body embeddings)
    ],
    "traverse_relations": {
        "page": [
            "links",
            "linked-by",
            "co-evidence",
            "evidence",
            "similar",
            "see-also",
            "category",
            "categories",
        ],
        "category": ["children", "parent", "pages"],
    },
    "rank_metrics": {
        "page": ["n_links", "n_evidence"],
    },
    "find_modes": {
        "text": "Literal substring grep over committed page markdown.",
        "bm25": "FTS5 BM25 over wiki.db pages.",
        "semantic": "Cosine search over derived/vectors.npz page embeddings.",
        "hybrid": "RRF fusion over BM25 and semantic search (default).",
        "--text": "Backward-compatible alias for --mode text.",
    },
    "formats": ["auto", "quiet", "compact", "json"],
    "handle_resolution": (
        "Slugs are natural Wikipedia-style titles. Exact match wins; "
        "case-insensitive unique prefix is also accepted. Relative paths "
        "like 'wiki/articles/<slug>.md' work too."
    ),
}


@app.command("schema")
def cmd_schema(
    fmt: str = typer.Option(
        "text", "--format", help="Output format: text | json."
    ),
) -> None:
    """Self-describe the wiki CLI surface: nodes, edges, relations, metrics."""
    if fmt == "json":
        typer.echo(json.dumps(_WIKI_SCHEMA, indent=2))
        return
    typer.echo("Node types:")
    for k, v in _WIKI_SCHEMA["node_types"].items():
        typer.echo(f"  {k:10s}  {v}")
    typer.echo("")
    typer.echo("Edge kinds:")
    for kind in _WIKI_SCHEMA["edge_kinds"]:
        typer.echo(f"  {kind}")
    typer.echo("")
    typer.echo("Traverse relations (wiki traverse <handle> --to <relation>):")
    for handle_kind, rels in _WIKI_SCHEMA["traverse_relations"].items():
        typer.echo(f"  {handle_kind+':':<8s} {' | '.join(rels)}")
    typer.echo("")
    typer.echo("Rank metrics:")
    for over, metrics in _WIKI_SCHEMA["rank_metrics"].items():
        typer.echo(f"  over {over+'s:':<8s} {' | '.join(metrics)}")
    typer.echo("")
    typer.echo("find modes:")
    for k, v in _WIKI_SCHEMA["find_modes"].items():
        typer.echo(f"  {k:14s} {v}")
    typer.echo("")
    typer.echo(f"Formats: {' | '.join(_WIKI_SCHEMA['formats'])}")
    typer.echo("")
    typer.echo(f"Handles: {_WIKI_SCHEMA['handle_resolution']}")


# --------------------------------------------------------------- traverse


_PAGE_RELATIONS = {
    "links",
    "linked-by",
    "co-evidence",
    "evidence",
    "similar",
    "see-also",
    "category",
    "categories",
}
_CATEGORY_RELATIONS = {"children", "parent", "pages"}
_TRAVERSE_RELATIONS = _PAGE_RELATIONS | _CATEGORY_RELATIONS


@app.command("traverse")
def cmd_traverse(
    handle: str = typer.Argument(..., help="page:<slug> or <slug> (exact or unique prefix)"),
    run: Path | None = typer.Option(None, "--run"),
    to: str = typer.Option(
        ...,
        "--to",
        help=(
            "Relation: links | linked-by | co-evidence | evidence | similar | "
            "see-also | category | categories | children | parent | pages. "
            "evidence emits chunk handles for the corpus."
        ),
    ),
    rank: str = typer.Option(
        "",
        "--rank",
        help="Optional rank for page results: n_links | n_evidence.",
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
) -> None:
    """Traverse one hop from a wiki page handle.

    Page-typed relations (``links``, ``linked-by``, ``co-evidence``)
    emit ``page:<slug>`` handles. The ``evidence`` relation emits
    ``chunk:<id>`` handles so the result pipes into ``corpus show`` or
    ``corpus traverse``.

    Compact output columns:

    - page rows: ``links=N \\t ev=N \\t page-handle \\t title``
      where ``links`` is outgoing-link count and ``ev`` is evidence
      count for the page.
    - evidence rows: ``chunk-handle \\t doc-handle \\t quote``.
    """
    bundle = _resolve_bundle(run)
    fmt_resolved = _resolve_format_or_error(fmt)
    if to not in _TRAVERSE_RELATIONS:
        cli_error(
            EXIT_VALIDATION,
            error="bad_relation",
            message=(
                f"unknown wiki relation {to!r}; expected "
                f"{' | '.join(sorted(_TRAVERSE_RELATIONS))}"
            ),
        )

    if handle.startswith("category:"):
        category_id = handle[len("category:"):]
        if to not in _CATEGORY_RELATIONS:
            cli_error(
                EXIT_VALIDATION,
                error="bad_relation",
                message=(
                    f"category handles support "
                    f"{' | '.join(sorted(_CATEGORY_RELATIONS))}; got {to!r}"
                ),
            )
        try:
            rows = traverse_category(
                bundle,
                category_id=category_id,
                relation=to,
                top_k=(top_k if top_k > 0 else None),
            )
        except ValueError as exc:
            cli_error(EXIT_VALIDATION, error="bad_relation", message=str(exc))
        _emit_wiki_traverse_rows(rows, fmt=fmt_resolved)
        return

    if to in _CATEGORY_RELATIONS:
        cli_error(
            EXIT_VALIDATION,
            error="bad_relation",
            message=f"{to!r} requires a category:<id> handle",
        )

    handle_clean = handle[len("page:"):] if handle.startswith("page:") else handle
    try:
        info = show_page(bundle, handle=handle_clean)
    except AmbiguousSlugError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="ambiguous_handle",
            message=str(exc),
            matches=exc.matches,
        )
    if info is None:
        cli_error(EXIT_VALIDATION, error="page_not_found", handle=handle)
    slug = info["slug"]

    try:
        rows = traverse_page(
            bundle,
            slug=slug,
            relation=to,
            rank=(rank or None),
            top_k=(top_k if top_k > 0 else None),
        )
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_rank", message=str(exc))

    _emit_wiki_traverse_rows(rows, fmt=fmt_resolved)


def _emit_wiki_traverse_rows(rows: list[dict], *, fmt: str) -> None:
    if fmt == "json":
        items: list[dict] = []
        for r in rows:
            ntype = r.get("type", "")
            if ntype == "page":
                items.append({**r, "handle": f"page:{r.get('slug', r.get('id', ''))}"})
            elif ntype == "evidence":
                items.append({**r, "handle": f"chunk:{r.get('chunk_id', '')}"})
            elif ntype == "category":
                items.append({**r, "handle": f"category:{r.get('id', '')}"})
            else:
                items.append(r)
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    if fmt == "quiet":
        for r in rows:
            ntype = r.get("type", "")
            if ntype == "page":
                typer.echo(f"page:{r.get('slug', r.get('id', ''))}")
            elif ntype == "evidence":
                cid = str(r.get("chunk_id", ""))
                if cid:
                    typer.echo(f"chunk:{cid}")
            elif ntype == "category":
                cid = str(r.get("id", ""))
                if cid:
                    typer.echo(f"category:{cid}")
        return
    for r in rows:
        ntype = r.get("type", "")
        if ntype == "page":
            slug = str(r.get("slug", r.get("id", "")))
            typer.echo(
                format_row([
                    f"links={r.get('n_links', 0)}",
                    f"ev={r.get('n_evidence', 0)}",
                    f"page:{slug}",
                    str(r.get("title", "") or ""),
                ])
            )
        elif ntype == "evidence":
            cid = str(r.get("chunk_id", ""))
            did = str(r.get("doc_id", ""))
            quote = str(r.get("quote", "") or "").replace("\n", " ")[:120]
            typer.echo(format_row([f"chunk:{cid}", f"doc:{did}", quote]))
        elif ntype == "category":
            cid = str(r.get("id", ""))
            typer.echo(
                format_row([
                    f"children={r.get('n_children', 0)}",
                    f"pages={r.get('n_pages', 0)}",
                    f"category:{cid}",
                    str(r.get("title", "") or ""),
                ])
            )


# --------------------------------------------------------------- repl


class WikiReplExitError(Exception):
    """Signal a clean interactive-session exit."""


class WikiReplError(Exception):
    """User-facing wiki REPL command error."""


def _pop_wiki_flag(tokens: list[str], name: str) -> bool:
    if name not in tokens:
        return False
    tokens.remove(name)
    return True


def _pop_wiki_int_option(tokens: list[str], name: str, default: int) -> int:
    if name not in tokens:
        return default
    idx = tokens.index(name)
    try:
        raw = tokens[idx + 1]
    except IndexError as exc:
        raise WikiReplError(f"{name} requires an integer value") from exc
    del tokens[idx : idx + 2]
    try:
        return int(raw)
    except ValueError as exc:
        raise WikiReplError(f"{name} requires an integer value") from exc


def _pop_wiki_key_int(tokens: list[str], key: str, default: int) -> int:
    prefix = f"{key}="
    for token in list(tokens):
        if not token.startswith(prefix):
            continue
        tokens.remove(token)
        raw = token[len(prefix):]
        try:
            return int(raw)
        except ValueError as exc:
            raise WikiReplError(f"{key}= requires an integer value") from exc
    return default


def _wiki_repl_help() -> list[str]:
    return [
        "commands:",
        "  find [--top-k N|top=N] <query>",
        "  show <slug|wiki/articles/file.md> [--full]",
        "  list [pages|articles|people|files]",
        "  help",
        "  exit",
    ]


def _render_wiki_hits(hits: list[dict]) -> list[str]:
    return [
        f"{hit['kind']:<8}  {hit['slug']:<32}  {hit['snippet']}"
        for hit in hits
    ]


def _render_wiki_page(info: dict, *, full: bool) -> list[str]:
    return [
        f"slug:  {info['slug']}",
        f"kind:  {info['kind']}",
        f"path:  {info['path']}",
        "---",
        info["text"] if full else info["text"][:500],
    ]


def _run_wiki_repl_line(session: WikiSearchSession, line: str) -> list[str]:
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        raise WikiReplError(str(exc)) from exc
    if not tokens:
        return []

    cmd, args = tokens[0], tokens[1:]
    if cmd in {"exit", "quit"}:
        raise WikiReplExitError
    if cmd == "help":
        return _wiki_repl_help()

    if cmd == "find":
        top_k = _pop_wiki_int_option(
            args, "--top-k", _pop_wiki_key_int(args, "top", 20)
        )
        _pop_wiki_flag(args, "--text")  # Accepted for parity with one-shot CLI.
        if not args:
            raise WikiReplError("find requires a query")
        hits = session.find_text(" ".join(args), top_k=top_k)
        return _render_wiki_hits(hits)

    if cmd == "show":
        full = _pop_wiki_flag(args, "--full") or _pop_wiki_flag(args, "full")
        if len(args) != 1:
            raise WikiReplError("show requires exactly one handle")
        info = session.show(args[0])
        if info is None:
            raise WikiReplError(f"page not found: {args[0]}")
        return _render_wiki_page(info, full=full)

    if cmd == "list":
        kind = args[0] if args else "pages"
        if kind == "pages":
            return [
                f"{item['kind']:<8}  {item['slug']}"
                for item in session.list_pages()
            ]
        if kind == "articles":
            return session.list_articles()
        if kind == "people":
            return session.list_people()
        if kind == "files":
            return session.list_files()
        raise WikiReplError("list supports pages, articles, people, or files")

    raise WikiReplError(f"unknown command: {cmd}; type help")


@app.command("repl")
def cmd_repl(
    run: Path | None = typer.Option(None, "--run"),
    prompt: str = typer.Option("wikify-wiki> ", "--prompt"),
) -> None:
    """Open a line-oriented committed-wiki query session."""
    bundle = _resolve_bundle(run)
    session = WikiSearchSession(bundle)
    typer.echo(f"ready bundle={bundle.root} pages={session.n_pages}")
    while True:
        try:
            line = input(prompt)
        except EOFError:
            typer.echo("")
            break
        try:
            lines = _run_wiki_repl_line(session, line)
        except WikiReplExitError:
            break
        except WikiReplError as exc:
            typer.echo(f"error: {exc}", err=True)
            continue
        for out in lines:
            typer.echo(out)


__all__ = ["app"]
