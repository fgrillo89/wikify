"""``wikify draft ...`` — per-attempt draft + response IO + validation gate.

Subcommands::

    draft build <concept> [--task create|refine] [--corpus <c>] [--run <b>]
    draft show  <concept> [--run <b>] [--full] [--format text|json]
    draft normalize-references <concept> [--run <b>] [--format text|json]
    draft check <concept> [--run <b>] [--format text|json]
    draft finalize <concept> --run <b> --owner <o> [--format json|compact|auto] [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..bundle.draft.artifact import (
    dossier_path,
    draft_path,
    read_json,
    response_path,
    validation_path,
)
from ..bundle.draft.builder import build_draft, load_draft
from ..bundle.draft.dossier import render_dossier
from ..bundle.draft.references import normalize_response_references
from ..bundle.draft.validator import validate_response, validate_response_data
from ..bundle.run.lock import LockHeldError
from ..bundle.wiki.commit import CommitGateError, commit_page
from ..bundle.work.claim import read_claim, release_claim
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error, cli_owner
from ._io import _clean_slug_arg

app = typer.Typer(add_completion=False, help="Per-attempt draft IO + validation gate.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
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
            message=f"no bundle resolved (cwd={cwd}); pass --run <bundle>. cause: {exc}",
        )


@app.command("build")
def cmd_build(
    concept: str = typer.Argument(...),
    task: str = typer.Option("create", "--task", help="create | refine"),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    model_id: str = typer.Option(
        ...,
        "--model-id",
        help="Writer model identifier (e.g. claude-sonnet-4-6). Required.",
    ),
    tier: str = typer.Option(
        ...,
        "--tier",
        help="Writer cost tier — S | M | L. Required.",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
    with_adjacent: bool = typer.Option(
        False,
        "--with-adjacent",
        help=(
            "For every evidence record, also load the previous and next "
            "chunk (by ord, within the same document) into the evidence "
            "entry's ``context_window`` so the writer sees flanking "
            "context. Citations and quote grounding still target the "
            "primary chunk only."
        ),
    ),
) -> None:
    """Compile a WriteRequest for *concept* and write draft.json.

    ``--model-id`` and ``--tier`` are required; strategy lives in
    skills, not Python defaults.
    """
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if task not in {"create", "refine"}:
        cli_error(EXIT_VALIDATION, error="bad_task", task=task)
    if tier not in {"S", "M", "L"}:
        cli_error(EXIT_VALIDATION, error="bad_tier", tier=tier)
    if not corpus_dir.is_dir():
        cli_error(
            EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir)
        )
    corpus = Corpus(root=corpus_dir)
    try:
        request = build_draft(
            bundle,
            slug=concept,
            corpus=corpus,
            task=task,
            model_id=model_id,
            tier=tier,
            with_adjacent=with_adjacent,
        )
    except FileNotFoundError as exc:
        cli_error(EXIT_VALIDATION, error="concept_not_found", message=str(exc))
    p = draft_path(bundle, concept)
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "draft_path": str(p),
                    "page_id": request.page_id,
                    "evidence_count": len(request.evidence),
                }
            )
        )
        return
    typer.echo(f"draft:    {p}")
    typer.echo(f"page_id:  {request.page_id}")
    typer.echo(f"evidence: {len(request.evidence)} chunks")


@app.command("show")
def cmd_show(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print the draft.json for a concept."""
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    p = draft_path(bundle, concept)
    if not p.is_file():
        cli_error(EXIT_VALIDATION, error="draft_not_found", path=str(p))
    payload = read_json(p)
    if fmt == "json":
        if not full:
            # Trim heavy chunk_text fields to keep output token-light.
            for ev in payload.get("evidence", []):
                if isinstance(ev, dict) and "chunk_text" in ev:
                    text = ev["chunk_text"]
                    ev["chunk_text"] = text[:500] + "..." if len(text) > 500 else text
        typer.echo(json.dumps(payload))
        return
    request = load_draft(bundle, concept)
    typer.echo(f"page_id:    {request.page_id}")
    typer.echo(f"page_kind:  {request.page_kind}")
    typer.echo(f"title:      {request.title}")
    typer.echo(f"aliases:    {request.aliases}")
    typer.echo(f"evidence:   {len(request.evidence)} chunks")
    if full:
        for i, ev in enumerate(request.evidence):
            preview = (ev.chunk_text or "")[:200]
            typer.echo(f"  e{i + 1}: {ev.chunk_id} ({ev.doc_id})")
            typer.echo(f"       quote: {ev.quote}")
            typer.echo(f"       chunk: {preview}")


@app.command("render-dossier")
def cmd_render_dossier(
    concept: str = typer.Argument(...),
    out: Path | None = typer.Option(
        None, "--out",
        help="Destination path. Defaults to work/concepts/<slug>/dossier.md.",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Regenerate the markdown evidence dossier from ``draft.json``.

    The dossier is also written automatically by ``wikify draft build``.
    Call this directly when evidence on disk changed without rebuilding
    the draft, or when the dossier was deleted.
    """
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if not draft_path(bundle, concept).is_file():
        cli_error(EXIT_VALIDATION, error="draft_not_found", concept=concept)
    request = load_draft(bundle, concept)
    target = out if out is not None else dossier_path(bundle, concept)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = render_dossier(request)
    target.write_text(body, encoding="utf-8")
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "dossier_path": str(target),
                    "evidence_records": len(request.evidence),
                    "bytes": len(body),
                }
            )
        )
        return
    typer.echo(f"dossier: {target}")
    typer.echo(f"records: {len(request.evidence)}  bytes: {len(body)}")


@app.command("normalize-references")
def cmd_normalize_references(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Normalize response.json references from draft evidence markers."""
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if not draft_path(bundle, concept).is_file():
        cli_error(EXIT_VALIDATION, error="draft_not_found", concept=concept)
    if not response_path(bundle, concept).is_file():
        cli_error(EXIT_VALIDATION, error="response_not_found", concept=concept)
    try:
        result = normalize_response_references(bundle, concept)
    except ValueError as exc:
        cli_error(EXIT_VALIDATION, error="normalization_failed", message=str(exc))
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "response_path": result.response_path,
                    "markers": result.markers,
                    "reference_count": result.reference_count,
                }
            )
        )
        return
    typer.echo(f"response:   {result.response_path}")
    typer.echo(f"markers:    {result.markers}")
    typer.echo(f"references: {result.reference_count}")


@app.command("check")
def cmd_check(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Read a candidate response.json from stdin and validate it "
            "against the on-disk draft. Does not write validation.json. "
            "Use this from a writer subagent to pre-check a response "
            "before committing it to disk."
        ),
    ),
) -> None:
    """Validate response.json for *concept* against draft.json. Writes validation.json."""
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if not draft_path(bundle, concept).is_file():
        cli_error(EXIT_VALIDATION, error="draft_not_found", concept=concept)
    if dry_run:
        import sys as _sys

        try:
            response_data = json.loads(_sys.stdin.buffer.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            cli_error(
                EXIT_VALIDATION,
                error="bad_response_json",
                message=f"stdin is not valid JSON: {exc}",
            )
        draft_data = read_json(draft_path(bundle, concept))
        verdict = validate_response_data(draft_data, response_data)
    else:
        if not response_path(bundle, concept).is_file():
            cli_error(EXIT_VALIDATION, error="response_not_found", concept=concept)
        verdict = validate_response(bundle, concept)
    if fmt == "json":
        typer.echo(json.dumps(verdict))
    else:
        ok = verdict["ok"]
        typer.echo(f"ok:        {ok}")
        typer.echo(f"page_id:   {verdict['page_id']}")
        typer.echo(f"verdict:   {validation_path(bundle, concept)}")
        if not ok:
            typer.echo(f"errors:    {len(verdict['errors'])}")
            for e in verdict["errors"][:10]:
                typer.echo(f"  [{e.get('code')}] {e.get('path')}: {e.get('message')}")
    if not verdict["ok"]:
        raise typer.Exit(code=EXIT_VALIDATION)


_FINALIZE_STEPS = (
    "normalize-references",
    "check",
    "commit",
    "release",
)


def _resolve_finalize_format(fmt: str) -> str:
    """Resolve ``--format`` for ``draft finalize``.

    Honors only the three flag values documented for this command:
    ``json``, ``compact``, and ``auto`` (TTY -> compact, otherwise json).
    """
    if fmt not in {"json", "compact", "auto"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_format",
            message=f"unknown --format {fmt!r}; expected json | compact | auto",
        )
    if fmt == "auto":
        return "compact" if sys.stdout.isatty() else "json"
    return fmt


def _emit_finalize(envelope: dict, fmt: str) -> None:
    if fmt == "json":
        typer.echo(json.dumps(envelope))
        return
    # compact: one line per step, tab-separated: status \t step \t detail
    for step in envelope.get("steps", []):
        status = "ok" if step.get("ok") else "fail"
        detail_bits: list[str] = []
        for k, v in step.items():
            if k in {"step", "ok"}:
                continue
            detail_bits.append(f"{k}={v}")
        typer.echo("\t".join([status, step["step"], " ".join(detail_bits)]))


@app.command("finalize")
def cmd_finalize(
    concept: str = typer.Argument(...),
    run: Path = typer.Option(..., "--run"),
    owner: str = typer.Option(..., "--owner"),
    fmt: str = typer.Option("auto", "--format", help="json | compact | auto"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report which steps would run; do not execute them.",
    ),
) -> None:
    """Run the per-page commit chain in order.

    Composes ``normalize_response_references`` ->
    ``validate_response`` -> ``commit_page`` -> ``release_claim``.
    Short-circuits on the first failure; the JSON envelope names the
    failing step so callers can resume from the right place.
    """
    fmt_resolved = _resolve_finalize_format(fmt)
    concept = _clean_slug_arg(concept)

    if dry_run:
        envelope = {
            "ok": True,
            "slug": concept,
            "dry_run": True,
            "steps": [{"step": s, "ok": True, "planned": True} for s in _FINALIZE_STEPS],
        }
        _emit_finalize(envelope, fmt_resolved)
        return

    try:
        bundle = Bundle.open(run)
    except FileNotFoundError as exc:
        cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))

    steps: list[dict] = []
    envelope: dict = {"ok": False, "slug": concept, "steps": steps}

    # Step 1: normalize references.
    if not draft_path(bundle, concept).is_file():
        steps.append({
            "step": "normalize-references",
            "ok": False,
            "error": "draft_not_found",
            "message": str(draft_path(bundle, concept)),
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_VALIDATION)
    if not response_path(bundle, concept).is_file():
        steps.append({
            "step": "normalize-references",
            "ok": False,
            "error": "response_not_found",
            "message": str(response_path(bundle, concept)),
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_VALIDATION)
    typer.echo(f"finalize: normalize-references {concept}", err=True)
    try:
        norm = normalize_response_references(bundle, concept)
    except ValueError as exc:
        steps.append({
            "step": "normalize-references",
            "ok": False,
            "error": "normalization_failed",
            "message": str(exc),
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_VALIDATION) from exc
    steps.append({
        "step": "normalize-references",
        "ok": True,
        "markers": norm.markers,
        "reference_count": norm.reference_count,
    })

    # Step 2: validation gate.
    typer.echo(f"finalize: check {concept}", err=True)
    verdict = validate_response(bundle, concept)
    if not verdict.get("ok"):
        steps.append({
            "step": "check",
            "ok": False,
            "error": "validation_failed",
            "errors": verdict.get("errors", []),
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_VALIDATION)
    steps.append({"step": "check", "ok": True, "page_id": verdict.get("page_id")})

    # Step 3: promote response to the wiki layout.
    typer.echo(f"finalize: commit {concept}", err=True)
    try:
        result = commit_page(bundle, slug=concept)
    except CommitGateError as exc:
        steps.append({
            "step": "commit",
            "ok": False,
            "error": "commit_gate",
            "message": str(exc),
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_VALIDATION) from exc
    except LockHeldError as exc:
        steps.append({
            "step": "commit",
            "ok": False,
            "error": "lock_held",
            "message": str(exc),
            "owner": exc.owner,
            "acquired_at": exc.acquired_at,
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_LOCK_HELD) from exc
    steps.append({
        "step": "commit",
        "ok": True,
        "page_id": result.page_id,
        "kind": result.kind,
        "path": str(result.page_path.relative_to(bundle.root)).replace("\\", "/"),
    })

    # Step 4: release the per-concept claim.
    typer.echo(f"finalize: release {concept}", err=True)
    canonical_owner = cli_owner(owner)
    released = release_claim(bundle, concept, owner=canonical_owner)
    if not released:
        existing = read_claim(bundle, concept)
        if existing and existing.get("owner") != canonical_owner:
            steps.append({
                "step": "release",
                "ok": False,
                "error": "claim_held",
                "owner": existing.get("owner"),
                "acquired_at": existing.get("acquired_at"),
            })
            _emit_finalize(envelope, fmt_resolved)
            raise typer.Exit(code=EXIT_LOCK_HELD)
    steps.append({"step": "release", "ok": True, "released": released})

    envelope["ok"] = True
    _emit_finalize(envelope, fmt_resolved)


__all__ = ["app"]
