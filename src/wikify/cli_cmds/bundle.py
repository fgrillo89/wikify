"""wikify bundle ... — promote validated responses into canonical bundle files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import typer

from ..distill.write_runner import rebuild_wiki_graph
from ..meter import CallRecord
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
from ..types import ModelTier, Role
from .meter import BudgetExceededError, check_budget_gate, haiku_eq_for

app = typer.Typer(add_completion=False, help="Promote validated drafts into bundle artifacts.")


def _cli_owner(override: str | None) -> str:
    return override or f"wikify-cli/pid-{os.getpid()}"


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
    # Same envelope-stripping contract as `wikify validate write`: scratch
    # writers may attach schema_version, but the canonical Pydantic model
    # is extra="forbid".
    response_data_clean = {k: v for k, v in response_data.items() if k != "schema_version"}
    parsed = WriteResponse.model_validate(response_data_clean)

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

    # Bind the verdict to THIS response. A stale ok=true verdict must
    # not authorise a different or later-edited response.
    verdict_page_id = verdict.get("page_id")
    if verdict_page_id != parsed.page_id:
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "error": "verdict_page_id_mismatch",
                    "verdict_page_id": verdict_page_id,
                    "response_page_id": parsed.page_id,
                }
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    verdict_response_path = verdict.get("response_path")
    if verdict_response_path and Path(verdict_response_path).resolve() != response.resolve():
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "error": "verdict_response_path_mismatch",
                    "verdict_response_path": verdict_response_path,
                    "response_path": str(response.resolve()),
                }
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    verdict_sha = verdict.get("response_sha256")
    if verdict_sha:
        actual_sha = hashlib.sha256(response.read_bytes()).hexdigest()
        if actual_sha != verdict_sha:
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "response_content_changed",
                        "verdict_sha256": verdict_sha,
                        "response_sha256": actual_sha,
                        "message": (
                            "response bytes changed since the verdict was recorded; "
                            "re-run `wikify validate write` to refresh the verdict"
                        ),
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
            if page_entry is None or page_entry.status != "validated":
                actual = page_entry.status if page_entry else "<missing>"
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "precondition_not_met",
                            "page_id": parsed.page_id,
                            "expected_status": "validated",
                            "actual_status": actual,
                            "message": (
                                "commit-page requires the session page entry "
                                "to be status=validated. Run "
                                "`wikify validate write --session <p>` first so "
                                "the validated transition is recorded."
                            ),
                        }
                    ),
                    err=True,
                )
                raise typer.Exit(code=1)
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

            # Auto-record the write call in _calls.jsonl. The skill
            # path does not hold a CostMeter instance; this entry is
            # the skill-side equivalent of legacy meter.record() after
            # a write dispatch. Tokens come from the response itself;
            # tier comes from session.config.default_tiers["write"].
            #
            # Validation contract (mirrors `wikify meter record`):
            #   - misconfigured tier: fail loudly, do not silently default
            #   - negative tokens: reject (would decrement budget)
            #   - tokens_in > context_cap: reject
            #   - 1.05x budget overrun: refuse like legacy CostMeter
            write_tier_str = fresh.config.default_tiers.get("write", "M")
            try:
                write_tier = ModelTier(write_tier_str)
            except ValueError:
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "invalid_write_tier",
                            "tier": write_tier_str,
                            "message": (
                                "session.config.default_tiers['write'] is not a "
                                "valid ModelTier (expected one of S, M, L)"
                            ),
                        }
                    ),
                    err=True,
                )
                raise typer.Exit(code=1) from None
            tokens_in = int(parsed.tokens_in)
            tokens_out = int(parsed.tokens_out)
            context_cap = 200_000
            if tokens_in < 0 or tokens_out < 0:
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "negative_tokens",
                            "tokens_in": tokens_in,
                            "tokens_out": tokens_out,
                            "message": (
                                "WriteResponse.tokens_in/tokens_out must be "
                                "non-negative; refusing to auto-record"
                            ),
                        }
                    ),
                    err=True,
                )
                raise typer.Exit(code=1)
            if tokens_in > context_cap:
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "context_overrun",
                            "tokens_in": tokens_in,
                            "context_cap": context_cap,
                        }
                    ),
                    err=True,
                )
                raise typer.Exit(code=1)
            haiku_eq = haiku_eq_for(write_tier, tokens_in, tokens_out)
            projected_spent = float(fresh.budget.haiku_eq_spent) + float(haiku_eq)
            try:
                check_budget_gate(
                    float(fresh.budget.haiku_eq_target), projected_spent
                )
            except BudgetExceededError as exc:
                typer.echo(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "budget_exceeded",
                            "spent": exc.spent,
                            "target": exc.target,
                            "ratio": exc.ratio,
                        }
                    ),
                    err=True,
                )
                raise typer.Exit(code=3) from exc
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

            # Update session: mark this page committed + bump spent. page_entry
            # is guaranteed non-None by the precondition check above.
            new_pages = [p.model_dump(mode="json") for p in fresh.pages]
            for entry in new_pages:
                if entry["page_id"] == parsed.page_id:
                    entry["status"] = "committed"
                    entry["validation_path"] = str(validation)
                    break
            updated = apply_merge_patch(
                fresh,
                {"pages": new_pages, "budget": {"haiku_eq_spent": projected_spent}},
            )
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
