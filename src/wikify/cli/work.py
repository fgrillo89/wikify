"""``wikify work ...`` — in-flight build state for v2 bundles.

Subcommands::

    work list [--run] [--status]
    work list claims [--run]
    work list inbox [--run]
    work list evidence <concept> [--run]
    work show <concept> [--run] [--detail|--full]
    work add concept "<title>" [--run] [--kind] [--aliases]
    work add evidence <concept> --records <jsonl-path> [--run]
    work add feedback query --record <json|jsonl-path> [--run]
    work set <concept> [--run] [--status] [--needs-refine]
    work claim <concept> [--run] [--owner] [--ttl-seconds]
    work release <concept> [--run] [--owner]
    work tend [--run]
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle, LayoutMismatchError
from ..bundle.work.card import (
    create_concept,
    list_concept_slugs,
    load_card,
    save_card,
)
from ..bundle.work.claim import (
    ClaimHeldError,
    acquire_claim,
    list_claims,
    read_claim,
    release_claim,
)
from ..bundle.work.evidence import EvidenceRecord, append_evidence, read_evidence
from ..bundle.work.inbox import append_inbox, list_inbox_files
from ..bundle.work.tend import tend_bundle
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error, cli_owner

app = typer.Typer(add_completion=False, help="In-flight build state.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
    if run_flag is not None:
        try:
            return Bundle.open(run_flag)
        except (LayoutMismatchError, FileNotFoundError) as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except (LayoutMismatchError, FileNotFoundError) as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=f"no v2 bundle resolved (cwd={cwd}); pass --run <bundle>. cause: {exc}",
        )


# -------------------------------------------------------------- list


list_app = typer.Typer(add_completion=False, help="List work state.")
app.add_typer(list_app, name="list")


@list_app.callback(invoke_without_command=True)
def cmd_list_default(
    ctx: typer.Context,
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """List concept slugs with their card frontmatter."""
    if ctx.invoked_subcommand is not None:
        return
    bundle = _resolve_bundle(run)
    slugs = list_concept_slugs(bundle)
    if fmt == "json":
        items = []
        for s in slugs:
            card = load_card(bundle, s)
            items.append(
                {
                    "slug": s,
                    "page_id": card.page_id,
                    "kind": card.kind,
                    "status": card.status,
                    "needs_refine": card.needs_refine,
                }
            )
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for s in slugs:
        card = load_card(bundle, s)
        typer.echo(f"{s:<32}  {card.kind:<8}  {card.status:<14}  {card.page_id}")


@list_app.command("claims")
def cmd_list_claims(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    claims = list_claims(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": claims}))
        return
    for c in claims:
        typer.echo(
            f"{c.get('slug', '?'):<32}  {c.get('owner', '?'):<14}  {c.get('acquired_at', '?')}"
        )


@list_app.command("inbox")
def cmd_list_inbox(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    files = list_inbox_files(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": files}))
        return
    for f in files:
        typer.echo(f)


@list_app.command("evidence")
def cmd_list_evidence(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    records = read_evidence(bundle, concept)
    if fmt == "json":
        typer.echo(
            json.dumps({"ok": True, "items": [r.model_dump() for r in records]})
        )
        return
    for r in records:
        typer.echo(f"{r.chunk_id}  {r.doc_id}  {r.status}  {r.score:.2f}")


# -------------------------------------------------------------- show


@app.command("show")
def cmd_show(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    card = load_card(bundle, concept)
    if not card.front:
        cli_error(EXIT_VALIDATION, error="concept_not_found", slug=concept)
    if fmt == "json":
        body_payload = card.body if full else card.body[:500]
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "slug": concept,
                    "front": card.front,
                    "body": body_payload,
                }
            )
        )
        return
    typer.echo(f"slug:         {concept}")
    for k, v in card.front.items():
        typer.echo(f"{k:<14}  {v}")
    if full and card.body:
        typer.echo("---")
        typer.echo(card.body)


# -------------------------------------------------------------- add


add_app = typer.Typer(add_completion=False, help="Mutate concepts / inbox.")
app.add_typer(add_app, name="add")


@add_app.command("concept")
def cmd_add_concept(
    title: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    kind: str = typer.Option("article", "--kind"),
    aliases: str = typer.Option("[]", "--aliases", help='JSON list of aliases.'),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    if kind not in {"article", "person"}:
        cli_error(EXIT_VALIDATION, error="bad_kind", kind=kind)
    try:
        alias_list = json.loads(aliases)
        if not isinstance(alias_list, list):
            raise ValueError("aliases must be a JSON list")
    except (json.JSONDecodeError, ValueError) as exc:
        cli_error(EXIT_VALIDATION, error="bad_aliases", message=str(exc))
    slug, _ = create_concept(bundle, page_id=title, kind=kind, aliases=alias_list)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "slug": slug, "page_id": title}))
        return
    typer.echo(f"created {slug}")


@add_app.command("evidence")
def cmd_add_evidence(
    concept: str = typer.Argument(...),
    records: Path = typer.Option(..., "--records", help="JSONL of EvidenceRecords."),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    if not records.is_file():
        cli_error(EXIT_VALIDATION, error="records_not_found", path=str(records))
    parsed: list[EvidenceRecord] = []
    for line in records.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed.append(EvidenceRecord.model_validate_json(line))
        except Exception as exc:
            cli_error(EXIT_VALIDATION, error="bad_record", message=str(exc))
    n = append_evidence(bundle, concept, parsed)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "appended": n}))
        return
    typer.echo(f"appended {n} records to {concept}/evidence.jsonl")


@add_app.command("feedback")
def cmd_add_feedback(
    kind: str = typer.Argument(..., help="evidence|concept|merge|query"),
    record: Path = typer.Option(..., "--record", help="JSON or JSONL path."),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    inbox_kind = {
        "evidence": "evidence_suggestions",
        "concept": "concept_suggestions",
        "merge": "merge_suggestions",
        "query": "query_feedback",
    }.get(kind)
    if inbox_kind is None:
        cli_error(EXIT_VALIDATION, error="bad_feedback_kind", kind=kind)
    if not record.is_file():
        cli_error(EXIT_VALIDATION, error="record_not_found", path=str(record))
    text = record.read_text(encoding="utf-8")
    n = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            cli_error(EXIT_VALIDATION, error="bad_json", message=str(exc))
        append_inbox(bundle, inbox_kind, obj)
        n += 1
    if n == 0:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            cli_error(EXIT_VALIDATION, error="bad_json", message=str(exc))
        append_inbox(bundle, inbox_kind, obj)
        n = 1
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "appended": n, "kind": inbox_kind}))
        return
    typer.echo(f"appended {n} records to {inbox_kind}.jsonl")


# -------------------------------------------------------------- set


@app.command("set")
def cmd_set(
    concept: str = typer.Argument(...),
    status: str | None = typer.Option(None, "--status"),
    needs_refine: bool | None = typer.Option(None, "--needs-refine"),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    card = load_card(bundle, concept)
    if not card.front:
        cli_error(EXIT_VALIDATION, error="concept_not_found", slug=concept)
    if status is not None:
        card.front["status"] = status
    if needs_refine is not None:
        card.front["needs_refine"] = needs_refine
    save_card(bundle, concept, card)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "front": card.front}))
        return
    typer.echo("ok")


# -------------------------------------------------------------- claim


@app.command("claim")
def cmd_claim(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    owner: str | None = typer.Option(None, "--owner"),
    ttl_seconds: int = typer.Option(1800, "--ttl-seconds"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    o = cli_owner(owner)
    try:
        acquire_claim(bundle, concept, owner=o, ttl_seconds=ttl_seconds)
    except ClaimHeldError as exc:
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "claim_held",
                        "slug": exc.slug,
                        "owner": exc.owner,
                        "acquired_at": exc.acquired_at,
                    }
                )
            )
        else:
            typer.echo(
                f"claim on {exc.slug} held by {exc.owner} since {exc.acquired_at}",
                err=True,
            )
        raise typer.Exit(code=EXIT_LOCK_HELD) from exc
    record = read_claim(bundle, concept) or {}
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **record}))
        return
    typer.echo(f"claimed {concept} as {record.get('owner', '?')}")


@app.command("release")
def cmd_release(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    owner: str | None = typer.Option(None, "--owner"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    o = cli_owner(owner)
    ok = release_claim(bundle, concept, owner=o)
    if not ok:
        # Either no claim, or held by someone else.
        existing = read_claim(bundle, concept)
        if existing and existing.get("owner") != o:
            if fmt == "json":
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "claim_held",
                            "slug": concept,
                            "owner": existing.get("owner"),
                            "acquired_at": existing.get("acquired_at"),
                        }
                    )
                )
            else:
                typer.echo(
                    f"cannot release {concept}: held by {existing.get('owner', '?')}",
                    err=True,
                )
            raise typer.Exit(code=EXIT_LOCK_HELD)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "released": ok}))
        return
    typer.echo("released" if ok else "no claim")


# -------------------------------------------------------------- tend


@app.command("tend")
def cmd_tend(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    summary = tend_bundle(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **summary}))
        return
    typer.echo(f"concepts:                {summary['concepts']}")
    typer.echo(f"claims active:           {summary['claims_active']}")
    typer.echo(f"claims expired:          {summary['claims_expired']}")
    typer.echo(f"evidence records deduped:{summary['evidence_records_deduped']}")
    typer.echo(f"inbox files:             {len(summary['inbox_files'])}")
    typer.echo(f"index:                   {summary['index_path']}")


__all__ = ["app"]
