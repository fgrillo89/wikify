"""wikify bundle ... — promote validated responses into canonical bundle files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

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
from ..store.wiki_files import write_page as write_page_file

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

    # Find the matching session page entry; infer title/aliases from it if
    # possible. We keep this minimal — the skill is expected to have
    # populated aliases via the draft step.
    page_entry = next((p for p in session.pages if p.page_id == parsed.page_id), None)
    title = parsed.page_id  # title == page_id is the wikify convention
    kind = parsed.page_kind or "article"

    page = WikiPage(
        id=parsed.page_id,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        aliases=[],
        body_markdown=parsed.body_markdown,
        evidence=[],
        provenance={
            "session_id": session.session_id,
            "strategy": session.strategy,
            "committed_via": "wikify bundle commit-page",
        },
    )
    page_path = write_page_file(bundle_paths, page)

    # Update the session: mark this page committed.
    new_pages = [p.model_dump(mode="json") for p in session.pages]
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

    try:
        with session_lock(session_path, owner=_cli_owner(owner)):
            fresh = load_session(session_path)
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
            }
        )
    )


__all__ = ["app"]
