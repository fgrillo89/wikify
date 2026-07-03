"""``wikify draft ...`` — per-attempt draft + response IO + validation gate.

Subcommands::

    draft build <concept> [--task create|refine] [--corpus <c>] [--run <b>]
                          [--model-id <id>] [--tier S|M|L]
    draft show  <concept> [--run <b>] [--full] [--format text|json]
    draft normalize-references <concept> [--run <b>] [--format text|json]
    draft check <concept> [--run <b>] [--format text|json]
    draft finalize <concept> --run <b> [--owner <o>] [--format json|compact|auto] [--dry-run]
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
from ..bundle.run.events import read_events
from ..bundle.run.lock import LockHeldError
from ..bundle.wiki.commit import CommitGateError, commit_page
from ..bundle.work.card import load_card
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


_DEFAULT_MODEL_ID = "claude-sonnet-4-6"
_DEFAULT_TIER = "M"


@app.command("build")
def cmd_build(
    concept: str = typer.Argument(...),
    task: str = typer.Option("create", "--task", help="create | refine"),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    model_id: str = typer.Option(
        _DEFAULT_MODEL_ID,
        "--model-id",
        help=f"Writer model identifier. Default: {_DEFAULT_MODEL_ID!r}.",
    ),
    tier: str = typer.Option(
        _DEFAULT_TIER,
        "--tier",
        help=f"Writer cost tier — S | M | L. Default: {_DEFAULT_TIER!r}.",
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

    ``--model-id`` defaults to ``claude-sonnet-4-6``; ``--tier`` defaults
    to ``M``. Both are overridable per-call.
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
    dropped_empty = read_json(p).get("dropped_empty_evidence", 0)
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "draft_path": str(p),
                    "page_id": request.page_id,
                    "evidence_count": len(request.evidence),
                    "dropped_empty_evidence": dropped_empty,
                }
            )
        )
        return
    typer.echo(f"draft:    {p}")
    if dropped_empty:
        typer.echo(f"warning:  dropped {dropped_empty} empty-body evidence record(s)")
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


def _recall_cleared(bundle: Bundle, slug: str) -> bool:
    """True when a FRESH ``page_recall_cleared`` event cleared *slug*'s gate.

    Scans the run ledger for an event of type ``page_recall_cleared`` whose
    ``concept_id`` is *slug* and whose ``data`` marks the page either
    recall-satisfied (``recall_ok == true``) or mined out
    (``exhausted == true``).

    The clearance is FRESH only if it is the latest such event AND it is
    newer (later in ledger order) than the latest ``evidence_added`` event
    for the slug. If any ``evidence_added`` postdates the clearance, evidence
    changed after the gate was cleared, so the clearance is STALE and this
    returns ``False``. Uses ledger ORDER (the same signal ``_growth_stalled``
    keys off), so no hashing is needed.
    """
    last_cleared = -1
    last_evidence = -1
    for idx, ev in enumerate(read_events(bundle)):
        if ev.concept_id != slug:
            continue
        if ev.type == "evidence_added":
            last_evidence = idx
        elif ev.type == "page_recall_cleared" and (
            ev.data.get("recall_ok") is True or ev.data.get("exhausted") is True
        ):
            last_cleared = idx
    return last_cleared > last_evidence


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
    owner: str | None = typer.Option(
        None,
        "--owner",
        help=(
            "Claim owner string. Defaults to 'investigate' when omitted. "
            "Override to match the owner used when the claim was acquired."
        ),
    ),
    fmt: str = typer.Option("auto", "--format", help="json | compact | auto"),
    require_recall: bool = typer.Option(
        False,
        "--require-recall",
        help=(
            "Hard-enforce the evidence-recall gate for article pages: refuse "
            "to commit unless a `page_recall_cleared` event (recall_ok or "
            "exhausted) was recorded for this slug. Off by default; person "
            "and data pages are exempt."
        ),
    ),
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

    # Step 0: ownership gate. If another owner holds a live claim on this
    # slug, do not normalize / check / commit — exit before any mutation.
    canonical_owner = cli_owner(owner or "investigate")
    existing_claim = read_claim(bundle, concept)
    if existing_claim and existing_claim.get("owner") != canonical_owner:
        steps.append({
            "step": "claim-check",
            "ok": False,
            "error": "claim_held",
            "owner": existing_claim.get("owner"),
            "acquired_at": existing_claim.get("acquired_at"),
        })
        _emit_finalize(envelope, fmt_resolved)
        raise typer.Exit(code=EXIT_LOCK_HELD)

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

    # Step 2b: opt-in evidence-recall gate. Only article pages are gated;
    # person and data pages have their own gates elsewhere. When the flag is
    # off, the default path is untouched.
    if require_recall and load_card(bundle, concept).kind == "article":
        if not _recall_cleared(bundle, concept):
            steps.append({
                "step": "recall-gate",
                "ok": False,
                "error": "recall_not_cleared",
                "message": (
                    f"evidence-recall gate not cleared for {concept}; run "
                    "`wikify work concept-recall` and record page_recall_cleared, "
                    "or pass nothing to skip"
                ),
            })
            _emit_finalize(envelope, fmt_resolved)
            raise typer.Exit(code=EXIT_VALIDATION)
        steps.append({"step": "recall-gate", "ok": True})

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

    # Step 4: release the per-concept claim. Ownership was gated at Step 0,
    # so a False return here means "no live claim" (the no-claim branch
    # matches `work release`'s behaviour).
    typer.echo(f"finalize: release {concept}", err=True)
    released = release_claim(bundle, concept, owner=canonical_owner)
    steps.append({"step": "release", "ok": True, "released": released})

    envelope["ok"] = True
    _emit_finalize(envelope, fmt_resolved)


__all__ = ["app"]
