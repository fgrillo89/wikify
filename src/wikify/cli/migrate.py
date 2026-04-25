"""wikify migrate ... — read-only legacy-bundle inspector.

The skill-centric redesign keeps existing v1 (legacy) bundles on disk
intact. ``wikify migrate inspect <bundle>`` reports the layout version
and which legacy artifacts are present, so a user can decide whether
to start a fresh v2 bundle from the same corpus or hand-migrate.

This is a read-only diagnostic. It never mutates the bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle, LayoutMismatchError, LegacyBundle, _detect_layout

app = typer.Typer(
    add_completion=False,
    help="Inspect and (eventually) migrate legacy wiki bundles to the v2 layout.",
)


# Legacy v1 artifacts the inspector reports on. Order matters — output is
# stable so tests + diff-based reviews don't churn.
_LEGACY_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("_session/", "dir"),
    ("_session/session.json", "file"),
    ("_session/checkpoints/", "dir"),
    ("_session/session.lock", "file"),
    ("_scratch/", "dir"),
    ("_calls.jsonl", "file"),
    ("_run.json", "file"),
    ("_run_history.jsonl", "file"),
    ("_index.json", "file"),
    ("_index.md", "file"),
    ("_wiki_graph.json", "file"),
    ("_wiki_vectors.npz", "file"),
    ("_meta/", "dir"),
    ("articles/", "dir"),
    ("people/", "dir"),
)

# v2 artifacts the inspector reports on. Helpful when a bundle is mid-migration.
_V2_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("run/", "dir"),
    ("run/state.json", "file"),
    ("run/events.jsonl", "file"),
    ("run/lock", "file"),
    ("work/", "dir"),
    ("wiki/", "dir"),
    ("derived/", "dir"),
)


def _probe(root: Path, rel: str, kind: str) -> dict | None:
    """Return a metadata dict if the artifact is present, else ``None``."""
    p = root / rel.rstrip("/")
    if kind == "dir":
        if not p.is_dir():
            return None
        try:
            file_count = sum(1 for _ in p.iterdir())
        except OSError:
            file_count = -1
        return {"kind": "dir", "file_count": file_count}
    if kind == "file":
        if not p.is_file():
            return None
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        return {"kind": "file", "size_bytes": size}
    return None


@app.command("inspect")
def cmd_inspect(
    bundle_dir: Path = typer.Argument(..., help="Path to the bundle directory."),
    fmt: str = typer.Option(
        "text", "--format", help="text (default, human) | json (stable, machine)."
    ),
) -> None:
    """Report a bundle's layout version and which artifacts are present."""
    if fmt not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")

    if not bundle_dir.is_dir():
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {"ok": False, "error": "not_a_directory", "path": str(bundle_dir)}
                )
            )
        else:
            typer.echo(f"error: not a directory: {bundle_dir}", err=True)
        raise typer.Exit(code=1)

    layout = _detect_layout(bundle_dir)

    legacy_present: dict[str, dict] = {}
    for rel, kind in _LEGACY_ARTIFACTS:
        info = _probe(bundle_dir, rel, kind)
        if info is not None:
            legacy_present[rel] = info

    v2_present: dict[str, dict] = {}
    for rel, kind in _V2_ARTIFACTS:
        info = _probe(bundle_dir, rel, kind)
        if info is not None:
            v2_present[rel] = info

    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "bundle": str(bundle_dir),
                    "layout": layout,
                    "legacy_artifacts": legacy_present,
                    "v2_artifacts": v2_present,
                },
                indent=2,
            )
        )
        return

    typer.echo(f"bundle:  {bundle_dir}")
    typer.echo(f"layout:  {layout}")
    if legacy_present:
        typer.echo("legacy artifacts:")
        for rel, info in legacy_present.items():
            if info["kind"] == "dir":
                count = info["file_count"]
                count_str = f"{count} entries" if count >= 0 else "unreadable"
                typer.echo(f"  {rel:<32} ({count_str})")
            else:
                size = info["size_bytes"]
                size_str = f"{size} bytes" if size >= 0 else "unreadable"
                typer.echo(f"  {rel:<32} ({size_str})")
    else:
        typer.echo("legacy artifacts: none")
    if v2_present:
        typer.echo("v2 artifacts:")
        for rel, info in v2_present.items():
            if info["kind"] == "dir":
                count = info["file_count"]
                count_str = f"{count} entries" if count >= 0 else "unreadable"
                typer.echo(f"  {rel:<32} ({count_str})")
            else:
                size = info["size_bytes"]
                size_str = f"{size} bytes" if size >= 0 else "unreadable"
                typer.echo(f"  {rel:<32} ({size_str})")
    else:
        typer.echo("v2 artifacts:    none")


__all__ = ["app", "Bundle", "LegacyBundle", "LayoutMismatchError"]
