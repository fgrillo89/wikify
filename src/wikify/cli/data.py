"""``wikify data`` — factual-data claim store + evolving data artifacts.

Captures extracted numbers/figures into a bundle-scoped claim store with
mechanically-verified provenance, lets writers query them, and consolidates
them into wiki-resident data-artifact tables that re-derive from a durable
spec::

    wikify data add records.jsonl --run <bundle> [--corpus <path>]
    wikify data list [--subject S] [--property P] [--status verified]
    wikify data show <claim_id>
    wikify data query --subject S [--property P] --format json
    wikify data coverage
    wikify data consolidate spec.json --run <bundle>
    wikify data commit <artifact_id> --run <bundle>
    wikify data rebuild [<artifact_id>] --run <bundle>

``add`` runs the hard verification gate: a point is stored only when its
grounding quote and reported number are located in the cited source text
(figure-digitized points are kept but flagged). Rejected points are dropped
and reported.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import typer

from ..api import Bundle, Corpus
from ..bundle.run.lock import LockHeldError, run_lock
from ..bundle.run.state import load_state
from ..data.artifact_page import (
    DataPageCollisionError,
    register_artifact_wiki_page,
    write_artifact_page,
)
from ..data.consolidate import consolidate
from ..data.harvest import source_text_for
from ..data.models import ArtifactSpec, DataPoint
from ..data.store import DataStore
from ..data.verify import verify_point
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error


@contextmanager
def _wiki_mutation_lock(bundle: Bundle, op: str):
    """Serialize a data-artifact wiki mutation (page write + DB registration)
    with `wiki rebuild` / page commits / navigation under the bundle run lock,
    so the wiki.db write and the on-disk page cannot interleave or be left
    half-applied. A held lock exits with EXIT_LOCK_HELD."""
    try:
        with run_lock(bundle, owner=f"data-{op}/pid-{os.getpid()}"):
            yield
    except LockHeldError as exc:
        cli_error(EXIT_LOCK_HELD, error="lock_held", message=str(exc))
    except DataPageCollisionError as exc:
        cli_error(EXIT_VALIDATION, error="page_id_collision", message=str(exc))

app = typer.Typer(add_completion=False, help="Factual-data claim store + data artifacts.")


def _resolve_bundle(bundle_flag: Path | None) -> Bundle:
    if bundle_flag is not None:
        try:
            return Bundle.open(bundle_flag)
        except FileNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    try:
        return Bundle.open(Path.cwd())
    except FileNotFoundError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=f"no bundle resolved; pass --run <bundle>. cause: {exc}",
        )


def _resolve_corpus(bundle: Bundle, corpus_flag: Path | None) -> Corpus | None:
    path = corpus_flag
    if path is None:
        try:
            raw = load_state(bundle).corpus_path
        except FileNotFoundError:
            return None
        # An empty corpus_path would make Path("") resolve to "." (cwd) and
        # Corpus.open succeed against the wrong tree — treat it as unresolved.
        if not raw or not str(raw).strip():
            return None
        path = Path(raw)
    try:
        return Corpus.open(path)
    except FileNotFoundError:
        return None


def _emit(payload: dict, fmt: str) -> None:
    if fmt == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(_human(payload))


def _human(payload: dict) -> str:
    lines = []
    for k, v in payload.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


@app.command("add")
def cmd_add(
    records: Path = typer.Argument(..., help="JSONL of staged data points."),
    run: Path | None = typer.Option(None, "--run", help="Bundle directory."),
    corpus: Path | None = typer.Option(None, "--corpus"),
    keep_rejected: bool = typer.Option(
        False, "--keep-rejected", help="Also store quote-unverifiable points (audit)."
    ),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Verify staged data points against their source and ingest the survivors."""
    bundle = _resolve_bundle(run)
    cor = _resolve_corpus(bundle, corpus)
    if cor is None:
        cli_error(
            EXIT_VALIDATION,
            error="no_corpus",
            message=(
                "could not resolve a corpus to verify against; every point "
                "would be rejected. Pass --corpus <path> or fix "
                "run/state.json corpus_path."
            ),
        )
    if not records.is_file():
        cli_error(EXIT_VALIDATION, error="no_records", message=f"not found: {records}")

    points: list[DataPoint] = []
    for line in records.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            points.append(DataPoint.from_dict(json.loads(line)))
        except json.JSONDecodeError as exc:
            cli_error(EXIT_VALIDATION, error="bad_record", message=str(exc))

    counts: dict[str, int] = {}
    keep: list[DataPoint] = []
    for p in points:
        # cor is guaranteed non-None here (cmd_add errors out otherwise).
        chunk_text, caption, canonical_doc_id = source_text_for(
            cor, doc_id=p.doc_id, chunk_id=p.chunk_id, locator=p.locator
        )
        # Store the canonical doc id from the resolved chunk so claims share
        # the same id space as evidence (consistent downstream joins).
        p.doc_id = canonical_doc_id
        verify_point(p, chunk_text=chunk_text, caption=caption)
        counts[p.verification_status] = counts.get(p.verification_status, 0) + 1
        if p.verification_status == "rejected" and not keep_rejected:
            continue
        keep.append(p)

    store = DataStore.open(bundle.root)
    try:
        result = store.add_points(keep)
    finally:
        store.close()

    _emit(
        {
            "ok": True,
            "submitted": len(points),
            "verified": counts.get("verified", 0),
            "rejected": counts.get("rejected", 0),
            "figure_digitized": counts.get("figure_digitized", 0),
            "stored": result["added"],
            "duplicate": result["duplicate"],
        },
        fmt,
    )


@app.command("list")
def cmd_list(
    run: Path | None = typer.Option(None, "--run"),
    subject: str | None = typer.Option(None, "--subject"),
    property: str | None = typer.Option(None, "--property"),
    status: str | None = typer.Option(None, "--status"),
    limit: int = typer.Option(0, "--limit"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """List stored data points (filtered)."""
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        rows = store.list_points(
            subject=subject, property=property, status=status, limit=limit
        )
    finally:
        store.close()
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "points": rows}, ensure_ascii=False))
        return
    if not rows:
        typer.echo("(no data points)")
        return
    for r in rows:
        unit = f" {r['unit']}" if r["unit"] else ""
        typer.echo(
            f"[{r['verification_status']}] {r['subject']} | {r['property']} = "
            f"{r['value_text']}{unit}  <{r['doc_id']}>"
        )


@app.command("show")
def cmd_show(
    claim_id: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """Show one data point by claim id."""
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        row = store.get_point(claim_id)
    finally:
        store.close()
    if row is None:
        cli_error(EXIT_VALIDATION, error="not_found", message=claim_id)
    typer.echo(json.dumps({"ok": True, "point": row}, ensure_ascii=False))


@app.command("query")
def cmd_query(
    run: Path | None = typer.Option(None, "--run"),
    subject: str | None = typer.Option(None, "--subject"),
    property: str | None = typer.Option(None, "--property"),
    status: str = typer.Option("verified", "--status"),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """Return claims as a compact table for a writer to cite or embed."""
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        rows = store.list_points(subject=subject, property=property, status=status)
    finally:
        store.close()
    compact = [
        {
            "subject": r["subject"],
            "property": r["property"],
            "value": r["value_text"],
            "unit": r["unit"],
            "doc_id": r["doc_id"],
            "chunk_id": r["chunk_id"],
            "quote": r["grounding_quote"],
            "claim_id": r["claim_id"],
        }
        for r in rows
    ]
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "rows": compact}, ensure_ascii=False))
        return
    for r in compact:
        typer.echo(f"{r['subject']} | {r['property']} = {r['value']} {r['unit']}".rstrip())


@app.command("coverage")
def cmd_coverage(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """Summarize the claim store (counts, verified ratio, subjects/properties)."""
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        cov = store.coverage()
        cov["subjects"] = [s["subject"] for s in store.subjects()[:25]]
        cov["properties"] = [p["property_norm"] for p in store.properties()[:25]]
    finally:
        store.close()
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **cov}, ensure_ascii=False))
        return
    typer.echo(_human(cov))


def _load_spec(spec_path: Path | None) -> ArtifactSpec:
    if spec_path is not None:
        if not spec_path.is_file():
            cli_error(EXIT_VALIDATION, error="no_spec", message=str(spec_path))
        raw = spec_path.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    try:
        return ArtifactSpec.from_json(raw)
    except (json.JSONDecodeError, KeyError) as exc:
        cli_error(EXIT_VALIDATION, error="bad_spec", message=str(exc))


@app.command("consolidate")
def cmd_consolidate(
    spec: Path | None = typer.Argument(None, help="Artifact spec JSON (or stdin)."),
    run: Path | None = typer.Option(None, "--run"),
    commit: bool = typer.Option(False, "--commit", help="Also write the wiki page."),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Build a data-artifact table from a spec and store it (draft).

    With ``--commit`` the page + sidecar are written under ``wiki/data/``
    immediately.
    """
    bundle = _resolve_bundle(run)
    artifact_spec = _load_spec(spec)
    with _wiki_mutation_lock(bundle, "consolidate"):
        store = DataStore.open(bundle.root)
        try:
            table = consolidate(store, artifact_spec)
            store.upsert_artifact(artifact_spec, n_rows=table.n_rows)
            store.set_artifact_claims(artifact_spec.artifact_id, table.claim_ids)
            available = (
                [p["property_norm"] for p in store.properties()]
                if table.empty_columns else []
            )
            page_path = None
            if commit:
                page_path = write_artifact_page(bundle.wiki_data_dir, artifact_spec, table)
                register_artifact_wiki_page(bundle, artifact_spec, table)
                store.set_artifact_status(artifact_spec.artifact_id, "committed")
        finally:
            store.close()
    _emit(
        {
            "ok": True,
            "artifact_id": artifact_spec.artifact_id,
            "rows": table.n_rows,
            "columns": table.columns,
            "claims": len(table.claim_ids),
            "conflicts": table.n_conflicts,
            "empty_columns": table.empty_columns,
            "available_properties": available,
            "committed": str(page_path) if page_path else False,
        },
        fmt,
    )
    if table.empty_columns and fmt != "json":
        typer.echo(
            f"warning:  {len(table.empty_columns)} spec propert(ies) matched no "
            f"stored claims: {table.empty_columns}"
        )
        typer.echo(f"available property_norms: {available}")


@app.command("commit")
def cmd_commit(
    artifact_id: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Write a stored artifact's page + sidecar under ``wiki/data/``."""
    bundle = _resolve_bundle(run)
    with _wiki_mutation_lock(bundle, "commit"):
        store = DataStore.open(bundle.root)
        try:
            rec = store.get_artifact(artifact_id)
            if rec is None:
                cli_error(EXIT_VALIDATION, error="not_found", message=artifact_id)
            spec = ArtifactSpec.from_json(rec["spec_json"])
            table = consolidate(store, spec)
            store.set_artifact_claims(artifact_id, table.claim_ids)
            store.upsert_artifact(spec, n_rows=table.n_rows)
            page_path = write_artifact_page(bundle.wiki_data_dir, spec, table)
            register_artifact_wiki_page(bundle, spec, table)
            store.set_artifact_status(artifact_id, "committed")
        finally:
            store.close()
    _emit(
        {"ok": True, "artifact_id": artifact_id, "page": str(page_path), "rows": table.n_rows},
        fmt,
    )


@app.command("rebuild")
def cmd_rebuild(
    artifact_id: str | None = typer.Argument(None),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Re-derive committed artifact pages from their stored specs.

    Run after new claims/papers land so each data table reflects the current
    claim store (the evolving-artifact property).
    """
    bundle = _resolve_bundle(run)
    rebuilt = []
    with _wiki_mutation_lock(bundle, "rebuild"):
        store = DataStore.open(bundle.root)
        try:
            if artifact_id:
                recs = [store.get_artifact(artifact_id)]
                if recs[0] is None:
                    cli_error(EXIT_VALIDATION, error="not_found", message=artifact_id)
            else:
                recs = [r for r in store.list_artifacts() if r["status"] == "committed"]
            for rec in recs:
                spec = ArtifactSpec.from_json(rec["spec_json"])
                table = consolidate(store, spec)
                store.set_artifact_claims(spec.artifact_id, table.claim_ids)
                store.upsert_artifact(spec, n_rows=table.n_rows)
                page_path = write_artifact_page(bundle.wiki_data_dir, spec, table)
                register_artifact_wiki_page(bundle, spec, table)
                rebuilt.append(
                    {"artifact_id": spec.artifact_id, "rows": table.n_rows,
                     "page": str(page_path)}
                )
        finally:
            store.close()
    _emit({"ok": True, "rebuilt": rebuilt}, fmt)


@app.command("list-artifacts")
def cmd_list_artifacts(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """List stored data artifacts."""
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        rows = store.list_artifacts()
    finally:
        store.close()
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "artifacts": rows}, ensure_ascii=False))
        return
    if not rows:
        typer.echo("(no artifacts)")
        return
    for r in rows:
        typer.echo(f"[{r['status']}] {r['artifact_id']} ({r['n_rows']} rows) — {r['title']}")


__all__ = ["app"]
