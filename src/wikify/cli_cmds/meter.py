"""wikify meter ... — cost/call telemetry for skill-driven workflows.

Skill workflows record model-call telemetry through this CLI instead of
going through a long-lived CostMeter instance. Each `wikify meter record`
call appends one line to `<bundle>/_calls.jsonl` in the same `CallRecord`
shape the legacy `CostMeter` emits, and updates
`session.budget.haiku_eq_spent` under the session lock. `wikify session
close` reads the jsonl and aggregates the meter snapshot into
`<bundle>/_run.json`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from ..meter import _DEFAULT_TIERS, CallRecord
from ..paths import BundlePaths
from ..session import (
    SessionLockHeldError,
    apply_merge_patch,
    load_session,
    save_session,
    session_lock,
    touch,
)
from ..types import ModelTier, Role

app = typer.Typer(add_completion=False, help="Cost-meter telemetry for skill workflows.")


def _cli_owner(override: str | None) -> str:
    return override or f"wikify-cli/pid-{os.getpid()}"


def haiku_eq_for(tier: ModelTier, input_tokens: int, output_tokens: int) -> float:
    return _DEFAULT_TIERS[tier].haiku_eq(input_tokens, output_tokens)


def append_call_record(
    *,
    session_path: Path,
    role: Role,
    tier: ModelTier,
    input_tokens: int,
    output_tokens: int,
    context_cap: int,
    wall_seconds: float,
    cache_hit: bool,
    prompt_hash: str,
    owner: str | None = None,
) -> CallRecord:
    """Append a CallRecord to _calls.jsonl and increment session spent.

    Raises SessionLockHeldError if another owner holds the lock.
    """
    session = load_session(session_path)
    bundle_paths = BundlePaths(Path(session.bundle_root))
    haiku_eq = haiku_eq_for(tier, input_tokens, output_tokens)
    record = CallRecord(
        role=role,
        tier=tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        context_used=input_tokens,
        context_cap=context_cap,
        wall_seconds=wall_seconds,
        cache_hit=cache_hit,
        prompt_hash=prompt_hash,
        haiku_eq=haiku_eq,
    )
    with session_lock(session_path, owner=_cli_owner(owner)):
        fresh = load_session(session_path)
        bundle_paths.calls_path.parent.mkdir(parents=True, exist_ok=True)
        with bundle_paths.calls_path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json() + "\n")
        new_spent = int(fresh.budget.haiku_eq_spent + haiku_eq)
        updated = apply_merge_patch(fresh, {"budget": {"haiku_eq_spent": new_spent}})
        save_session(session_path, touch(updated))
    return record


@app.command("record")
def cmd_record(
    session_path: Path = typer.Option(..., "--session"),
    role: str = typer.Option(
        ...,
        "--role",
        help="One of: extractor, writer, editor, compactor, orchestrator.",
    ),
    tier: str = typer.Option(..., "--tier", help="One of: S, M, L."),
    input_tokens: int = typer.Option(..., "--input-tokens"),
    output_tokens: int = typer.Option(..., "--output-tokens"),
    context_cap: int = typer.Option(
        200000,
        "--context-cap",
        help="Agent-side context window cap. Defaults to 200k (Claude's current cap).",
    ),
    wall_seconds: float = typer.Option(0.0, "--wall-seconds"),
    cache_hit: bool = typer.Option(False, "--cache-hit"),
    prompt_hash: str = typer.Option("", "--prompt-hash"),
    owner: str | None = typer.Option(None, "--owner"),
) -> None:
    """Append a CallRecord to _calls.jsonl and bump session.budget.haiku_eq_spent."""
    try:
        role_e = Role(role)
    except ValueError as exc:
        raise typer.BadParameter(
            f"invalid --role {role!r}; expected one of {[r.value for r in Role]}"
        ) from exc
    try:
        tier_e = ModelTier(tier)
    except ValueError as exc:
        raise typer.BadParameter(
            f"invalid --tier {tier!r}; expected one of {[t.value for t in ModelTier]}"
        ) from exc
    if input_tokens < 0 or output_tokens < 0:
        raise typer.BadParameter("token counts must be non-negative")
    if input_tokens > context_cap:
        raise typer.BadParameter(
            f"input_tokens={input_tokens} exceeds --context-cap={context_cap}"
        )

    try:
        record = append_call_record(
            session_path=session_path,
            role=role_e,
            tier=tier_e,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_cap=context_cap,
            wall_seconds=wall_seconds,
            cache_hit=cache_hit,
            prompt_hash=prompt_hash,
            owner=owner,
        )
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

    calls_path = BundlePaths(Path(load_session(session_path).bundle_root)).calls_path
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "role": record.role.value,
                "tier": record.tier.value,
                "haiku_eq": record.haiku_eq,
                "calls_path": str(calls_path),
            }
        )
    )


__all__ = ["app", "append_call_record", "haiku_eq_for"]
