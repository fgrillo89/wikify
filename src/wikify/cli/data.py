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
    wikify data reindex --run <bundle>
    wikify data relabel <claim_id> --subject S [--property P] --run <bundle>
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
    check_data_page_id_free,
    register_artifact_wiki_page,
    write_artifact_page,
)
from ..data.consolidate import SubjectFilterUnmatchedError, consolidate
from ..data.harvest import source_text_for, sweep_property_candidates
from ..data.models import ArtifactSpec, DataPoint, normalize_key
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
    except SubjectFilterUnmatchedError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="subjects_filter_unmatched",
            message=str(exc),
            requested=exc.requested,
        )

app = typer.Typer(add_completion=False, help="Factual-data claim store + data artifacts.")

# Recommended minimum alias phrasings (incl. the canonical name) for a
# whole-corpus property sweep; fewer risks missing paraphrased mentions.
PROPERTY_ALIAS_MIN = 3

# Data-recall commit gate thresholds. A property broadly reported across the
# corpus (>= this many docs mention it) but thinly extracted (recall below the
# floor) yields a sparse, unrepresentative table; the gate blocks that commit.
DATA_RECALL_DOCS_FLOOR = 10
DATA_RECALL_MIN = 0.75


def _enforce_data_recall(
    store: DataStore, spec: ArtifactSpec, skip_recall: bool
) -> None:
    """Block a data-artifact commit when a spec property is under-harvested.

    For each spec property, a whole-corpus ``harvest-property`` sweep must be
    on record (``get_property_sweep``). The commit is blocked when the sweep
    shows the property is broadly reported but thinly extracted --
    ``docs_mentioning >= DATA_RECALL_DOCS_FLOOR`` and ``data_recall <
    DATA_RECALL_MIN`` -- and also when no sweep exists at all. ``skip_recall``
    bypasses every check; the bypass is logged to stderr.
    """
    if skip_recall:
        typer.echo(
            json.dumps(
                {"warning": "data_recall_gate_bypassed", "properties": spec.properties},
                ensure_ascii=False,
            ),
            err=True,
        )
        return
    for prop in spec.properties:
        sweep = store.get_property_sweep(normalize_key(prop))
        if sweep is None:
            cli_error(
                EXIT_VALIDATION,
                error="data_recall_no_sweep",
                property=prop,
                message=(
                    f"data recall gate: property '{prop}' has no whole-corpus "
                    "sweep on record; run harvest-property first, or pass "
                    "--skip-recall"
                ),
            )
        docs_mentioning = sweep["docs_mentioning"]
        # Measure recall from the LIVE store, not the persisted sweep snapshot.
        # ``harvest-property`` records ``docs_in_table=0`` -- it only enumerates
        # candidate chunks; extraction happens afterwards via ``data add`` and
        # never rewrites the snapshot. Reading the snapshot therefore makes a
        # freshly-extracted property look like recall 0.0, so no first-ever
        # table for a property could ever clear the gate (a chicken-and-egg:
        # ``docs_in_table`` only became non-zero once a table already existed).
        # The live count of distinct verified-claim docs is the true numerator.
        verified_docs = store.property_doc_stats(
            normalize_key(prop)
        )["docs_in_table"]
        # Clamp to 1.0: if the whole-corpus sweep is stale (corpus grew since
        # the last harvest-property) the live verified-doc count can exceed
        # the snapshot's docs_mentioning, which would otherwise report an
        # implausible recall > 1.0. A property extracted from every mentioning
        # doc is simply fully covered.
        recall = round(min(verified_docs / max(docs_mentioning, 1), 1.0), 4)
        if docs_mentioning >= DATA_RECALL_DOCS_FLOOR and recall < DATA_RECALL_MIN:
            cli_error(
                EXIT_VALIDATION,
                error="data_recall_too_low",
                property=prop,
                docs_mentioning=docs_mentioning,
                data_recall=recall,
                message=(
                    f"data recall gate: property '{prop}' has {docs_mentioning} "
                    f"docs mentioning but recall {recall} < 0.75; run "
                    "harvest-property + extract more, or pass --skip-recall"
                ),
            )


def _publish_blockers(
    table, *, spec_subjects: list[str] | None = None
) -> list[dict]:
    """Reasons *table* must not be published.

    Applies to a first-ever publish as well as to overwriting an existing page:
    shipping a table whose spec no longer resolves is wrong either way.

    ONE gate for every publish path (`consolidate --commit`, `commit`,
    `rebuild`). It tests one thing: **does the spec still reference data that
    resolves?**

    - ``missing_subjects``  - a spec subject matches no stored claim at all;
    - a spec subject that resolves to NO ROW in this table. This is the subset
      case ``missing_subjects`` cannot see: it tests ``store.subjects()``, so a
      subject whose label survives on some other property still counts as
      resolving even when the claim THIS table needs went stale;
    - ``empty_columns``     - a spec property contributes no cell anywhere;
    - zero rows             - the projection is empty however it got there.

    Each is a spec/data mismatch with a mechanical fix (a spelling, ``wikify
    data reindex`` after a ``normalize_key`` revision, or verifying the
    claims), and together they are how a stale norm key silently deletes a row
    or blanks a column on a committed page.

    What this deliberately does NOT block is EVOLUTION. An earlier version
    keyed on "no claim the committed page cites may disappear", which is a
    stronger invariant than the subsystem can support: a data artifact is a
    materialized view that re-derives as evidence lands, so claims churn by
    design. One new paper reporting a conflicting value collapses agreeing
    claims into a conflict cell and legitimately drops citations - under the
    evidence rule that permanently jammed the artifact, on the single most
    normal event in a literature corpus. Narrowing caused by real data
    movement is the feature; narrowing caused by a spec that no longer
    resolves is the bug.
    """
    blockers: list[dict] = []
    if table.missing_subjects:
        blockers.append({
            "error": "subjects_filter_partially_unmatched",
            "missing_subjects": table.missing_subjects,
            "message": (
                f"{len(table.missing_subjects)} spec subject(s) match no stored "
                f"claim ({table.missing_subjects}); publishing would ship a "
                "table without their rows. Fix the spec spelling, run `wikify "
                "data reindex`, or drop them from `subjects`."
            ),
        })
    if spec_subjects:
        rendered = {normalize_key(r["subject"]) for r in table.rows}
        without_rows = [
            subj for subj in spec_subjects if normalize_key(subj) not in rendered
        ]
        if without_rows:
            blockers.append({
                "error": "subjects_without_rows",
                "subjects_without_rows": without_rows,
                "message": (
                    f"{len(without_rows)} spec subject(s) contribute no row to "
                    f"this table ({without_rows}); the page would not carry "
                    "them. Possible causes: no claim for this subject and "
                    "property yet, a stale `subject_norm` (run `wikify data "
                    "reindex`), a spelling that differs from the spec, or a "
                    "verification status the spec's `min_verification` excludes."
                ),
            })
    if table.empty_columns:
        blockers.append({
            "error": "properties_without_cells",
            "empty_columns": table.empty_columns,
            "message": (
                f"{len(table.empty_columns)} spec propert(ies) contribute no "
                f"cell to this table ({table.empty_columns}); the page would "
                "carry a blank column and none of its references. Possible "
                "causes: no claim for this property yet, a stale "
                "`property_norm` (run `wikify data reindex`), a spelling that "
                "differs from the spec, or a verification status the spec's "
                "`min_verification` excludes (the default admits `verified` "
                "and `conflict`). Check `wikify data list --property <p>`."
            ),
        })
    if table.n_rows == 0:
        blockers.append({
            "error": "empty_table",
            "message": (
                "the table resolves to zero rows; the page would be empty. "
                "Check the spec's properties and subjects against `wikify data "
                "list`."
            ),
        })
    return blockers


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


@app.command("reindex")
def cmd_reindex(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Recompute ``subject_norm`` / ``property_norm`` over every stored claim.

    The norm columns are derived by ``normalize_key`` at write time, so points
    written by an older revision of it keep the older spelling and every
    norm-keyed lookup (the ``subjects`` filter of a data-artifact spec,
    ``data list --subject``, the property registry) silently misses them. Run
    this after upgrading, or when a spec's ``subjects`` filter matches nothing
    you can see in ``data list``.
    """
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        result = store.reindex_norm_keys()
    finally:
        store.close()
    _emit({"ok": True, **result}, fmt)


@app.command("relabel")
def cmd_relabel(
    claim_id: str = typer.Argument(..., help="Claim id to relabel."),
    run: Path | None = typer.Option(None, "--run"),
    subject: str | None = typer.Option(None, "--subject", help="New subject text."),
    property: str | None = typer.Option(None, "--property", help="New property text."),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Relabel even when a committed data artifact filters on the old "
            "subject OR property key and would lose this row on its next "
            "rebuild."
        ),
    ),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Rewrite a claim's ``subject`` / ``property`` label without touching its
    value or provenance.

    The label is presentation (it becomes a table row/column heading), so
    rewriting it (e.g. from ASCII ``sigma_D`` to inline LaTeX
    ``$\\sigma_D$``) must not require re-extracting and re-verifying it. The
    verification gate is unaffected: it grounds the VALUE in the source quote,
    which this never changes. Derived norm keys are recomputed.
    """
    if subject is None and property is None:
        cli_error(
            EXIT_VALIDATION,
            error="nothing_to_do",
            message="pass --subject and/or --property",
        )
    bundle = _resolve_bundle(run)
    store = DataStore.open(bundle.root)
    try:
        row = store.get_point(claim_id)
        if row is None:
            cli_error(EXIT_VALIDATION, error="not_found", message=claim_id)
        # A relabel rewrites the norm key a committed spec filters on -
        # `subject_norm` for its rows, `property_norm` for its columns - so the
        # row silently disappears from any page built on the old spelling.
        # Both axes are checked; `spec.properties` is mandatory where
        # `subjects` is optional, so the property is the likelier orphan path.
        affected = store.artifacts_naming(
            subject_norm=row["subject_norm"] if subject is not None else None,
            property_norm=row["property_norm"] if property is not None else None,
        )
        if affected and not force:
            cli_error(
                EXIT_VALIDATION,
                error="committed_artifact_filters_on_label",
                artifacts=affected,
                message=(
                    f"{len(affected)} committed artifact(s) filter on the "
                    f"current label and would lose this row on the next "
                    f"rebuild: {[a['artifact_id'] for a in affected]}. Update "
                    "the matching spec subjects/properties to the new label "
                    "too, or pass --force."
                ),
            )
        updated = store.relabel_point(claim_id, subject=subject, property=property)
    finally:
        store.close()
    _emit(
        {"ok": True, "claim_id": claim_id, **updated,
         "artifacts_needing_spec_update": affected},
        fmt,
    )


@app.command("harvest-property")
def cmd_harvest_property(
    property: str = typer.Option(..., "--property", help="Canonical property name."),
    alias: list[str] = typer.Option([], "--alias", help="Alias phrasing (repeatable)."),
    unit: list[str] = typer.Option([], "--unit", help="Unit token (repeatable)."),
    run: Path | None = typer.Option(None, "--run", help="Bundle directory."),
    corpus: Path | None = typer.Option(None, "--corpus"),
    max_chunks: int = typer.Option(500, "--max-chunks", help="Candidate-chunk cap."),
    include_text: bool = typer.Option(
        False, "--include-text", help="Also emit each candidate's chunk text."
    ),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """Enumerate every corpus chunk mentioning a property and report recall.

    Runs a whole-corpus sweep for the canonical name plus its aliases/units,
    surfaces the candidate worklist, and computes ``data_recall`` (docs already
    represented in the table over docs mentioning the property). It does NOT
    add points -- the extract-data agent verifies + ingests each candidate via
    ``data add``; this only shows what still needs harvesting.
    """
    bundle = _resolve_bundle(run)
    cor = _resolve_corpus(bundle, corpus)
    if cor is None:
        cli_error(
            EXIT_VALIDATION,
            error="no_corpus",
            message=(
                "could not resolve a corpus to sweep. Pass --corpus <path> or "
                "fix run/state.json corpus_path."
            ),
        )
    phrasings = [property, *(alias or [])]
    sweep = sweep_property_candidates(
        cor, phrasings=phrasings, units=list(unit or []),
        max_chunks=max_chunks, include_text=include_text,
    )
    pnorm = normalize_key(property)
    store = DataStore.open(bundle.root)
    try:
        stats = store.property_doc_stats(pnorm)
        store.record_property_sweep(
            property=property,
            property_norm=pnorm,
            docs_mentioning=len(sweep["docs_mentioning"]),
            docs_extracted=stats["docs_extracted"],
            docs_in_table=stats["docs_in_table"],
            candidate_chunks=sweep["candidate_chunks"],
            truncated=sweep["truncated"],
        )
    finally:
        store.close()
    docs_mentioning = len(sweep["docs_mentioning"])
    report = {
        "property": property,
        "docs_mentioning_property": docs_mentioning,
        "candidate_chunks": sweep["candidate_chunks"],
        "docs_in_table": stats["docs_in_table"],
        "data_recall": round(stats["docs_in_table"] / max(docs_mentioning, 1), 4),
        "truncated": sweep["truncated"],
    }
    n_phrasings = len({normalize_key(p) for p in phrasings if p.strip()})
    payload = {
        "ok": True,
        "report": report,
        "docs_extracted": stats["docs_extracted"],
        "matched_chunks": sweep["matched_chunks"],
        "candidates": sweep["candidates"],
    }
    if n_phrasings < PROPERTY_ALIAS_MIN:
        payload["warning"] = (
            f"only {n_phrasings} phrasing(s) supplied; >= {PROPERTY_ALIAS_MIN} "
            "recommended so paraphrased mentions are not missed"
        )
    if fmt == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(_human(report))
    if "warning" in payload:
        typer.echo(f"warning: {payload['warning']}")


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
    require_recall: bool = typer.Option(
        False,
        "--require-recall",
        help=(
            "Hard-enforce the data-recall gate on --commit: refuse the commit "
            "when a spec property mentioned in >= 10 docs has recall < 0.75, or "
            "when a property has no harvest-property sweep on record. Off by "
            "default. Bypass a single commit with --skip-recall."
        ),
    ),
    skip_recall: bool = typer.Option(
        False,
        "--skip-recall",
        help="Bypass the --require-recall data-recall gate (logs the bypass).",
    ),
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
            if commit and require_recall:
                _enforce_data_recall(store, artifact_spec, skip_recall)
            table = consolidate(store, artifact_spec)
            blockers = _publish_blockers(table, spec_subjects=artifact_spec.subjects)
            already_published = (store.get_artifact(artifact_spec.artifact_id) or {}
                                 ).get("status") == "committed"
            if commit and blockers:
                # Refuse BEFORE touching the store or the disk. Narrowing
                # `n_rows` / `data_artifact_claims` and then refusing would
                # desync the claim-link graph from a page that still cites the
                # dropped rows.
                cli_error(EXIT_VALIDATION, error=blockers[0]["error"],
                          blockers=blockers, message=blockers[0]["message"])
            if commit:
                # Preflight the page-id collision BEFORE any store or file
                # write, so a rejected commit leaves nothing half-applied.
                check_data_page_id_free(
                    bundle, artifact_spec.title, artifact_spec.artifact_id
                )
            if commit or not already_published:
                # Only a COMMIT may rewrite the stored artifact once it is
                # published. A draft build that touched it would rewrite
                # `spec_json`, `n_rows` and the claim links of a page it
                # explicitly declined to publish - and `rebuild` reads
                # `spec_json`, so the next unattended sweep would ship the
                # draft's spec over the committed page.
                # upsert FIRST: `INSERT OR REPLACE` on data_artifacts fires
                # the ON DELETE CASCADE for data_artifact_claims, so writing
                # the links before it would delete them again.
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
            "missing_subjects": table.missing_subjects,
            "publish_blockers": blockers,
            "available_properties": available,
            "committed": str(page_path) if page_path else False,
        },
        fmt,
    )
    if table.missing_subjects and fmt != "json":
        typer.echo(
            f"warning:  {len(table.missing_subjects)} spec subject(s) matched no "
            f"stored claim and produced no rows: {table.missing_subjects}"
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
    require_recall: bool = typer.Option(
        False,
        "--require-recall",
        help=(
            "Hard-enforce the data-recall gate: refuse the commit when a spec "
            "property mentioned in >= 10 docs has recall < 0.75, or when a "
            "property has no harvest-property sweep on record. Off by default. "
            "Bypass a single commit with --skip-recall."
        ),
    ),
    skip_recall: bool = typer.Option(
        False,
        "--skip-recall",
        help="Bypass the --require-recall data-recall gate (logs the bypass).",
    ),
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
            if require_recall:
                _enforce_data_recall(store, spec, skip_recall)
            table = consolidate(store, spec)
            blockers = _publish_blockers(table, spec_subjects=spec.subjects)
            if blockers:
                # The same gate `consolidate --commit` and `rebuild` apply:
                # `commit` is the documented second half of the two-step
                # publish flow, so leaving it open would let the sanctioned
                # path publish what the other two refuse.
                cli_error(EXIT_VALIDATION, error=blockers[0]["error"],
                          blockers=blockers, message=blockers[0]["message"])
            check_data_page_id_free(bundle, spec.title, spec.artifact_id)
            # upsert FIRST — see the cascade note in `cmd_consolidate`.
            store.upsert_artifact(spec, n_rows=table.n_rows)
            store.set_artifact_claims(artifact_id, table.claim_ids)
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
    rebuilt: list[dict] = []
    skipped: list[dict] = []
    with _wiki_mutation_lock(bundle, "rebuild"):
        store = DataStore.open(bundle.root)
        try:
            if artifact_id:
                recs = [store.get_artifact(artifact_id)]
                if recs[0] is None:
                    cli_error(EXIT_VALIDATION, error="not_found", message=artifact_id)
                if recs[0].get("status") != "committed":
                    # Rebuild REFRESHES published pages. Letting it publish a
                    # draft would put a live page on disk without `--commit`
                    # and without the data-recall gate that `commit` enforces.
                    cli_error(
                        EXIT_VALIDATION,
                        error="artifact_not_committed",
                        artifact_id=artifact_id,
                        message=(
                            f"artifact {artifact_id!r} is a draft; `rebuild` "
                            "refreshes committed pages only. Publish it with "
                            "`wikify data commit` first."
                        ),
                    )
            else:
                recs = [r for r in store.list_artifacts() if r["status"] == "committed"]
            for rec in recs:
                spec = ArtifactSpec.from_json(rec["spec_json"])
                try:
                    table = consolidate(store, spec)
                except SubjectFilterUnmatchedError as exc:
                    # Rebuild walks every committed artifact. Aborting on one
                    # bad spec would leave the artifacts already rebuilt in
                    # this loop written to disk and re-registered while the
                    # rest keep stale pages — a half-applied mutation. Skip
                    # the artifact, report it, and let the others finish.
                    skipped.append(
                        {"artifact_id": spec.artifact_id,
                         "error": "subjects_filter_unmatched",
                         "message": str(exc)}
                    )
                    continue
                blockers = _publish_blockers(table, spec_subjects=spec.subjects)
                if blockers:
                    # Rebuild is unattended and walks every committed artifact,
                    # so it is the most destructive place to publish a narrowed
                    # table. Leave the committed page in place and report.
                    skipped.append(
                        {"artifact_id": spec.artifact_id, **blockers[0],
                         "blockers": blockers}
                    )
                    continue
                try:
                    # Preflight BEFORE any store write, and per-artifact: an
                    # uncaught collision would abort the sweep mid-loop, which
                    # is the half-applied mutation the skip above exists to
                    # avoid.
                    check_data_page_id_free(bundle, spec.title, spec.artifact_id)
                except DataPageCollisionError as exc:
                    skipped.append(
                        {"artifact_id": spec.artifact_id,
                         "error": "page_id_collision", "message": str(exc)}
                    )
                    continue
                store.upsert_artifact(spec, n_rows=table.n_rows)
                store.set_artifact_claims(spec.artifact_id, table.claim_ids)
                page_path = write_artifact_page(bundle.wiki_data_dir, spec, table)
                register_artifact_wiki_page(bundle, spec, table)
                rebuilt.append(
                    {"artifact_id": spec.artifact_id, "rows": table.n_rows,
                     "page": str(page_path)}
                )
        finally:
            store.close()
    _emit({"ok": not skipped, "rebuilt": rebuilt, "skipped": skipped}, fmt)
    if skipped:
        # The sweep finished (no half-applied mutation), but committed pages
        # were left stale. Exit non-zero so a batch loop that checks status
        # notices instead of reading it as a clean rebuild.
        raise typer.Exit(code=EXIT_VALIDATION)


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
