"""wikify bundle ... — promote validated responses into canonical bundle files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import typer

from ..context import response_reserve, total_context
from ..distill.write_runner import rebuild_wiki_graph
from ..meter import CallRecord
from ..models import WikiPage
from ..paths import BundlePaths
from ..schema import WriteResponse
from ..session import (
    apply_merge_patch,
    load_session,
    save_session,
    session_lock,
    touch,
)
from ..store.wiki_bundle import load_bundle
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from ..types import ModelTier, Role
from ._helpers import cli_error, cli_owner, handle_lock_held, strip_envelope
from .meter import BudgetExceededError, check_budget_gate, haiku_eq_for


def _write_context_cap() -> int:
    """Match legacy dispatch: `total_context() - response_reserve()`.

    Using the hardcoded 200_000 Claude context window would silently
    inflate every headroom metric by ~80k and break parity with the
    legacy CostMeter snapshot.
    """
    return total_context() - response_reserve()


app = typer.Typer(add_completion=False, help="Promote validated drafts into bundle artifacts.")


@app.command("commit-page")
def cmd_commit_page(
    session_path: Path = typer.Option(..., "--session"),
    response: Path = typer.Option(..., "--response", help="Path to response-<page_id>.json."),
    validation: Path = typer.Option(
        ...,
        "--validation",
        help=(
            "Path to validation-<page_id>.json with ok=true. Required: "
            "commit-page enforces the atoms.md precondition that the page "
            "has passed `wikify validate write` before promotion."
        ),
    ),
    owner: str | None = typer.Option(None, "--owner"),
) -> None:
    """Promote a validated WriteResponse into pages/<id>.md and update the session.

    Preconditions enforced (per reference/atoms.md commit-page):
      - The --validation verdict's ok field is true.
      - The session page entry's status is `validated` (set by
        `wikify validate write --session`).
    If either fails, no canonical mutation happens.
    """
    response_data = json.loads(response.read_text(encoding="utf-8"))
    parsed = WriteResponse.model_validate(strip_envelope(response_data))

    verdict = json.loads(validation.read_text(encoding="utf-8"))
    if not verdict.get("ok", False):
        cli_error(
            1,
            error="validation_failed",
            verdict_path=str(validation),
            errors=verdict.get("errors", []),
        )

    # Bind the verdict to THIS response. A stale ok=true verdict must
    # not authorise a different or later-edited response.
    verdict_page_id = verdict.get("page_id")
    if verdict_page_id != parsed.page_id:
        cli_error(
            1,
            error="verdict_page_id_mismatch",
            verdict_page_id=verdict_page_id,
            response_page_id=parsed.page_id,
        )
    verdict_response_path = verdict.get("response_path")
    if verdict_response_path and Path(verdict_response_path).resolve() != response.resolve():
        cli_error(
            1,
            error="verdict_response_path_mismatch",
            verdict_response_path=verdict_response_path,
            response_path=str(response.resolve()),
        )
    verdict_sha = verdict.get("response_sha256")
    if verdict_sha:
        actual_sha = hashlib.sha256(response.read_bytes()).hexdigest()
        if actual_sha != verdict_sha:
            cli_error(
                1,
                error="response_content_changed",
                verdict_sha256=verdict_sha,
                response_sha256=actual_sha,
                message=(
                    "response bytes changed since the verdict was recorded; "
                    "re-run `wikify validate write` to refresh the verdict"
                ),
            )

    session = load_session(session_path)
    bundle_paths = BundlePaths(Path(session.bundle_root))
    bundle_paths.ensure()

    # Acquire the lock BEFORE any canonical mutation. Every disk write
    # (page file, index, wiki graph, session.json) happens under the lock
    # so a lock_held failure leaves no partial state.
    with handle_lock_held():
        with session_lock(session_path, owner=cli_owner(owner)):
            # Re-read session state under the lock so we patch the latest.
            fresh = load_session(session_path)
            page_entry = next(
                (p for p in fresh.pages if p.page_id == parsed.page_id), None
            )
            if page_entry is None or page_entry.status != "validated":
                cli_error(
                    1,
                    error="precondition_not_met",
                    page_id=parsed.page_id,
                    expected_status="validated",
                    actual_status=page_entry.status if page_entry else "<missing>",
                    message=(
                        "commit-page requires the session page entry "
                        "to be status=validated. Run "
                        "`wikify validate write --session <p>` first so "
                        "the validated transition is recorded."
                    ),
                )

            # ------------------------------------------------------------
            # All validation + budget gating runs BEFORE any canonical
            # disk write. If we refuse the commit, no page file, no index
            # rebuild, no wiki-graph rebuild has happened — the bundle
            # stays in its pre-commit state.
            # ------------------------------------------------------------
            write_tier_str = fresh.config.default_tiers.get("write", "M")
            try:
                write_tier = ModelTier(write_tier_str)
            except ValueError:
                cli_error(
                    1,
                    error="invalid_write_tier",
                    tier=write_tier_str,
                    message=(
                        "session.config.default_tiers['write'] is not a "
                        "valid ModelTier (expected one of S, M, L)"
                    ),
                )
            tokens_in = int(parsed.tokens_in)
            tokens_out = int(parsed.tokens_out)
            context_cap = _write_context_cap()
            if tokens_in < 0 or tokens_out < 0:
                cli_error(
                    1,
                    error="negative_tokens",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    message=(
                        "WriteResponse.tokens_in/tokens_out must be "
                        "non-negative; refusing to auto-record"
                    ),
                )
            if tokens_in > context_cap:
                cli_error(
                    1,
                    error="context_overrun",
                    tokens_in=tokens_in,
                    context_cap=context_cap,
                )
            haiku_eq = haiku_eq_for(write_tier, tokens_in, tokens_out)
            projected_spent = float(fresh.budget.haiku_eq_spent) + float(haiku_eq)

            # Build the CallRecord early so we can append it BEFORE the
            # budget gate raises — matching legacy CostMeter.record which
            # appends the breaching record and updates aggregates before
            # aborting (see src/wikify/meter.py:250-256).
            call_record = CallRecord(
                role=Role.WRITER,
                tier=write_tier,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                context_used=tokens_in,
                context_cap=context_cap,
                wall_seconds=0.0,
                cache_hit=False,
                prompt_hash="",
                haiku_eq=haiku_eq,
            )
            bundle_paths.calls_path.parent.mkdir(parents=True, exist_ok=True)
            with bundle_paths.calls_path.open("a", encoding="utf-8") as fh:
                fh.write(call_record.to_json() + "\n")
            # Bump session spent to reflect the appended record.
            budget_patch = apply_merge_patch(
                fresh, {"budget": {"haiku_eq_spent": projected_spent}}
            )
            save_session(session_path, touch(budget_patch))
            try:
                check_budget_gate(
                    float(fresh.budget.haiku_eq_target), projected_spent
                )
            except BudgetExceededError as exc:
                cli_error(
                    3,
                    error="budget_exceeded",
                    spent=exc.spent,
                    target=exc.target,
                    ratio=exc.ratio,
                )

            # ------------------------------------------------------------
            # Gate cleared — promotion to canonical artifacts runs.
            # ------------------------------------------------------------
            # Prefer the session's canonicalize-time kind/aliases over
            # whatever the subagent put on the response. Mismatch is a
            # contract violation: the canonicalize step is the source
            # of truth for what kind of page this is.
            session_kind = page_entry.kind
            response_kind = parsed.page_kind or session_kind
            if response_kind and response_kind != session_kind:
                cli_error(
                    1,
                    error="page_kind_mismatch",
                    session_kind=session_kind,
                    response_page_kind=response_kind,
                    message=(
                        "WriteResponse.page_kind disagrees with "
                        "session.pages[<id>].kind set at canonicalize "
                        "time; refusing to commit"
                    ),
                )
            page = WikiPage(
                id=parsed.page_id,
                kind=session_kind,  # type: ignore[arg-type]
                title=parsed.page_id,
                aliases=list(page_entry.aliases),
                body_markdown=parsed.body_markdown,
                evidence=[],
                provenance={
                    "session_id": fresh.session_id,
                    "strategy": fresh.strategy,
                    "committed_via": "wikify bundle commit-page",
                },
            )
            page_path = write_page_file(bundle_paths, page)

            # Rebuild indices over ALL committed pages on disk.
            loaded = load_bundle(bundle_paths.root)
            wiki_pages = [_page_to_wiki_page(p) for p in loaded.pages]
            build_index(bundle_paths, wiki_pages).save()
            rebuild_wiki_graph(bundle_paths, wiki_pages)

            # Final session patch: mark the page committed.
            reread = load_session(session_path)
            new_pages = [p.model_dump(mode="json") for p in reread.pages]
            for entry in new_pages:
                if entry["page_id"] == parsed.page_id:
                    entry["status"] = "committed"
                    entry["validation_path"] = str(validation)
                    break
            updated = apply_merge_patch(reread, {"pages": new_pages})
            save_session(session_path, touch(updated))

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
