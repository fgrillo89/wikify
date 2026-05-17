"""``wikify work ...`` — in-flight build state for wiki bundles.

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

from ..api import Bundle
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
    aliases: str | None = typer.Option(None, "--aliases", help="JSON list of aliases."),
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
    if aliases is not None:
        try:
            alias_list = json.loads(aliases)
            if not isinstance(alias_list, list) or not all(
                isinstance(item, str) for item in alias_list
            ):
                raise ValueError("aliases must be a JSON list of strings")
        except (json.JSONDecodeError, ValueError) as exc:
            cli_error(EXIT_VALIDATION, error="bad_aliases", message=str(exc))
        card.front["aliases"] = alias_list
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


# -------------------------------------------------------------- build-evidence


_NEVER_CITE_PATTERNS = (
    r"\bForm\s+Approved\s+OMB",
    r"\bArticle\s+history\b",
    r"\bReceived[:\s]+\d?\d\s+\w+\s+\d{4}",
    r"\bAvailable\s+online\b",
    r"^\s*Keywords?\s*:\s*\S",
    r"^\s*ISSN[:\s-]+\d{4}",
    r"^\s*DOI\s*:\s*10\.\d{4,9}/",
    r"\bjournal\s+homepage\s*:",
    r"^\s*©\s*\d{4}",
    r"^\s*Copyright\s+©?\s*\d{4}",
    r"\bCorresponding\s+author\b",
    r"@[a-z0-9.-]+\.(?:edu|com|org|gov)",
    r"\borcid\.org/",
    r"^\s*A\s*R\s*T\s*I\s*C\s*L\s*E\s+I\s*N\s*F\s*O",
)


def _matches_never_cite(text: str) -> bool:
    import re as _re

    head = text[:600] if text else ""
    return any(
        _re.search(p, head, _re.IGNORECASE | _re.MULTILINE)
        for p in _NEVER_CITE_PATTERNS
    )


# Section kinds the vetter excludes structurally via corpus-find
# --exclude-kind flags. The --from-ids commit path must enforce the
# same blacklist so a manually-supplied references / caption / etc.
# chunk cannot slip in past the boilerplate + length filters.
_FROM_IDS_EXCLUDED_KINDS = frozenset({
    "references",
    "acknowledgments",
    "appendix",
    "figure",
    "table",
    "caption",
    "boilerplate",
})


def _resolve_doc_id(corpus, short_or_full: str) -> str | None:
    """Map ``doc:<short>`` / ``<short>`` / full id to the full doc_id."""
    from ..corpus.queries import get_doc

    handle = short_or_full
    if handle.startswith("doc:"):
        handle = handle[4:]
    doc = get_doc(corpus, handle)
    return doc.id if doc is not None else None


@app.command("build-evidence")
def cmd_build_evidence(
    concept: str = typer.Argument(...),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    target: int = typer.Option(14, "--target", help="Target active records."),
    top_k: int = typer.Option(40, "--top-k", help="Initial corpus-find depth."),
    per_doc_cap: int = typer.Option(3, "--per-doc-cap"),
    min_chunk_chars: int = typer.Option(80, "--min-chunk-chars"),
    from_ids: str = typer.Option(
        "",
        "--from-ids",
        help=(
            "Commit-only mode: comma-separated chunk_ids. Skips the "
            "seed + corpus-find phases and validates the supplied ids "
            "against the boilerplate, never-cite, and min-chars filters "
            "before appending. Use after an external vetter (e.g. the "
            "wikify-gather-evidence skill) has curated the chunk list."
        ),
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Gather evidence for *concept* using seed_doc_handles + corpus find.

    The extractor's ``seed_doc_handles`` (persisted on the work card)
    serve as a high-precision prior: up to ``per_doc_cap`` body chunks
    are pulled from each seed doc first. The remainder is filled by
    ``corpus find --rank all`` with structural exclusions, until the
    active count reaches ``--target`` or no more candidates pass the
    filters. Every chunk is rejected if its ``is_boilerplate`` flag is
    set or its leading text matches a never-cite pattern (ISSN/DOI
    banner, Article-history, Keywords, affiliation, copyright, OMB
    form, etc.). Per-doc cap keeps a single review paper from
    dominating the page.

    With ``--from-ids <a,b,c>`` the seed + find phases are bypassed and
    the supplied chunk_ids are validated and appended as-is, each with
    ``score=1.0`` and a ``source="vetter"`` tag. ``--target``,
    ``--top-k`` and ``--per-doc-cap`` are ignored in this mode.

    Writes ``work/concepts/<slug>/evidence.jsonl`` and prints stats.
    """
    import sqlite3
    import subprocess

    from ..api import Corpus
    from ..bundle.work.card import load_card
    from ..bundle.work.evidence import (
        EvidenceRecord,
        append_evidence,
        read_evidence,
    )

    bundle = _resolve_bundle(run)
    if not corpus_dir.is_dir():
        cli_error(EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir))
    corpus = Corpus(root=corpus_dir)
    card = load_card(bundle, concept)
    if not card.front:
        cli_error(EXIT_VALIDATION, error="concept_not_found", concept=concept)
    title = card.page_id
    seed_handles = card.front.get("seed_doc_handles") or []

    db_path = corpus.sqlite_path
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    def fetch_chunk(chunk_id: str):
        return con.execute(
            "SELECT chunk_id, doc_id, text, is_boilerplate, section_type "
            "FROM chunks WHERE chunk_id=?",
            (chunk_id,),
        ).fetchone()

    # ----- commit-only mode: skip seed/find, just validate + append.
    if from_ids:
        raw_ids = [s.strip() for s in from_ids.split(",") if s.strip()]
        # Preserve caller order while deduping.
        seen_in: set[str] = set()
        ordered_ids: list[str] = []
        for cid in raw_ids:
            if cid in seen_in:
                continue
            seen_in.add(cid)
            ordered_ids.append(cid)
        if not ordered_ids:
            con.close()
            cli_error(
                EXIT_VALIDATION,
                error="no_ids_provided",
                message="--from-ids requires at least one chunk_id",
            )
        # Only currently-active records block a fresh commit. Archived
        # records sit in the ledger as history; the same chunk_id may be
        # re-accepted (a fresh "active" row supersedes archived ones at
        # dedup time).
        committed = {
            r.chunk_id
            for r in read_evidence(bundle, concept)
            if r.status == "active"
        }
        vetter_stats = {
            "ids_total": len(ordered_ids),
            "appended": 0,
            "rejected_not_found": 0,
            "rejected_boilerplate": 0,
            "rejected_excluded_kind": 0,
            "rejected_never_cite": 0,
            "rejected_short": 0,
            "rejected_already_committed": 0,
        }
        vetter_records: list[dict] = []
        for cid in ordered_ids:
            if cid in committed:
                vetter_stats["rejected_already_committed"] += 1
                continue
            row = fetch_chunk(cid)
            if row is None:
                vetter_stats["rejected_not_found"] += 1
                continue
            if row["is_boilerplate"]:
                vetter_stats["rejected_boilerplate"] += 1
                continue
            section_type = (row["section_type"] or "").lower()
            if section_type in _FROM_IDS_EXCLUDED_KINDS:
                vetter_stats["rejected_excluded_kind"] += 1
                continue
            text = (row["text"] or "").strip()
            if len(text) < min_chunk_chars:
                vetter_stats["rejected_short"] += 1
                continue
            if _matches_never_cite(text):
                vetter_stats["rejected_never_cite"] += 1
                continue
            vetter_records.append(
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "quote": text[:400],
                    "score": 1.0,
                    "status": "active",
                    "source": "vetter",
                }
            )
        con.close()
        if not vetter_records:
            if fmt == "json":
                typer.echo(
                    json.dumps(
                        {"ok": False, "error": "no_evidence", "stats": vetter_stats}
                    )
                )
            else:
                typer.echo(
                    f"{concept}: no evidence appended from --from-ids  "
                    f"stats={vetter_stats}"
                )
            raise typer.Exit(code=EXIT_VALIDATION)
        parsed = [EvidenceRecord.model_validate(r) for r in vetter_records]
        n = append_evidence(bundle, concept, parsed)
        vetter_stats["appended"] = n
        distinct_docs = len({r["doc_id"] for r in vetter_records})
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": True,
                        "concept": concept,
                        "appended": n,
                        "distinct_docs": distinct_docs,
                        "stats": vetter_stats,
                    }
                )
            )
            return
        typer.echo(
            f"{concept}: appended {n} records across {distinct_docs} docs "
            f"(from-ids; rejected=nf{vetter_stats['rejected_not_found']}/"
            f"bp{vetter_stats['rejected_boilerplate']}/"
            f"xk{vetter_stats['rejected_excluded_kind']}/"
            f"nc{vetter_stats['rejected_never_cite']}/"
            f"short{vetter_stats['rejected_short']}/"
            f"dup{vetter_stats['rejected_already_committed']})"
        )
        return

    def fetch_seed_chunks(doc_id: str, limit: int):
        return con.execute(
            "SELECT chunk_id, doc_id, text, is_boilerplate, section_type "
            "FROM chunks WHERE doc_id=? AND is_boilerplate=0 "
            "AND section_type IN "
            "('abstract','introduction','body','discussion','conclusion','methods','results') "
            "ORDER BY ord LIMIT ?",
            (doc_id, limit),
        ).fetchall()

    def find_chunks(query: str, k: int):
        cmd = [
            "wikify", "corpus", "find", query,
            "--rank", "all", "--top-k", str(k),
            "--exclude-kind", "references",
            "--exclude-kind", "acknowledgments",
            "--exclude-kind", "appendix",
            "--exclude-kind", "figure",
            "--exclude-kind", "table",
            "--exclude-kind", "caption",
            "--exclude-kind", "boilerplate",
            "--corpus", str(corpus_dir),
            "--run", str(bundle.root),
            "--format", "json",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            return []
        try:
            return json.loads(r.stdout).get("items", [])
        except Exception:
            return []

    records: list[dict] = []
    doc_counts: dict[str, int] = {}
    stats = {
        "seeds_total": len(seed_handles),
        "seed_records": 0,
        "find_records": 0,
        "rejected_boilerplate": 0,
        "rejected_never_cite": 0,
        "rejected_short": 0,
        "rejected_doc_cap": 0,
        "passes": 0,
    }

    def try_chunk(row, *, score: float, source: str) -> bool:
        if row is None:
            return False
        if row["is_boilerplate"]:
            stats["rejected_boilerplate"] += 1
            return False
        text = (row["text"] or "").strip()
        if len(text) < min_chunk_chars:
            stats["rejected_short"] += 1
            return False
        if _matches_never_cite(text):
            stats["rejected_never_cite"] += 1
            return False
        if any(r["chunk_id"] == row["chunk_id"] for r in records):
            return False
        if doc_counts.get(row["doc_id"], 0) >= per_doc_cap:
            stats["rejected_doc_cap"] += 1
            return False
        records.append(
            {
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "quote": text[:400],
                "score": float(score),
                "status": "active",
            }
        )
        doc_counts[row["doc_id"]] = doc_counts.get(row["doc_id"], 0) + 1
        if source == "seed":
            stats["seed_records"] += 1
        else:
            stats["find_records"] += 1
        return True

    # Phase 1: seed doc handles (extractor prior)
    for handle in seed_handles:
        if len(records) >= target:
            break
        doc_id = _resolve_doc_id(corpus, handle)
        if doc_id is None:
            continue
        seed_chunks = fetch_seed_chunks(doc_id, per_doc_cap)
        for row in seed_chunks:
            if len(records) >= target:
                break
            try_chunk(row, score=1.0, source="seed")

    # Phase 2: corpus find top-up with widening k
    for k in (top_k, top_k * 2, top_k * 3):
        if len(records) >= target:
            break
        items = find_chunks(title, k)
        for it in items:
            if len(records) >= target:
                break
            row = fetch_chunk(it.get("id") or "")
            try_chunk(row, score=float(it.get("score", 0.0)), source="find")
        stats["passes"] += 1

    con.close()

    if not records:
        if fmt == "json":
            typer.echo(json.dumps({"ok": False, "error": "no_evidence", "stats": stats}))
        else:
            typer.echo(f"{concept}: no evidence gathered  stats={stats}")
        raise typer.Exit(code=EXIT_VALIDATION)

    parsed = [EvidenceRecord.model_validate(r) for r in records]
    n = append_evidence(bundle, concept, parsed)
    distinct_docs = len({r["doc_id"] for r in records})
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "concept": concept,
                    "appended": n,
                    "distinct_docs": distinct_docs,
                    "stats": stats,
                }
            )
        )
        return
    typer.echo(
        f"{concept}: appended {n} records across {distinct_docs} docs  "
        f"(seed={stats['seed_records']} find={stats['find_records']} "
        f"rejected=bp{stats['rejected_boilerplate']}/"
        f"nc{stats['rejected_never_cite']}/short{stats['rejected_short']}/"
        f"cap{stats['rejected_doc_cap']})"
    )


# -------------------------------------------------------------- cluster-concepts


@app.command("cluster-concepts")
def cmd_cluster_concepts(
    threshold: float = typer.Option(
        0.15, "--threshold",
        help="Minimum Jaccard overlap (over doc_id sets) to link two concepts.",
    ),
    max_cluster_size: int = typer.Option(
        5, "--max-cluster-size",
        help="Largest single cluster the algorithm emits; large clusters "
        "are split by greedy chaining.",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Cluster active concepts by evidence overlap (Jaccard over doc_ids).

    Pages within a cluster share evidence sources and benefit from one
    writer agent reading them together (consistent terminology, no
    duplicate chunk reads). Person concepts are placed in their own
    cluster regardless of overlap so the person-style prompt path stays
    distinct from article writing.
    """
    from ..bundle.work.evidence import read_evidence

    bundle = _resolve_bundle(run)
    slugs = list_concept_slugs(bundle)
    by_slug: dict[str, set[str]] = {}
    kind_of: dict[str, str] = {}
    for s in slugs:
        card = load_card(bundle, s)
        if card.front.get("status") not in ("active", "committed"):
            continue
        kind_of[s] = card.kind
        recs = read_evidence(bundle, s)
        by_slug[s] = {r.doc_id for r in recs if r.status == "active"}

    def jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    article_slugs = [s for s in by_slug if kind_of[s] == "article"]
    person_slugs = [s for s in by_slug if kind_of[s] == "person"]

    # Greedy connected-component clustering with size cap. Sort slugs by
    # evidence set size descending so high-coverage hubs anchor clusters.
    remaining = sorted(article_slugs, key=lambda s: -len(by_slug[s]))
    clusters: list[list[str]] = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        # Pull in neighbors above threshold up to max_cluster_size.
        added_any = True
        while added_any and len(cluster) < max_cluster_size:
            added_any = False
            best_slug = None
            best_score = threshold
            for cand in list(remaining):
                score = max(jaccard(by_slug[c], by_slug[cand]) for c in cluster)
                if score >= best_score:
                    best_score = score
                    best_slug = cand
            if best_slug is not None:
                cluster.append(best_slug)
                remaining.remove(best_slug)
                added_any = True
        clusters.append(cluster)

    if person_slugs:
        # Persons travel together; split into chunks of max_cluster_size.
        for i in range(0, len(person_slugs), max_cluster_size):
            clusters.append(person_slugs[i : i + max_cluster_size])

    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "clusters": [
                        {
                            "id": i,
                            "kind": (
                                "person"
                                if all(kind_of[s] == "person" for s in c)
                                else "article"
                            ),
                            "slugs": c,
                            "size": len(c),
                        }
                        for i, c in enumerate(clusters)
                    ],
                }
            )
        )
        return
    for i, c in enumerate(clusters):
        kind = "person" if all(kind_of[s] == "person" for s in c) else "article"
        typer.echo(f"cluster {i:2d} ({kind}, {len(c)}): {', '.join(c)}")


__all__ = ["app"]
