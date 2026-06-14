"""``wikify run ...`` — execution control for wiki bundles.

Subcommands::

    run init   --bundle <b> --corpus <c> [--strategy <s>] [--target-haiku-eq <n>]
    run show   [--run <b>] [--detail|--full] [--format text|json]
    run list events [--run <b>] [--tail <n>] [--type <t>] [--format text|json]
    run lock   --run <b> [--owner <id>]
    run unlock --run <b>
    run close  [--run <b>] [--status completed|failed|abandoned]
    run record-call [--run <b>] --role <r> --model-id <m> --tier S|M|L
                    --tokens-in N --tokens-out N [--stage <s>]
    run record-calls --run <b> --from-stdin [--fail-fast] [--format json|compact]
    run record-event [--run <b>] --type <t> [--stage <s>] [--concept-id <c>]
                     [--page-id <p>] [--chunk-id <c>] [--doc-id <d>]
                     [--actor <a>] [--data <json>]

``--run <bundle>`` overrides; otherwise the current working directory
must be a bundle root (``run/state.json`` present).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..bundle.run.events import Event, append_event, iter_events
from ..bundle.run.lifecycle import close_run, init_run
from ..bundle.run.lock import LockHeldError, acquire_lock, read_lock, release_lock
from ..bundle.run.state import load_state, save_state, touch
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error, cli_owner

app = typer.Typer(add_completion=False, help="Run-level execution control.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
    """Resolve ``--run <bundle>`` or fall back to CWD; error on missing marker."""
    if run_flag is not None:
        try:
            return Bundle.open(run_flag)
        except FileNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except FileNotFoundError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=(
                f"no bundle resolved (cwd={cwd}); pass --run <bundle> "
                f"or cd into a bundle root with run/state.json. cause: {exc}"
            ),
        )


@app.command("init")
def cmd_init(
    bundle_dir: Path = typer.Option(..., "--bundle", help="Bundle directory."),
    corpus_dir: Path = typer.Option(..., "--corpus", help="Corpus directory."),
    strategy: str = typer.Option(
        "",
        "--strategy",
        help=(
            "Free-form workflow label (e.g. baseline | guided | free | query). "
            "Passive run metadata; the agent picks. No Python branch reads this."
        ),
    ),
    target_haiku_eq: int = typer.Option(0, "--target-haiku-eq"),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Create ``run/state.json`` and ``run/events.jsonl`` for a fresh bundle."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if (bundle_dir / "run" / "state.json").is_file():
        cli_error(
            EXIT_VALIDATION,
            error="bundle_already_initialised",
            message=f"{bundle_dir} already has run/state.json; refusing to re-init",
        )
    corpus_fingerprint = Corpus(root=corpus_dir).manifest_fingerprint()
    # ``init_run`` writes ``run/state.json``; until that happens
    # ``Bundle.open`` would refuse this directory. Construct the Bundle
    # dataclass directly — ``run init`` is the privileged bootstrap path.
    bundle = Bundle(root=bundle_dir)
    state = init_run(
        bundle,
        corpus_path=corpus_dir,
        strategy=strategy,
        target_haiku_eq=target_haiku_eq,
        corpus_fingerprint=corpus_fingerprint,
    )
    # The cli_invoked event for `run init` is emitted by
    # ``_io.run_with_io_logging``: it detects ``run init --bundle <b>`` at
    # pre-flight and tees stdin/stdout/stderr into ``<b>/run/io/`` even
    # though the bundle does not yet exist. The event lands after init
    # has materialised state.json and events.jsonl.
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "run_id": state.run_id,
                    "bundle": str(bundle.root),
                    "state_path": str(bundle.state_path),
                    "events_path": str(bundle.events_path),
                    "corpus_fingerprint": state.corpus_fingerprint,
                }
            )
        )
    else:
        typer.echo(f"run_id:           {state.run_id}")
        typer.echo(f"bundle:           {bundle.root}")
        typer.echo(f"state:            {bundle.state_path}")
        typer.echo(f"events:           {bundle.events_path}")
        if state.corpus_fingerprint:
            typer.echo(f"corpus_fingerprint: {state.corpus_fingerprint}")


@app.command("show")
def cmd_show(
    run: Path | None = typer.Option(None, "--run"),
    detail: bool = typer.Option(False, "--detail"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format", help="text | json"),
) -> None:
    """Print the current run state. ``--full`` includes computed cost."""
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    if fmt == "json":
        out: dict = state.model_dump()
        if full:
            from ..bundle.run.cost import cost_summary
            out["cost"] = cost_summary(bundle)
        typer.echo(json.dumps(out))
        return
    typer.echo(f"run_id:    {state.run_id}")
    typer.echo(f"status:    {state.status}")
    typer.echo(f"strategy:  {state.strategy}")
    typer.echo(f"corpus:    {state.corpus_path}")
    typer.echo(f"updated:   {state.updated_at}")
    if detail or full:
        typer.echo(
            f"budget:    {state.budget.spent_haiku_eq}/"
            f"{state.budget.target_haiku_eq} haiku-eq"
        )
        if state.stages:
            typer.echo("stages:")
            for stage, status in state.stages.items():
                typer.echo(f"  {stage:<16} {status}")
    if full:
        from ..bundle.run.cost import cost_summary
        cost = cost_summary(bundle)
        totals = cost["totals"]
        typer.echo(
            f"cost:      {totals['calls']} calls, "
            f"{totals['haiku_eq']:.1f} haiku-eq, "
            f"{totals['input_tokens']}+{totals['output_tokens']} tokens"
        )


events_app = typer.Typer(add_completion=False, help="Event-ledger queries.")
app.add_typer(events_app, name="list")


@events_app.command("events")
def cmd_list_events(
    run: Path | None = typer.Option(None, "--run"),
    tail: int = typer.Option(20, "--tail"),
    type_filter: str | None = typer.Option(None, "--type"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print the most recent events from ``run/events.jsonl``."""
    bundle = _resolve_bundle(run)
    events = list(iter_events(bundle))
    if type_filter:
        events = [e for e in events if e.type == type_filter]
    events = events[-tail:] if tail > 0 else events
    if fmt == "json":
        typer.echo(json.dumps([e.model_dump() for e in events]))
        return
    for e in events:
        actor = (e.actor or "?")[:12]
        typer.echo(f"{e.at}  {e.type:<22} {actor:<14} {e.event_id[:8]}")


@app.command("lock")
def cmd_lock(
    run: Path | None = typer.Option(None, "--run"),
    owner: str | None = typer.Option(None, "--owner"),
    ttl_seconds: int = typer.Option(3600, "--ttl-seconds"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Acquire ``run/lock`` for ``--owner`` (default: this CLI process)."""
    bundle = _resolve_bundle(run)
    try:
        acquire_lock(bundle, owner=cli_owner(owner), ttl_seconds=ttl_seconds)
    except LockHeldError as exc:
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "lock_held",
                        "owner": exc.owner,
                        "acquired_at": exc.acquired_at,
                    }
                )
            )
        else:
            typer.echo(f"lock held by {exc.owner} since {exc.acquired_at}", err=True)
        raise typer.Exit(code=EXIT_LOCK_HELD) from exc
    record = read_lock(bundle) or {}
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **record}))
    else:
        typer.echo(f"locked by {record.get('owner', '?')}")


@app.command("unlock")
def cmd_unlock(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Release the bundle lock unconditionally."""
    bundle = _resolve_bundle(run)
    release_lock(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True}))
    else:
        typer.echo("unlocked")


@app.command("close")
def cmd_close(
    run: Path | None = typer.Option(None, "--run"),
    status: str = typer.Option("completed", "--status"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Mark the run completed/failed/abandoned and emit ``run_closed``."""
    bundle = _resolve_bundle(run)
    if status not in {"completed", "failed", "abandoned"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_status",
            message="--status must be completed|failed|abandoned",
        )
    has_call_events = any(e.type == "call" for e in iter_events(bundle))
    state = close_run(bundle, status=status)
    if not has_call_events:
        typer.echo(
            "WARNING: no agent call telemetry recorded; eval cost curves will "
            "be empty. Use 'wikify run record-calls' before closing.",
            err=True,
        )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "run_id": state.run_id, "status": state.status}))
    else:
        typer.echo(f"run {state.run_id} -> {state.status}")


@app.command("record-call")
def cmd_record_call(
    run: Path | None = typer.Option(None, "--run"),
    role: str = typer.Option(..., "--role"),
    model_id: str = typer.Option(..., "--model-id"),
    tier: str = typer.Option(..., "--tier"),
    tokens_in: int = typer.Option(..., "--tokens-in"),
    tokens_out: int = typer.Option(..., "--tokens-out"),
    stage: str = typer.Option("model_call", "--stage"),
    concept_id: str | None = typer.Option(None, "--concept-id"),
    page_id: str | None = typer.Option(None, "--page-id"),
    wall_seconds: float = typer.Option(0.0, "--wall-seconds"),
    actor: str = typer.Option("agent", "--actor"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Append a model-call telemetry event emitted by an agent harness.

    Python does not call the model SDK. This command gives skills a
    deterministic bridge for token accounting after each extractor or
    writer Task returns.
    """
    if tokens_in < 0 or tokens_out < 0:
        cli_error(
            EXIT_VALIDATION,
            error="bad_tokens",
            message="--tokens-in and --tokens-out must be >= 0",
        )
    if wall_seconds < 0:
        cli_error(
            EXIT_VALIDATION,
            error="bad_wall_seconds",
            message="--wall-seconds must be >= 0",
        )
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    try:
        from ..bundle.run.cost import haiku_eq_for
        cost_haiku_eq = haiku_eq_for(tier, tokens_in, tokens_out)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_tier", message=str(exc))

    payload = {
        "role": role,
        "model_id": model_id,
        "tier": tier,
        "stage": stage,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "haiku_eq": cost_haiku_eq,
        "cost_haiku_eq": cost_haiku_eq,
        "cost_usd": 0.0,
        "wall_seconds": wall_seconds,
    }
    append_event(
        bundle,
        Event(
            run_id=state.run_id,
            type="call",
            actor=actor,
            concept_id=concept_id,
            page_id=page_id,
            stage=stage,
            data=payload,
        ),
    )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **payload}))
    else:
        typer.echo(
            f"recorded call role={role} model={model_id} tier={tier} "
            f"tokens={tokens_in}+{tokens_out} haiku_eq={cost_haiku_eq:.1f}"
        )


_ROUND_REQUIRED_TYPES = frozenset(
    {"round_started", "round_completed", "evidence_added", "pattern_dispatched"}
)


def _read_event_payload(data_flag: str | None) -> tuple[dict, bool]:
    """Return ``(payload_dict, stdin_was_ignored)``.

    Resolution order:
    1. Read stdin when it is not a TTY; treat an empty read as no input.
    2. If ``data_flag`` is set, parse it and use it (stdin is ignored if
       it had content — caller should warn).
    3. Else if stdin had content, parse and return it.
    4. Else fall back to an empty object.

    ``stdin_was_ignored`` is True only when ``data_flag`` was supplied
    *and* stdin also had non-empty content, so the caller can warn that
    piped input was discarded.
    """
    # Consume stdin once; treat empty as absent.
    stdin_content: str = ""
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read().strip()

    if data_flag is not None:
        stdin_was_ignored = bool(stdin_content)
        try:
            payload = json.loads(data_flag)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--data is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("--data must be a JSON object, not an array or scalar")
        return payload, stdin_was_ignored

    if stdin_content:
        try:
            payload = json.loads(stdin_content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"stdin is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("stdin payload must be a JSON object, not an array or scalar")
        return payload, False

    return {}, False


def _validate_event_payload(type_: str, payload: dict) -> None:
    """Raise ``ValueError`` when a required field is absent or wrong type.

    Events that carry a round counter must have ``round`` as a non-negative
    int. A missing or non-int ``round`` is rejected so malformed events
    cannot corrupt maturity / growth-stall logic downstream.
    """
    if type_ not in _ROUND_REQUIRED_TYPES:
        return
    if "round" not in payload:
        raise ValueError(
            f"event type {type_!r} requires a 'round' int field in the payload"
        )
    val = payload["round"]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(
            f"event type {type_!r}: 'round' must be an int, got {type(val).__name__}"
        )


@app.command("record-event")
def cmd_record_event(
    type_: str = typer.Option(..., "--type", help="Event type literal."),
    run: Path | None = typer.Option(None, "--run"),
    stage: str | None = typer.Option(None, "--stage"),
    concept_id: str | None = typer.Option(None, "--concept-id"),
    page_id: str | None = typer.Option(None, "--page-id"),
    chunk_id: str | None = typer.Option(None, "--chunk-id"),
    doc_id: str | None = typer.Option(None, "--doc-id"),
    actor: str = typer.Option("agent", "--actor"),
    data: str | None = typer.Option(None, "--data", help="JSON object payload."),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Append a non-call event (round_started, round_completed, etc.).

    Use this for the investigate workflow's round + pattern lifecycle
    events. ``call`` events stay on ``record-call`` / ``record-calls``
    where the cost machinery enforces token validation.

    The ``--type`` value is validated against ``EventType``; unknown
    types are rejected.

    Payload resolution order: ``--data`` > piped stdin > empty object.
    When both ``--data`` and piped stdin are supplied, ``--data`` wins
    and a warning is printed to stderr.

    Events that carry a round counter (``round_started``,
    ``round_completed``, ``evidence_added``, ``pattern_dispatched``)
    must include ``round`` as an integer field or the command exits
    non-zero.
    """
    from typing import get_args

    from ..bundle.run.events import EventType
    allowed = set(get_args(EventType))
    if type_ == "call":
        cli_error(
            EXIT_VALIDATION,
            error="use_record_call",
            message="use 'wikify run record-call' for type=call.",
        )
    if type_ not in allowed:
        cli_error(
            EXIT_VALIDATION,
            error="bad_event_type",
            message=f"unknown event type: {type_!r}",
            allowed=sorted(allowed),
        )
    try:
        payload, stdin_ignored = _read_event_payload(data)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_data", message=str(exc))
    if stdin_ignored:
        typer.echo(
            "WARNING: --data was supplied; piped stdin was ignored",
            err=True,
        )
    try:
        _validate_event_payload(type_, payload)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="bad_payload", message=str(exc))
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    event = Event(
        run_id=state.run_id,
        type=type_,
        actor=actor,
        stage=stage,
        concept_id=concept_id,
        page_id=page_id,
        chunk_id=chunk_id,
        doc_id=doc_id,
        data=payload,
    )
    append_event(bundle, event)
    if fmt == "json":
        typer.echo(
            json.dumps({"ok": True, "event_id": event.event_id, "type": type_})
        )
        return
    typer.echo(f"recorded {type_} event_id={event.event_id}")


_REQUIRED_BATCH_FIELDS: tuple[tuple[str, type], ...] = (
    ("role", str),
    ("model_id", str),
    ("tier", str),
    ("tokens_in", int),
    ("tokens_out", int),
    ("stage", str),
)


def _validate_batch_line(obj: object) -> tuple[dict | None, str | None]:
    """Return ``(payload, None)`` or ``(None, error_message)`` for one line.

    ``payload`` is a normalised dict ready to hand to ``Event``. Errors
    are short human-readable strings ("missing role", "tokens_in must
    be int", etc.); the caller prefixes the line number.
    """
    if not isinstance(obj, dict):
        return None, "line is not a JSON object"
    for name, typ in _REQUIRED_BATCH_FIELDS:
        if name not in obj:
            return None, f"missing {name}"
        val = obj[name]
        # bool is a subclass of int; reject explicitly for the int fields.
        if typ is int and (not isinstance(val, int) or isinstance(val, bool)):
            return None, f"{name} must be int"
        if typ is str and not isinstance(val, str):
            return None, f"{name} must be str"
    if obj["tokens_in"] < 0 or obj["tokens_out"] < 0:
        return None, "tokens_in and tokens_out must be >= 0"
    return obj, None


@app.command("record-calls")
def cmd_record_calls(
    run: Path = typer.Option(..., "--run"),
    from_stdin: bool = typer.Option(False, "--from-stdin"),
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    fmt: str = typer.Option("json", "--format", help="json | compact"),
) -> None:
    """Batched ingest of call telemetry: one JSON object per stdin line.

    Each line must carry ``role``, ``model_id``, ``tier``, ``tokens_in``,
    ``tokens_out``, ``stage``. Optional keys: ``concept_id``, ``page_id``,
    ``chunk_id``, ``doc_id``, ``at``, ``wall_seconds``, ``actor``. Valid
    lines append a ``call`` event preserving input order; malformed lines
    are skipped (or abort the batch under ``--fail-fast``).
    """
    if not from_stdin:
        cli_error(
            EXIT_VALIDATION,
            error="missing_input_mode",
            message="--from-stdin is required",
        )
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    from ..bundle.run.cost import haiku_eq_for

    appended = 0
    errors: list[str] = []
    for lineno, raw in enumerate(sys.stdin, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            err = f"line {lineno}: invalid json ({exc.msg})"
            errors.append(err)
            if fail_fast:
                break
            continue
        payload, reason = _validate_batch_line(obj)
        if payload is None:
            errors.append(f"line {lineno}: {reason}")
            if fail_fast:
                break
            continue
        try:
            cost_haiku_eq = haiku_eq_for(
                payload["tier"], payload["tokens_in"], payload["tokens_out"]
            )
        except ValueError as exc:
            errors.append(f"line {lineno}: {exc}")
            if fail_fast:
                break
            continue
        wall_seconds = payload.get("wall_seconds", 0.0)
        actor = payload.get("actor", "agent")
        data = {
            "role": payload["role"],
            "model_id": payload["model_id"],
            "tier": payload["tier"],
            "stage": payload["stage"],
            "input_tokens": payload["tokens_in"],
            "output_tokens": payload["tokens_out"],
            "haiku_eq": cost_haiku_eq,
            "cost_haiku_eq": cost_haiku_eq,
            "cost_usd": 0.0,
            "wall_seconds": wall_seconds,
        }
        event_kwargs: dict = {
            "run_id": state.run_id,
            "type": "call",
            "actor": actor,
            "stage": payload["stage"],
            "data": data,
        }
        for opt in ("concept_id", "page_id", "chunk_id", "doc_id"):
            if opt in payload and payload[opt] is not None:
                event_kwargs[opt] = payload[opt]
        if "at" in payload and payload["at"] is not None:
            event_kwargs["at"] = payload["at"]
        try:
            append_event(bundle, Event(**event_kwargs))
        except Exception as exc:
            errors.append(f"line {lineno}: append failed ({exc})")
            if fail_fast:
                break
            continue
        appended += 1

    summary = {
        "ok": True,
        "run": str(bundle.root),
        "appended": appended,
        "rejected": len(errors),
        "errors": errors,
    }
    if fmt == "compact":
        typer.echo(
            f"appended={appended} rejected={len(errors)} run={bundle.root}"
        )
    else:
        typer.echo(json.dumps(summary))


@app.command("set")
def cmd_set(
    run: Path | None = typer.Option(None, "--run"),
    target_haiku_eq: int | None = typer.Option(None, "--target-haiku-eq"),
    strategy_note: str | None = typer.Option(None, "--strategy-note"),
    corpus_fingerprint: str | None = typer.Option(None, "--corpus-fingerprint"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Update small mutable fields. ``--corpus`` is forbidden — open a new bundle.

    ``--corpus-fingerprint`` re-stamps corpus identity after a re-entry has
    absorbed new documents (the value is the live ``health.fingerprint``),
    so drift detection does not re-fire on the next round.
    """
    bundle = _resolve_bundle(run)
    state = load_state(bundle)
    updates: dict = {}
    if target_haiku_eq is not None:
        budget = state.budget.model_copy(update={"target_haiku_eq": target_haiku_eq})
        updates["budget"] = budget
    if corpus_fingerprint is not None:
        updates["corpus_fingerprint"] = corpus_fingerprint
    if strategy_note is not None:
        # Append note as a stage_changed event; state.json itself stays slim.
        append_event(
            bundle,
            Event(
                run_id=state.run_id,
                type="stage_changed",
                actor="cli",
                stage="set",
                data={"strategy_note": strategy_note},
            ),
        )
    if updates:
        new_state = touch(state.model_copy(update=updates))
        save_state(bundle, new_state)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True}))
    else:
        typer.echo("ok")


__all__ = ["app"]
