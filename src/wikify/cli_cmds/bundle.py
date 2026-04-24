"""wikify bundle ... — promote validated responses into canonical bundle files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from ..distill.write_runner import rebuild_wiki_graph
from ..models import WikiPage
from ..paths import BundlePaths
from ..schema import WriteResponse
from ..session import (
    SessionLockHeldError,
    apply_merge_patch,
    load_session,
    save_session,
    session_lock,
    touch,
)
from ..store.wiki_bundle import load_bundle
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index

app = typer.Typer(add_completion=False, help="Promote validated drafts into bundle artifacts.")


def _cli_owner(override: str | None) -> str:
    return override or f"wikify-cli/pid-{os.getpid()}"


@app.command("commit-page")
def cmd_commit_page(
    session_path: Path = typer.Option(..., "--session"),
    response: Path = typer.Option(..., "--response", help="Path to response-<page_id>.json."),
    validation: Path | None = typer.Option(
        None,
        "--validation",
        help=(
            "Path to validation-<page_id>.json. If provided, commit is rejected "
            "when the verdict has ok=false."
        ),
    ),
    owner: str | None = typer.Option(None, "--owner"),
) -> None:
    """Promote a validated WriteResponse into pages/<id>.md and update the session."""
    response_data = json.loads(response.read_text(encoding="utf-8"))
    parsed = WriteResponse.model_validate(response_data)

    if validation is not None:
        verdict = json.loads(validation.read_text(encoding="utf-8"))
        if not verdict.get("ok", False):
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "validation_failed",
                        "verdict_path": str(validation),
                        "errors": verdict.get("errors", []),
                    }
                ),
                err=True,
            )
            raise typer.Exit(code=1)

    session = load_session(session_path)
    bundle_paths = BundlePaths(Path(session.bundle_root))
    bundle_paths.ensure()

    # Acquire the lock BEFORE any canonical mutation. Every disk write
    # (page file, index, wiki graph, session.json) happens under the lock
    # so a lock_held failure leaves no partial state.
    try:
        with session_lock(session_path, owner=_cli_owner(owner)):
            # Re-read session state under the lock so we patch the latest.
            fresh = load_session(session_path)
            page_entry = next(
                (p for p in fresh.pages if p.page_id == parsed.page_id), None
            )
            kind = parsed.page_kind or "article"
            page = WikiPage(
                id=parsed.page_id,
                kind=kind,  # type: ignore[arg-type]
                title=parsed.page_id,
                aliases=[],
                body_markdown=parsed.body_markdown,
                evidence=[],
                provenance={
                    "session_id": fresh.session_id,
                    "strategy": fresh.strategy,
                    "committed_via": "wikify bundle commit-page",
                },
            )
            page_path = write_page_file(bundle_paths, page)

            # Rebuild indices over ALL committed pages on disk — keeps
            # _index.json / _wiki_graph.json in sync with the bundle
            # contents, not just the page we just wrote. Convert the
            # on-disk Page objects (body_clean only) into WikiPages with
            # full body_markdown by reparsing each file's original body.
            loaded = load_bundle(bundle_paths.root)
            wiki_pages = [_page_to_wiki_page(p) for p in loaded.pages]
            build_index(bundle_paths, wiki_pages).save()
            rebuild_wiki_graph(bundle_paths, wiki_pages)

            # Update session: mark this page committed.
            new_pages = [p.model_dump(mode="json") for p in fresh.pages]
            if page_entry is None:
                new_pages.append(
                    {
                        "page_id": parsed.page_id,
                        "status": "committed",
                        "draft_path": None,
                        "validation_path": (str(validation) if validation else None),
                    }
                )
            else:
                for entry in new_pages:
                    if entry["page_id"] == parsed.page_id:
                        entry["status"] = "committed"
                        if validation is not None:
                            entry["validation_path"] = str(validation)
                        break
            updated = apply_merge_patch(fresh, {"pages": new_pages})
            save_session(session_path, touch(updated))
    except SessionLockHeldError as exc:
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "error": "lock_held",
                    "owner": exc.owner,
                    "acquired_at": exc.acquired_at,
                }
            ),
            err=True,
        )
        raise typer.Exit(code=2) from exc

    typer.echo(
        json.dumps(
            {
                "ok": True,
                "page_path": str(page_path),
                "page_id": parsed.page_id,
                "index_path": str(bundle_paths.root / "_index.json"),
                "graph_path": str(bundle_paths.graph_path),
            }
        )
    )


def _page_to_wiki_page(page) -> WikiPage:
    """Convert a store.wiki_bundle.Page into a WikiPage for index + graph rebuild.

    body_markdown is set to the page's body_clean (frontmatter + evidence
    already stripped). For index + wiki-graph purposes this is the prose
    content; exact body preservation is not required here.
    """
    return WikiPage(
        id=page.id,
        kind=page.kind,  # type: ignore[arg-type]
        title=page.title,
        aliases=list(page.aliases),
        body_markdown=page.body_clean,
        evidence=list(page.evidence),
        links=list(page.links),
        equations=list(page.equations),
        provenance=dict(page.provenance),
    )


__all__ = ["app"]
