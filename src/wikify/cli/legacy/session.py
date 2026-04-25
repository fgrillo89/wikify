"""wikify session ... — create, inspect, mutate, checkpoint, close the run session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from ...api import LegacyBundle
from ...session import (
    SchemaVersionMismatchError,
    UnknownRoleError,
    acquire_lock,
    apply_merge_patch,
    checkpoint_session,
    init_session,
    load_session,
    read_lock,
    release_lock,
    save_session,
    session_lock,
    touch,
    write_run_snapshot,
)
from .._helpers import cli_error, cli_owner, handle_lock_held

app = typer.Typer(add_completion=False, help="Durable session file for skill workflows.")


@app.command("init")
def cmd_init(
    bundle: Path = typer.Option(..., "--bundle", help="Path to the wiki bundle root."),
    corpus: Path = typer.Option(..., "--corpus", help="Path to the ingested corpus root."),
    strategy: str = typer.Option("baseline", "--strategy"),
    budget_target: int = typer.Option(
        0, "--budget-target", help="Target budget in haiku-equivalent tokens."
    ),
) -> None:
    """Create a new session at <bundle>/_session/session.json."""
    session = init_session(
        bundle_root=bundle,
        corpus_root=corpus,
        strategy=strategy,
        budget_target_haiku_eq=budget_target,
    )
    paths = LegacyBundle(bundle)
    if paths.session_path.exists():
        raise typer.BadParameter(
            f"session already exists at {paths.session_path}; refusing to overwrite"
        )
    save_session(paths.session_path, session)
    typer.echo(
        json.dumps(
            {"session_path": str(paths.session_path), "schema_version": session.schema_version}
        )
    )


@app.command("show")
def cmd_show(
    session_path: Path = typer.Option(..., "--session"),
    full: bool = typer.Option(False, "--full", help="Emit the full session document."),
) -> None:
    """Print the session JSON. Token-light by default; --full for the whole document."""
    session = load_session(session_path)
    payload = session.model_dump(mode="json")
    if not full:
        pages = payload.get("pages", []) or []
        payload = {
            "session_id": payload["session_id"],
            "strategy": payload["strategy"],
            "status": payload["status"],
            "schema_version": payload["schema_version"],
            "updated_at": payload["updated_at"],
            "budget": payload["budget"],
            "stages": {
                k: v["status"] for k, v in (payload.get("stages") or {}).items()
            },
            "page_counts": {
                "total": len(pages),
                **_count_by_status(pages),
            },
        }
    typer.echo(json.dumps(payload, indent=2))


@app.command("update")
def cmd_update(
    session_path: Path = typer.Option(..., "--session"),
    patch: str | None = typer.Option(
        None,
        "--patch",
        help="JSON Merge Patch. If '-' or omitted, read from stdin.",
    ),
    owner: str | None = typer.Option(None, "--owner", help="Override the lock owner string."),
) -> None:
    """Apply a JSON Merge Patch (RFC 7396) to the session. Holds the session lock."""
    if patch is None or patch == "-":
        patch_text = sys.stdin.read()
    else:
        patch_text = patch
    patch_data = json.loads(patch_text)
    with handle_lock_held():
        with session_lock(session_path, owner=cli_owner(owner)):
            session = load_session(session_path)
            updated = apply_merge_patch(session, patch_data)
            updated = touch(updated)
            save_session(session_path, updated)
    typer.echo(json.dumps({"ok": True, "updated_at": updated.updated_at}))


@app.command("checkpoint")
def cmd_checkpoint(
    session_path: Path = typer.Option(..., "--session"),
    label: str = typer.Option(..., "--label"),
) -> None:
    """Snapshot the current session to <bundle>/_session/checkpoints/<label>.json."""
    dest = checkpoint_session(session_path, label)
    typer.echo(json.dumps({"checkpoint_path": str(dest)}))


@app.command("close")
def cmd_close(
    session_path: Path = typer.Option(..., "--session"),
    status: str = typer.Option(
        "completed",
        "--status",
        help="Terminal status: completed | failed | abandoned.",
    ),
    owner: str | None = typer.Option(None, "--owner", help="Override the lock owner string."),
) -> None:
    """Mark the session finished. Does not delete the file. Holds the session lock.

    Each terminal status is persisted distinctly so downstream stop-reason
    analysis can tell a successful run from a failure or an operator
    abandonment.
    """
    if status not in {"completed", "failed", "abandoned"}:
        raise typer.BadParameter(f"invalid status: {status}")
    try:
        with handle_lock_held():
            with session_lock(session_path, owner=cli_owner(owner)):
                session = load_session(session_path)
                updated = touch(session.model_copy(update={"status": status}))
                save_session(session_path, updated)
                run_path = write_run_snapshot(updated)
    except UnknownRoleError as exc:
        cli_error(1, error="unknown_role_in_calls_jsonl", message=str(exc))
    typer.echo(
        json.dumps({"ok": True, "status": updated.status, "run_path": str(run_path)})
    )


@app.command("lock")
def cmd_lock(
    session_path: Path = typer.Option(..., "--session"),
    owner: str | None = typer.Option(None, "--owner"),
    ttl_seconds: int = typer.Option(3600, "--ttl-seconds"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing valid lock. Use only to recover from stuck locks.",
    ),
) -> None:
    """Acquire the session lock explicitly. Fails with exit 2 if already held.

    `--force` overwrites a valid (non-stale) lock and echoes the displaced
    owner for audit. Stale locks (past expires_at) are always reclaimed
    silently, with or without --force.
    """
    with handle_lock_held():
        displaced = acquire_lock(
            session_path,
            owner=cli_owner(owner),
            ttl_seconds=ttl_seconds,
            force=force,
        )
    record = read_lock(session_path) or {}
    out = {
        "ok": True,
        "owner": record.get("owner"),
        "acquired_at": record.get("acquired_at"),
        "expires_at": record.get("expires_at"),
    }
    if displaced:
        out["displaced"] = {
            "owner": displaced.get("owner"),
            "acquired_at": displaced.get("acquired_at"),
            "expires_at": displaced.get("expires_at"),
        }
    typer.echo(json.dumps(out))


@app.command("unlock")
def cmd_unlock(
    session_path: Path = typer.Option(..., "--session"),
) -> None:
    """Release the session lock unconditionally."""
    release_lock(session_path)
    typer.echo(json.dumps({"ok": True}))


def _count_by_status(pages: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in pages:
        key = p.get("status", "planned")
        counts[key] = counts.get(key, 0) + 1
    return counts


# Re-export for error handling wiring in the top-level CLI if desired.
__all__ = ["app", "SchemaVersionMismatchError"]
