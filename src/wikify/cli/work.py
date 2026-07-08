"""``wikify work ...`` — in-flight build state for wiki bundles.

Subcommands::

    work list [--run] [--status]
    work list claims [--run]
    work list inbox [--run]
    work list evidence <concept> [--run]
    work seen-chunks <concept...> [--run]
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
import re
import unicodedata
from pathlib import Path

import typer

from ..api import Bundle
from ..bundle.run.events import Event, append_event, read_events
from ..bundle.run.state import load_state
from ..bundle.work.card import (
    create_concept,
    list_concept_slugs,
    load_card,
    save_card,
)
from ..bundle.work.chunk_ids import build_suffix_index, corpus_path_from_bundle, resolve_chunk_id
from ..bundle.work.claim import (
    ClaimHeldError,
    acquire_claim,
    list_claims,
    read_claim,
    release_claim,
)
from ..bundle.work.evidence import (
    EvidenceRecord,
    append_evidence,
    read_evidence,
    seen_chunk_ids,
)
from ..bundle.work.inbox import append_inbox, list_inbox_files
from ..bundle.work.tend import tend_bundle
from ..corpus.handles import HandleIndex as _HandleIndex
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error, cli_owner
from ._io import _clean_slug_arg

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
        claim_owner_by_slug = {
            c.get("slug"): c.get("owner") for c in list_claims(bundle)
        }
        items = []
        for s in slugs:
            card = load_card(bundle, s)
            recs = read_evidence(bundle, s)
            active = [r for r in recs if r.status == "active"]
            items.append(
                {
                    "slug": s,
                    "page_id": card.page_id,
                    "kind": card.kind,
                    "status": card.status,
                    "needs_refine": card.needs_refine,
                    "evidence_chunks": len(active),
                    "evidence_docs": len({r.doc_id for r in active}),
                    "claim_owner": claim_owner_by_slug.get(s),
                }
            )
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for s in slugs:
        card = load_card(bundle, s)
        recs = read_evidence(bundle, s)
        n_active = sum(1 for r in recs if r.status == "active")
        typer.echo(f"{s:<32}  {card.kind:<8}  {card.status:<14}  {n_active:>4}ev  {card.page_id}")


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
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    records = read_evidence(bundle, concept)
    if fmt == "json":
        typer.echo(
            json.dumps({"ok": True, "items": [r.model_dump() for r in records]})
        )
        return
    for r in records:
        typer.echo(f"{r.chunk_id}  {r.doc_id}  {r.status}  {r.score:.2f}")


# -------------------------------------------------------------- seen-chunks


@app.command("seen-chunks")
def cmd_seen_chunks(
    concepts: list[str] = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("json", "--format"),
) -> None:
    """Union of already-judged chunk_ids across ``concepts``.

    One cheap deterministic read an explorer makes before judging, to
    seed its ``seen_chunks`` dedup set from the durable evidence ledger
    (active records only) so it never re-judges a chunk across rounds.
    """
    bundle = _resolve_bundle(run)
    seen: set[str] = set()
    for concept in concepts:
        seen.update(seen_chunk_ids(bundle, _clean_slug_arg(concept)))
    ids = sorted(seen)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "seen_chunk_ids": ids, "n_seen": len(ids)}))
        return
    for cid in ids:
        typer.echo(cid)


# -------------------------------------------------------------- show


@app.command("show")
def cmd_show(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    card = load_card(bundle, concept)
    if not card.front:
        cli_error(EXIT_VALIDATION, error="concept_not_found", slug=concept)
    # Recount from disk so the display is always current regardless of
    # whether tend has run since the last build-evidence call.
    recs = read_evidence(bundle, concept)
    active = [r for r in recs if r.status == "active"]
    card.front["evidence_chunks"] = len(active)
    card.front["evidence_docs"] = len({r.doc_id for r in active})
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
    round_num: int | None = typer.Option(
        None, "--round",
        help="Round number to record in the evidence_added event.",
    ),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Append evidence records for *concept*, resolving chunk handles to
    canonical ids and emitting an ``evidence_added`` event.

    If the bundle's recorded corpus path is reachable, every record's
    ``chunk_id`` is resolved from short handles (``chunk:<hex>``) to the
    canonical form.  Records whose id cannot be resolved are rejected and
    reported; pass-through occurs without resolution when no corpus is
    reachable.
    """
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if not records.is_file():
        cli_error(EXIT_VALIDATION, error="records_not_found", path=str(records))

    # Build a suffix index from the bound corpus if reachable.
    corpus_sqlite = None
    suffix_index: _HandleIndex = _HandleIndex()
    canonical_ids: frozenset[str] = frozenset()
    corpus_p = corpus_path_from_bundle(bundle.root)
    if corpus_p is not None:
        from ..api import Corpus  # noqa: PLC0415 — deferred to avoid circular import at module load
        corpus_sqlite = Corpus(root=corpus_p).sqlite_path
        canonical_ids, suffix_index = build_suffix_index(corpus_sqlite)

    # A corpus is usable for validation only when it has at least one chunk.
    # An empty or absent corpus cannot validate anything; treat it as
    # unreachable so records pass through rather than being rejected en masse.
    has_corpus = bool(canonical_ids)

    if not has_corpus:
        typer.echo(
            "WARNING: corpus unreachable or empty -- chunk_ids stored unresolved; "
            "handles will zero out coverage on a machine without this corpus.",
            err=True,
        )

    parsed: list[EvidenceRecord] = []
    rejected: list[dict] = []
    for line in records.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = EvidenceRecord.model_validate_json(line)
        except Exception as exc:
            cli_error(EXIT_VALIDATION, error="bad_record", message=str(exc))

        if has_corpus:
            resolved = resolve_chunk_id(
                rec.chunk_id, suffix_index, canonical_ids,
                sqlite_path=corpus_sqlite,
            )
            if resolved is None:
                rejected.append({"chunk_id": rec.chunk_id, "reason": "unresolvable"})
                continue
            if resolved != rec.chunk_id:
                # Rebuild with canonical id; preserve all other fields.
                rec = rec.model_copy(update={"chunk_id": resolved})
        parsed.append(rec)

    if rejected and not parsed:
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "all_rejected",
                        "rejected": rejected,
                    }
                )
            )
        else:
            for r in rejected:
                typer.echo(
                    f"rejected {r['chunk_id']!r}: {r['reason']}", err=True
                )
        raise typer.Exit(code=EXIT_VALIDATION)

    if not parsed:
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "appended": 0}))
        else:
            typer.echo(f"appended 0 records to {concept}/evidence.jsonl")
        return

    n = append_evidence(bundle, concept, parsed)

    # Emit evidence_added event.
    try:
        state = load_state(bundle)
        event_data: dict = {"n": n}
        if round_num is not None:
            event_data["round"] = round_num
        append_event(
            bundle,
            Event(
                run_id=state.run_id,
                type="evidence_added",
                actor="cli",
                concept_id=concept,
                data=event_data,
            ),
        )
    except Exception:
        # Event emission is best-effort; do not fail the write.
        pass

    if fmt == "json":
        result: dict = {"ok": True, "appended": n}
        if rejected:
            result["rejected"] = rejected
        typer.echo(json.dumps(result))
        return
    if rejected:
        for r in rejected:
            typer.echo(f"rejected {r['chunk_id']!r}: {r['reason']}", err=True)
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
    concept = _clean_slug_arg(concept)
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
    concept = _clean_slug_arg(concept)
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
    concept = _clean_slug_arg(concept)
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
    keep_inbox: bool = typer.Option(
        False,
        "--keep-inbox",
        help=(
            "Preserve work/inbox/concept_suggestions.jsonl after "
            "consolidation. The other inboxes always drain."
        ),
    ),
) -> None:
    bundle = _resolve_bundle(run)
    summary = tend_bundle(bundle, keep_inbox=keep_inbox)
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
    head = text[:600] if text else ""
    return any(
        re.search(p, head, re.IGNORECASE | re.MULTILINE)
        for p in _NEVER_CITE_PATTERNS
    )


# Affiliation / role / career signal used by the person identity-context
# gather. A chunk from the target author's own doc carrying any of these
# AND naming the author is grounded biographical material the writer can
# cite, even though the general boilerplate filter would normally drop it.
_IDENTITY_SIGNAL_RE = re.compile(
    r"(Department\s+of|Universit|Institut|Laborator|Corporation|"
    r"School\s+of|Faculty\s+of|Centre\s+for|Center\s+for|"
    r"Professor|Ph\.?\s?D|received\s+(?:his|her|the)|"
    r"joined|is\s+currently|appointed|research\s+group|"
    r"graduated|born\s+in)",
    re.IGNORECASE,
)


def _has_identity_signal(text: str) -> bool:
    return bool(_IDENTITY_SIGNAL_RE.search(text or ""))


# Person-path gather quality. The maturity gate counts chunks whose quote
# carries an attributed-contribution verb (see ``_person_components`` in
# ``bundle.work.maturity``; the regex there already accepts first-person
# "we develop / demonstrate / propose" forms). Publisher front-matter that
# escaped ``is_boilerplate`` at ingest -- author bylines, "Cite as ...",
# "Special (Topic) Collection", submission/acceptance dates, DOI lines --
# otherwise fills the per-doc cap before those substantive claims, starving
# the gate (observed: a 4-paper modelling author gathered 0 contribution
# chunks). For the person path we (a) reject that front-matter even when it
# was not flagged boilerplate, and (b) surface contribution-bearing chunks
# from the author's own docs first so the gate is fed substance, not blurb.
_PERSON_CONTRIB_HINT_RE = re.compile(
    r"\b(?:propos|introduc|develop|invent|discover|demonstrat|report|"
    r"formulat|show|establish)(?:e|es|ed|s|n|ing)?\b",
    re.IGNORECASE,
)
_PERSON_FRONTMATTER_RE = re.compile(
    r"(?im)(?:^\s*cite\s+(?:as|this)\b|special\s+(?:topic\s+)?collection|"
    r"^\s*citation:|\bsubmitted:\s|\baccepted:\s|\breceived:\s|"
    r"\bdoi:\s*10\.|\bhttps?://doi\.org)",
)


def _person_frontmatter(text: str) -> bool:
    return bool(_PERSON_FRONTMATTER_RE.search(text or ""))


def _person_name_variants(card) -> set[str]:
    """Lowercased strings that specifically name the target author.

    Union of the card ``page_id``, each ``author:``/plain alias, and the
    last-name token of each (tokens < 3 chars, e.g. initials, are dropped
    so a bare ``A.`` cannot match unrelated text). Used to gate the
    identity-context gather so only chunks naming the target author are
    lifted past the boilerplate filter.
    """
    variants: set[str] = set()

    def _add(name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        variants.add(name.lower())
        toks = [t for t in re.split(r"\s+", name) if len(t) >= 3]
        if toks:
            variants.add(toks[-1].lower())

    pid = getattr(card, "page_id", "")
    if isinstance(pid, str):
        _add(pid)
    for alias in card.front.get("aliases") or []:
        if not isinstance(alias, str):
            continue
        a = alias.strip()
        if a.lower().startswith("author:"):
            _add(a.split(":", 1)[1].replace("_", " "))
        else:
            _add(a)
    return {v for v in variants if v}


def _chunk_names_author(text_lower: str, variants: set[str]) -> bool:
    return any(v in text_lower for v in variants)


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
    """Map ``doc:<short>`` / ``<short>`` / full id to the full doc_id.

    Seed handles are a best-effort prior drawn from the work card, the
    notebook's ``provenance.seed_docs`` (user-supplied via
    ``notebook-init --seed-docs``), and author-derived sources. A
    non-string element or an ambiguous handle resolves to ``None`` and is
    skipped rather than crashing the gather.
    """
    from ..corpus.queries import (
        AmbiguousHandleError,
        HandleNotFoundError,
        get_doc,
    )

    if not isinstance(short_or_full, str):
        return None
    handle = short_or_full
    if handle.startswith("doc:"):
        handle = handle[4:]
    try:
        doc = get_doc(corpus, handle)
    except (HandleNotFoundError, AmbiguousHandleError):
        return None
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
            "Commit-only mode: comma-separated chunk_ids, OR the literal "
            "value '@-' to read a JSON list of records from stdin. Each "
            "JSON entry: {\"chunk_id\": <id>, \"score\"?: <float>, "
            "\"quote\"?: <str>}. Quotes are verified to appear literally "
            "in the chunk's text (anti-hallucination); ids whose quote "
            "is fabricated are rejected with rejected_quote_not_in_chunk. "
            "CSV mode uses score=1.0 and text[:400] as quote."
        ),
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Gather evidence for *concept* using seed_doc_handles + corpus find.

    Seed docs serve as a high-precision prior: up to ``per_doc_cap`` body
    chunks are pulled from each seed doc first. Seeds are the union of the
    work card's ``seed_doc_handles`` (the data-extractor's prior), the
    notebook's ``provenance.seed_docs`` (set by ``notebook-init
    --seed-docs``), and — for ``kind=person`` cards carrying an
    ``author:<key>`` alias — that author's own papers. The remainder is
    filled by
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

    With ``--from-ids @-`` the CLI reads a JSON list from stdin where
    each entry is ``{"chunk_id": <id>, "score"?: <float>, "quote"?:
    <str>}``. Supplied quotes are verified to appear literally in the
    chunk's text; rejected as ``rejected_quote_not_in_chunk`` if not.

    Writes ``work/concepts/<slug>/evidence.jsonl`` and prints stats.
    """
    import contextlib
    import io
    import sqlite3

    from ..api import Corpus
    from ..bundle.work.card import load_card
    from ..bundle.work.evidence import (
        EvidenceRecord,
        append_evidence,
        read_evidence,
    )
    from ..corpus import queries as _queries
    from .corpus import _emit_chunk_reads

    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if not corpus_dir.is_dir():
        cli_error(EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir))
    corpus = Corpus(root=corpus_dir)
    card = load_card(bundle, concept)
    if not card.front:
        cli_error(EXIT_VALIDATION, error="concept_not_found", concept=concept)
    title = card.page_id
    seed_handles = list(card.front.get("seed_doc_handles") or [])
    # Seeds set via `notebook-init --seed-docs` persist on the notebook
    # provenance, not the work card (the card field is the data-extractor's
    # prior). Union both so the documented add-concept -> notebook-init
    # -> build-evidence flow actually seeds the gather.
    from ..bundle.work.notebook import read_notebook

    nb = read_notebook(bundle, concept)
    for h in nb.front.provenance.seed_docs:
        if h not in seed_handles:
            seed_handles.append(h)
    # A person page is grounded in the author's OWN papers. For a person
    # card carrying an `author:<key>` alias, union that author's sources
    # into the seeds so the gather lifts quoted-contribution chunks from
    # their work rather than generic name mentions corpus-wide.
    person_kind = card.front.get("kind") == "person"
    # The target author's own source docs, captured during author-seed
    # resolution and reused by the identity-context gather below.
    author_own_doc_ids: set[str] = set()
    if person_kind:
        from ..corpus.queries import (
            AmbiguousHandleError,
            HandleNotFoundError,
            QueryError,
        )
        from ..corpus.queries import traverse as _traverse

        for alias in card.front.get("aliases") or []:
            if not (isinstance(alias, str) and alias.lower().startswith("author:")):
                continue
            try:
                # The dispatcher resolves the author prefix to its graph key
                # before traversing to that author's papers.
                rows = _traverse(corpus, handle=alias, to="sources")["rows"]
            except (HandleNotFoundError, AmbiguousHandleError, QueryError):
                # Author absent / unresolvable / ambiguous (a bare last-name
                # alias matching several authors): skip this alias. Unexpected
                # errors propagate (not masked).
                continue
            for d in rows:
                did = d.get("id") or d.get("doc_id") or d.get("handle")
                if isinstance(did, str):
                    author_own_doc_ids.add(did)
                    if did not in seed_handles:
                        seed_handles.append(did)

    db_path = corpus.sqlite_path
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    def fetch_chunk(chunk_id: str):
        return con.execute(
            "SELECT chunk_id, doc_id, text, is_boilerplate, section_type "
            "FROM chunks WHERE chunk_id=?",
            (chunk_id,),
        ).fetchone()

    def resolve_chunk_handle(raw_cid: str) -> tuple[str, str | None]:
        """Map a chunk handle to a full chunk_id.

        Accepts:
        - Full chunk_id: returned as-is. If not found in the store, returns
          (raw_cid, error_message).
        - ``chunk:<short>``: the short suffix (8+ hex chars) is looked up
          via a LIKE query. Returns (full_id, None) on unique match or
          (raw_cid, error_message) on zero / multiple matches.

        Returns ``(resolved_id, error_or_None)``.
        """
        if not raw_cid.startswith("chunk:"):
            # Full id path: verify it exists.
            row = con.execute(
                "SELECT chunk_id FROM chunks WHERE chunk_id=?", (raw_cid,)
            ).fetchone()
            if row is None:
                return (
                    raw_cid,
                    (
                        f"chunk id {raw_cid!r} not found in corpus store; "
                        "check that the corpus path matches the one used "
                        "during retrieval"
                    ),
                )
            return (raw_cid, None)
        short = raw_cid[len("chunk:"):]
        # Escape SQLite LIKE wildcards so a suffix containing % or _
        # is matched literally, not as a wildcard pattern.
        short_esc = short.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = con.execute(
            "SELECT chunk_id FROM chunks WHERE chunk_id LIKE ? ESCAPE '\\'",
            (f"%_{short_esc}",),
        ).fetchall()
        # Also accept exact match (test fixtures use plain ids without hash).
        exact = con.execute(
            "SELECT chunk_id FROM chunks WHERE chunk_id=?", (short,)
        ).fetchone()
        if exact is not None:
            rows = [exact] + [r for r in rows if r["chunk_id"] != short]
        if len(rows) == 0:
            return (
                raw_cid,
                (
                    f"chunk handle {raw_cid!r} did not resolve; "
                    "no chunk id ends with that suffix"
                ),
            )
        if len(rows) > 1:
            matched = [r["chunk_id"] for r in rows]
            display = matched[:5]
            extra = f" (+{len(matched) - 5} more)" if len(matched) > 5 else ""
            return (
                raw_cid,
                (
                    f"chunk handle {raw_cid!r} did not resolve uniquely; "
                    f"matched {len(matched)} chunks: "
                    f"{', '.join(display)}{extra}"
                ),
            )
        return (rows[0]["chunk_id"], None)

    # ----- commit-only mode: skip seed/find, just validate + append.
    if from_ids:
        # Parse either JSON-from-stdin (@-) or CSV form into a uniform
        # list of {chunk_id, score?, quote?} entries.
        entries: list[dict] = []
        if from_ids.strip() == "@-":
            import sys as _sys

            # Prefer the binary path (matches draft check --dry-run);
            # the CLI-IO tee wraps stdin in a text-only _TeeReader that
            # has no .buffer attribute, so fall back to text read there.
            buf = getattr(_sys.stdin, "buffer", None)
            if buf is not None:
                stdin_text = buf.read().decode("utf-8")
            else:
                stdin_text = _sys.stdin.read()
            try:
                payload = json.loads(stdin_text)
            except json.JSONDecodeError as exc:
                con.close()
                cli_error(EXIT_VALIDATION, error="bad_json", message=str(exc))
            if not isinstance(payload, list):
                con.close()
                cli_error(
                    EXIT_VALIDATION,
                    error="bad_json",
                    message="expected JSON list",
                )
            for item in payload:
                if not isinstance(item, dict):
                    con.close()
                    cli_error(
                        EXIT_VALIDATION,
                        error="bad_json",
                        message="each entry must be a JSON object",
                    )
                cid = item.get("chunk_id")
                if not isinstance(cid, str) or not cid.strip():
                    con.close()
                    cli_error(
                        EXIT_VALIDATION,
                        error="bad_json",
                        message="each entry needs a non-empty 'chunk_id' string",
                    )
                entry: dict = {"chunk_id": cid.strip()}
                if "score" in item and item["score"] is not None:
                    try:
                        entry["score"] = float(item["score"])
                    except (TypeError, ValueError) as exc:
                        con.close()
                        cli_error(
                            EXIT_VALIDATION,
                            error="bad_json",
                            message=f"score must be numeric: {exc}",
                        )
                if "quote" in item and item["quote"] is not None:
                    if not isinstance(item["quote"], str):
                        con.close()
                        cli_error(
                            EXIT_VALIDATION,
                            error="bad_json",
                            message="quote must be a string",
                        )
                    entry["quote"] = item["quote"]
                entries.append(entry)
        else:
            raw_ids = [s.strip() for s in from_ids.split(",") if s.strip()]
            for cid in raw_ids:
                entries.append({"chunk_id": cid})
        # Preserve caller order while deduping by chunk_id (first wins).
        seen_in: set[str] = set()
        ordered_entries: list[dict] = []
        for entry in entries:
            cid = entry["chunk_id"]
            if cid in seen_in:
                continue
            seen_in.add(cid)
            ordered_entries.append(entry)
        if not ordered_entries:
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
            "ids_total": len(ordered_entries),
            "appended": 0,
            "rejected_not_found": 0,
            "rejected_boilerplate": 0,
            "rejected_excluded_kind": 0,
            "rejected_never_cite": 0,
            "rejected_short": 0,
            "rejected_already_committed": 0,
            "rejected_quote_not_in_chunk": 0,
            "rejected_quote_then_whitespace_recovered": 0,
        }
        vetter_records: list[dict] = []
        for entry in ordered_entries:
            raw_cid = entry["chunk_id"]
            cid, resolve_err = resolve_chunk_handle(raw_cid)
            if resolve_err is not None:
                vetter_stats["rejected_not_found"] += 1
                continue
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
            raw_text = row["text"] or ""
            supplied_quote = entry.get("quote")
            if supplied_quote is not None:
                norm_text = unicodedata.normalize("NFKC", raw_text)
                norm_quote = unicodedata.normalize("NFKC", supplied_quote)
                if norm_quote in norm_text:
                    quote = supplied_quote
                else:
                    # Tier 2: strip all whitespace on both sides
                    # (handles OCR artefacts like "SiN x" vs "SiNx").
                    # Gate on min length 12 so a short collapsed quote
                    # cannot substring-match an unrelated token region
                    # ("SiNx" inside "GeSiNxO").
                    ws_text = re.sub(r"\s+", "", norm_text)
                    ws_quote = re.sub(r"\s+", "", norm_quote)
                    if len(ws_quote) >= 12 and ws_quote in ws_text:
                        vetter_stats["rejected_quote_then_whitespace_recovered"] += 1
                        quote = supplied_quote  # keep writer's spelling
                    else:
                        vetter_stats["rejected_quote_not_in_chunk"] += 1
                        continue
            else:
                quote = text[:400]
            score = entry.get("score", 1.0)
            vetter_records.append(
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "quote": quote,
                    "score": float(score),
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
            f"q{vetter_stats['rejected_quote_not_in_chunk']}/"
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

    find_exclude_kinds = sorted(_FROM_IDS_EXCLUDED_KINDS)

    def find_chunks(query: str, k: int) -> list[dict]:
        # In-process corpus search. This was previously a subprocess shell-out
        # to ``wikify corpus find --format json``; parsing the child's stdout
        # meant any library that printed to stdout at import (e.g. a
        # mismatched onnxruntime warning) corrupted the JSON, ``json.loads``
        # threw, and the swallowed error silently contributed ZERO find chunks
        # -- producing seed-only pages with no signal. Calling ``queries.find``
        # directly removes the stdout dependency entirely. ``strict_semantic``
        # makes a broken runtime embedder fail loudly instead of degrading to
        # a lexical-only, thinly-evidenced page.
        #
        # The old subprocess captured the child's stdout, which incidentally
        # shielded THIS command's stdout from any noise a library printed
        # while importing the embedder. In-process there is no such shield, so
        # capture stray stdout into a buffer (NOT stderr -- cli_error writes
        # the JSON error envelope there) around the search: a stray warning
        # must corrupt neither the JSON result on stdout nor the JSON error on
        # stderr. Any captured noise is discarded on success and folded into
        # the error payload on failure, so it is preserved without breaking
        # either machine-readable channel.
        search_noise = io.StringIO()
        try:
            with contextlib.redirect_stdout(search_noise):
                result = _queries.find(
                    corpus, query=query, by="chunk", rank="all", top_k=k,
                    text=False, in_doc=None,
                    exclude_kinds=find_exclude_kinds,
                    strict_semantic=True,
                )
        except Exception as exc:  # noqa: BLE001
            # CLI boundary: normalise ANY search failure into the structured
            # envelope so ``--format json`` callers never receive a raw
            # traceback. This covers QueryError (query/embedder failures) and
            # unexpected store failures alike (e.g. a sqlite3 error from an
            # empty/corrupt wikify.db). The exception is reported, not
            # swallowed -- a zero-result gather is loud, matching the old
            # subprocess boundary that treated any nonzero search as handled.
            noise = search_noise.getvalue().strip()
            if isinstance(exc, _queries.QueryError):
                msg = f"{exc.code}: {exc.message}"
            else:
                msg = f"{type(exc).__name__}: {exc}"
            cli_error(
                EXIT_VALIDATION,
                error="corpus_search_failed",
                message=msg,
                **({"search_diagnostics": noise[:1000]} if noise else {}),
            )
        rows = result["rows"]
        # Preserve the chunk_read telemetry the CLI find path emitted via
        # ``--run`` so M5 read-tracking is unchanged by going in-process.
        _emit_chunk_reads(
            bundle, (r.get("id", "") for r in rows), via="corpus_find_semantic",
        )
        return rows

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
        "identity_context_records": 0,
        "rejected_frontmatter": 0,
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
        # Person path: drop publisher front-matter that escaped is_boilerplate
        # so it cannot occupy the per-doc cap ahead of substantive claims.
        if person_kind and _person_frontmatter(text):
            stats["rejected_frontmatter"] += 1
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
        if person_kind:
            # Pull a wider slice of the author's own doc and try
            # contribution-bearing chunks FIRST, so the per-doc cap fills
            # with the attributed claims the maturity gate counts rather
            # than whatever descriptive text leads the document.
            seed_chunks = sorted(
                fetch_seed_chunks(doc_id, per_doc_cap * 4),
                key=lambda r: 0
                if _PERSON_CONTRIB_HINT_RE.search(r["text"] or "")
                else 1,
            )
        else:
            seed_chunks = fetch_seed_chunks(doc_id, per_doc_cap)
        for row in seed_chunks:
            if len(records) >= target:
                break
            try_chunk(row, score=1.0, source="seed")

    # Phase 2: corpus find top-up with widening k. Query the title AND each
    # non-author alias as a separate facet, so a concept's specific sub-topics
    # (e.g. "Volmer-Weber growth", a precursor name) surface papers a broad
    # title query -- flat and generic on a single-domain corpus -- buries.
    # The title leads each pass for precision; try_chunk dedups across facets.
    facet_queries = [title]
    for alias in (card.front.get("aliases") or []):
        if (
            isinstance(alias, str)
            and alias.strip()
            and not alias.lower().startswith("author:")
        ):
            facet_queries.append(alias.strip())
    for k in (top_k, top_k * 2, top_k * 3):
        if len(records) >= target:
            break
        for q in facet_queries:
            if len(records) >= target:
                break
            items = find_chunks(q, k)
            for it in items:
                if len(records) >= target:
                    break
                row = fetch_chunk(it.get("id") or "")
                try_chunk(row, score=float(it.get("score", 0.0)), source="find")
        stats["passes"] += 1

    # Phase 3 (person only): identity-context gather. Pull chunks from the
    # target author's OWN docs that BOTH name the author AND carry an
    # affiliation / role / career signal. These are normally boilerplate-
    # excluded; the person path allows them when they specifically name the
    # target author, so the dossier carries grounded role/affiliation
    # material the writer can cite. Capped and per-doc-limited; the article
    # path is untouched.
    if person_kind and author_own_doc_ids:
        identity_context_cap = 4
        identity_min_chars = 40
        variants = _person_name_variants(card)
        if variants:
            seen_ids = {r["chunk_id"] for r in records}
            n_identity = 0
            for doc_id in sorted(author_own_doc_ids):
                if n_identity >= identity_context_cap:
                    break
                rows = con.execute(
                    "SELECT chunk_id, doc_id, text FROM chunks "
                    "WHERE doc_id=? ORDER BY ord",
                    (doc_id,),
                ).fetchall()
                per_doc = 0
                for row in rows:
                    if n_identity >= identity_context_cap:
                        break
                    if per_doc >= per_doc_cap:
                        break
                    cid = row["chunk_id"]
                    if cid in seen_ids:
                        continue
                    text = (row["text"] or "").strip()
                    if len(text) < identity_min_chars:
                        continue
                    if not _chunk_names_author(text.lower(), variants):
                        continue
                    if not _has_identity_signal(text):
                        continue
                    records.append(
                        {
                            "chunk_id": cid,
                            "doc_id": row["doc_id"],
                            "quote": text[:400],
                            "score": 1.0,
                            "status": "active",
                            "note": "identity_context",
                        }
                    )
                    seen_ids.add(cid)
                    doc_counts[row["doc_id"]] = doc_counts.get(row["doc_id"], 0) + 1
                    stats["identity_context_records"] += 1
                    n_identity += 1
                    per_doc += 1

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
        f"identity={stats['identity_context_records']} "
        f"rejected=bp{stats['rejected_boilerplate']}/"
        f"nc{stats['rejected_never_cite']}/short{stats['rejected_short']}/"
        f"fm{stats['rejected_frontmatter']}/"
        f"cap{stats['rejected_doc_cap']})"
    )


# -------------------------------------------------------------- cluster-concepts


@app.command("cluster-concepts")
def cmd_cluster_concepts(
    by: str = typer.Option(
        "auto", "--by",
        help="Signal to cluster on: 'auto' (pick 'evidence' if any "
        "concept has active evidence, else 'seeds'), 'evidence' "
        "(Jaccard over evidence doc_ids, requires evidence committed) "
        "or 'seeds' (Jaccard over seed_doc_handles from the work card, "
        "usable pre-evidence).",
    ),
    threshold: float = typer.Option(
        0.15, "--threshold",
        help="Minimum Jaccard overlap to link two concepts.",
    ),
    max_cluster_size: int = typer.Option(
        5, "--max-cluster-size",
        help="Largest single cluster the algorithm emits; large clusters "
        "are split by greedy chaining.",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Cluster active concepts by doc-set overlap (Jaccard).

    Choose the signal with ``--by``:

    - ``auto`` (default): inspect the bundle and pick ``evidence`` if
      at least one concept has an active evidence record, otherwise
      ``seeds``. Removes the trap of calling cluster pre-evidence with
      the default and getting empty clusters.
    - ``evidence``: overlap of evidence.jsonl doc_ids. Used between
      vetting and writing so one writer agent handles pages that share
      source documents.
    - ``seeds``: overlap of ``seed_doc_handles`` from each work card.
      Used pre-evidence (e.g. to group vetters into parallel waves).

    When ``--by auto`` is used the JSON output includes a
    ``mode_selected`` field naming the resolved mode.

    Person concepts are placed in their own cluster regardless of
    overlap so the person-style prompt path stays distinct from
    article writing.
    """
    from ..bundle.work.evidence import read_evidence

    requested = (by or "auto").lower()
    if requested not in ("auto", "evidence", "seeds"):
        cli_error(EXIT_VALIDATION, error="invalid_by",
                  message=f"--by must be 'auto', 'evidence' or 'seeds', "
                          f"got {by!r}")

    bundle = _resolve_bundle(run)
    slugs = list_concept_slugs(bundle)

    if requested == "auto":
        mode = "seeds"
        for s in slugs:
            recs = read_evidence(bundle, s)
            if any(r.status == "active" for r in recs):
                mode = "evidence"
                break
    else:
        mode = requested

    by_slug: dict[str, set[str]] = {}
    kind_of: dict[str, str] = {}
    for s in slugs:
        card = load_card(bundle, s)
        if card.front.get("status") not in ("active", "committed"):
            continue
        kind_of[s] = card.kind
        if mode == "evidence":
            recs = read_evidence(bundle, s)
            by_slug[s] = {r.doc_id for r in recs if r.status == "active"}
        else:  # mode == "seeds"
            handles = card.front.get("seed_doc_handles") or []
            by_slug[s] = {h.split(":", 1)[-1] for h in handles if h}

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
        payload = {
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
        if requested == "auto":
            payload["mode_selected"] = mode
        typer.echo(json.dumps(payload))
        return
    for i, c in enumerate(clusters):
        kind = "person" if all(kind_of[s] == "person" for s in c) else "article"
        typer.echo(f"cluster {i:2d} ({kind}, {len(c)}): {', '.join(c)}")


# -------------------------------------------------------------- maturity


@app.command("maturity")
def cmd_maturity(
    slugs: list[str] = typer.Argument(None),
    all_slugs: bool = typer.Option(False, "--all"),
    threshold: float = typer.Option(0.70, "--threshold"),
    current_round: int = typer.Option(0, "--round"),
    stencil: str | None = typer.Option(
        None, "--stencil",
        help="Override kind_stencil for article concepts.",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Score one or more concepts against the maturity gate.

    Without ``--all`` and no slug arguments, scores every concept slug
    on disk. The score formula and gates are documented in
    ``.claude/skills/wikify/subskills/reference/references/exploration/maturity.md``.
    """
    from ..bundle.work.maturity import compute_maturity

    bundle = _resolve_bundle(run)
    if not slugs and not all_slugs:
        # Default to listing everything; matches `work list` behavior.
        target = list_concept_slugs(bundle)
    elif all_slugs:
        target = list_concept_slugs(bundle)
    else:
        target = [_clean_slug_arg(s) for s in slugs]
    reports = [
        compute_maturity(
            bundle, s,
            kind_stencil=stencil,
            current_round=current_round,
            threshold=threshold,
        )
        for s in target
    ]
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "threshold": threshold,
                    "items": [r.to_dict() for r in reports],
                }
            )
        )
        return
    for r in reports:
        typer.echo(
            f"{r.slug:<32}  {r.kind:<8}  {r.band:<8}  "
            f"score={r.score:.2f}  gates={'ok' if r.gates_passed else 'fail'}  "
            f"n_chunks={r.n_chunks} n_docs={r.n_docs} "
            f"kinds={','.join(r.kinds_present) or '-'}"
        )


# -------------------------------------------------------------- refine-candidates


@app.command("refine-candidates")
def cmd_refine_candidates(
    run: Path | None = typer.Option(None, "--run"),
    growth: float = typer.Option(
        1.5, "--growth",
        help="Minimum evidence_now / evidence_at_commit ratio to flag.",
    ),
    min_new_chunks: int = typer.Option(
        6, "--min-new-chunks",
        help="Minimum evidence_now - evidence_at_commit delta to flag.",
    ),
    no_data: bool = typer.Option(
        False, "--no-data",
        help="Disable the data-artifact signal (only evidence growth flags).",
    ),
    min_new_siblings: int = typer.Option(
        4, "--min-new-siblings",
        help="Flag a page when this many topical-neighbour pages committed "
             "after it (cross-link refine). 0 disables the signal.",
    ),
    include_legacy_siblings: bool = typer.Option(
        False, "--include-legacy-siblings",
        help="Also flag pre-feature pages that never recorded a siblings_seen "
             "snapshot (a one-time retroactive cross-link drain). Off by "
             "default so a legacy wiki does not flood the STOP CHECK.",
    ),
    fmt: str = typer.Option("auto", "--format", help="json | compact | auto"),
) -> None:
    """List committed pages whose live evidence outgrew their write-time snapshot.

    For every card with ``status == committed`` the write-time evidence
    baseline (``evidence_total`` on the slug's latest ``page_committed``
    event, falling back to ``evidence_count`` for older events) is
    compared against the slug's live active-evidence count, recounted
    from the ledger on disk (the same value ``work show`` reports). A
    slug is a candidate when the ratio reaches ``--growth`` or the delta
    reaches ``--min-new-chunks``; committing a fresh page resets the
    baseline so a refreshed page won't re-trigger until it grows again.

    A page also flags with reason ``new_data`` when a committed data
    artifact relevant to it (sharing a source document with its active
    evidence) is not in the ``data_artifacts_seen`` snapshot recorded on
    its latest ``page_committed`` event -- i.e. a relevant artifact was
    committed after the page, so a re-draft can add a "Related data" link.
    Re-committing records the now-current artifacts and the page converges.
    Pass ``--no-data`` to disable this signal.

    Reason ``new_siblings`` flags a page that at least ``--min-new-siblings``
    topical-neighbour pages (sharing a source document) committed AFTER it and
    absent from its ``siblings_seen`` snapshot -- a page written when the wiki
    was small is under-connected once it fills in, so a re-draft weaves in the
    now-committed neighbours. Re-committing records the current neighbour set
    and converges. A page with NO snapshot predates this feature and is
    treated as converged (not flagged) unless ``--include-legacy-siblings`` is
    passed for a one-time retroactive cross-link drain. Deterministic and
    token-light (no chunk text).
    """
    import sys

    from ..bundle.wiki.commit import relevant_committed_artifacts

    if fmt not in {"json", "compact", "auto"}:
        cli_error(
            EXIT_VALIDATION,
            error="bad_format",
            message=f"unknown --format {fmt!r}; expected json | compact | auto",
        )
    if fmt == "auto":
        fmt = "compact" if sys.stdout.isatty() else "json"

    bundle = _resolve_bundle(run)

    # Latest write-time evidence baseline per slug, keyed off the ordered
    # event ledger (later lines supersede earlier ones). Prefer
    # ``evidence_total`` (the evidence set the page was written from,
    # comparable to the live active-evidence count); fall back to
    # ``evidence_count`` (writer-used markers) only for older events that
    # predate ``evidence_total``.
    baseline: dict[str, int] = {}
    seen_artifacts: dict[str, list[str]] = {}
    seen_siblings: dict[str, set[str]] = {}
    for ev in read_events(bundle):
        if ev.type != "page_committed":
            continue
        slug = ev.data.get("slug")
        count = ev.data.get("evidence_total", ev.data.get("evidence_count"))
        if isinstance(slug, str) and isinstance(count, int):
            baseline[slug] = count
        seen = ev.data.get("data_artifacts_seen")
        if isinstance(slug, str) and isinstance(seen, list):
            seen_artifacts[slug] = [a for a in seen if isinstance(a, str)]
        sibs = ev.data.get("siblings_seen")
        if isinstance(slug, str) and isinstance(sibs, list):
            seen_siblings[slug] = {x for x in sibs if isinstance(x, str)}

    committed_slugs = [
        s for s in list_concept_slugs(bundle)
        if load_card(bundle, s).status == "committed"
    ]
    # doc_id -> committed slugs citing it, from the indexed wiki_evidence
    # projection (one query), for the cross-link (new_siblings) signal: two
    # pages are topical neighbours when they share a source document.
    doc_slugs: dict[str, set[str]] = {}
    if min_new_siblings > 0 and bundle.sqlite_path.exists():
        import sqlite3
        _con = sqlite3.connect(str(bundle.sqlite_path))
        try:
            for _slug, _doc in _con.execute(
                "SELECT p.slug, e.doc_id FROM wiki_evidence e "
                "JOIN wiki_pages p ON p.page_id = e.page_id "
                "WHERE p.kind IN ('article', 'person')"
            ):
                if _slug and _doc:
                    doc_slugs.setdefault(_doc, set()).add(_slug)
        except sqlite3.Error:
            doc_slugs = {}
        finally:
            _con.close()

    items: list[dict] = []
    n_committed = 0
    for s in committed_slugs:
        card = load_card(bundle, s)
        n_committed += 1
        # Live recount from the ledger on disk so STOP-CHECK/finalize is
        # robust regardless of when ``work tend`` last refreshed the card
        # (same mechanism ``work show`` uses).
        active = [r for r in read_evidence(bundle, s) if r.status == "active"]
        e1 = len(active)

        # Evidence-growth signal (requires a positive write-time baseline).
        e0 = baseline.get(s)
        by_ratio = by_delta = False
        ratio = 0.0
        delta = 0
        if e0 and e0 > 0:
            ratio = e1 / e0
            delta = e1 - e0
            by_ratio = ratio >= growth
            by_delta = delta >= min_new_chunks

        # Data-artifact signal: committed artifacts relevant now that were
        # not in the snapshot recorded at the page's latest commit.
        new_artifacts: list[str] = []
        if not no_data:
            current = relevant_committed_artifacts(
                bundle, [r.doc_id for r in active]
            )
            seen = set(seen_artifacts.get(s, []))
            new_artifacts = [a for a in current if a not in seen]

        # Cross-link signal: topical-neighbour pages (share a source doc) that
        # committed after this page and were not in its write-time snapshot.
        new_siblings: list[str] = []
        if min_new_siblings > 0:
            mine = {r.doc_id for r in active if r.doc_id}
            current_sibs: set[str] = set()
            for d in mine:
                current_sibs |= doc_slugs.get(d, set())
            current_sibs.discard(s)
            # A page with no recorded snapshot predates this feature. Treat its
            # neighbours as already-seen (converged) unless the caller opts into
            # a one-time legacy drain, so an old wiki never floods the queue.
            if s in seen_siblings:
                seen = seen_siblings[s]
            elif include_legacy_siblings:
                seen = set()
            else:
                seen = current_sibs
            new_siblings = sorted(current_sibs - seen)

        siblings_hit = len(new_siblings) >= min_new_siblings > 0
        if not (by_ratio or by_delta or new_artifacts or siblings_hit):
            continue

        tokens: list[str] = []
        if by_ratio and by_delta:
            tokens.append("both")
        elif by_ratio:
            tokens.append("ratio")
        elif by_delta:
            tokens.append("delta")
        if new_artifacts:
            tokens.append("new_data")
        if siblings_hit:
            tokens.append("new_siblings")
        item = {
            "slug": s,
            "evidence_at_commit": e0 if (e0 and e0 > 0) else 0,
            "evidence_now": e1,
            "ratio": round(ratio, 3),
            "delta": delta,
            "n_docs_now": int(card.front.get("evidence_docs", 0) or 0),
            "reason": "+".join(tokens),
        }
        if new_artifacts:
            item["new_data_artifacts"] = new_artifacts
        if siblings_hit:
            item["n_new_siblings"] = len(new_siblings)
        items.append(item)

    items.sort(
        key=lambda it: (it["ratio"], it.get("n_new_siblings", 0)), reverse=True
    )
    payload = {
        "ok": True,
        "kind": "refine_candidates",
        "items": items,
        "thresholds": {
            "growth": growth, "min_new_chunks": min_new_chunks,
            "min_new_siblings": min_new_siblings,
        },
        "n_committed": n_committed,
        "n_candidates": len(items),
    }
    if fmt == "json":
        typer.echo(json.dumps(payload))
        return
    for it in items:
        typer.echo(
            f"{it['slug']:<32}  {it['evidence_at_commit']:>4} -> {it['evidence_now']:<4}  "
            f"ratio={it['ratio']:.2f}  delta={it['delta']:>3}  {it['reason']}"
        )


# -------------------------------------------------------------- coverage


@app.command("coverage")
def cmd_coverage(
    corpus_dir: Path = typer.Option(
        ..., "--corpus", help="Corpus root used to count total chunks."
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Report ``chunk_coverage_ratio`` for this bundle against the corpus.

    Unions chunk_ids from committed wiki pages (``wiki.db``) and
    in-flight notebooks / evidence ledgers, divided by the corpus
    chunk count.
    """
    from ..api import Corpus
    from ..bundle.work.coverage import compute_coverage

    if not corpus_dir.is_dir():
        cli_error(EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir))
    bundle = _resolve_bundle(run)
    corpus = Corpus(root=corpus_dir)
    report = compute_coverage(bundle, corpus)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, **report.to_dict()}))
        return
    typer.echo(
        f"covered: {report.n_covered}/{report.n_total} "
        f"({report.chunk_coverage_ratio:.3f})"
    )
    typer.echo(
        f"  committed: {report.n_covered_committed}  "
        f"in-flight: {report.n_covered_in_flight}"
    )
    typer.echo(
        f"  addressable: {report.n_addressable_covered}/{report.n_addressable} "
        f"({report.addressable_coverage_ratio:.3f})"
    )


# -------------------------------------------------------------- concept-recall


# Section kinds excluded from the relevance search so a page's candidate
# set is drawn from content chunks, not bibliography / captions / matter.
_RECALL_EXCLUDE_KINDS = [
    "references",
    "acknowledgments",
    "appendix",
    "figure",
    "table",
    "caption",
    "boilerplate",
]


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy 'linear' method).

    ``values`` need not be sorted. ``q`` is in [0, 1]. Empty input is a
    programming error (callers guard for it); a single value returns that
    value.
    """
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _coerce_year(value) -> int | None:
    """Best-effort parse of a document ``metadata['year']`` into an int.

    Accepts an int, a 4-digit-leading string (``"2015"``, ``"2015-03"``),
    or None; anything unparseable returns None so the doc is simply not
    placed in a year bucket rather than crashing the recall computation.
    """
    if value in (None, ""):
        return None
    try:
        return int(str(value)[:4])
    except (ValueError, TypeError):
        return None


# Blend weight for the citation-proximity boost. Proximity is normalised to
# [0, 1], so a candidate can gain at most this much over its relevance score.
# Kept small so relevance stays dominant and proximity only breaks near-ties.
_PROXIMITY_WEIGHT = 0.15

# A doc that sits in the citation neighbourhood of at least this many of the
# concept's own evidence docs is treated as part of its literature even when
# a broad keyword query buries it (the Grillo/Pt-ALD miss). Bounded so a
# heavily-cited page cannot flood the candidate set.
_COCITATION_MIN_NEIGHBOURS = 2
_COCITATION_CAP = 12


def _cocitation_candidates(
    corpus, represented_docs: list[str], exclude_ids: set[str],
) -> list[str]:
    """In-corpus docs cited by / citing at least ``_COCITATION_MIN_NEIGHBOURS``
    of the concept's evidence docs, excluding those already represented or
    already candidates.

    These are the papers the wiki's OWN sources collectively lean on, so a
    page that skips them has a real recall gap even when the concept's title
    query never ranks them. Returned most-central first (by neighbour count),
    capped at ``_COCITATION_CAP``. Cost is one targeted read of the
    ``references`` edges incident to the evidence docs -- the full graph is
    never built.
    """
    import sqlite3
    from collections import Counter

    if not represented_docs or not corpus.sqlite_path.exists():
        return []
    repr_set = set(represented_docs)
    repr_ph = ",".join("?" * len(represented_docs))
    con = None
    try:
        con = sqlite3.connect(str(corpus.sqlite_path))
        rows = con.execute(
            "SELECT src_id, dst_id FROM graph_edges WHERE kind='references' AND ("
            f"src_id IN ({repr_ph}) OR dst_id IN ({repr_ph}))",
            represented_docs + represented_docs,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if con is not None:
            con.close()
    # Distinct evidence-doc neighbours per external doc (either edge direction).
    neighbours: dict[str, set[str]] = {}
    for src, dst in rows:
        if src in repr_set and dst not in repr_set and dst not in exclude_ids:
            neighbours.setdefault(dst, set()).add(src)
        if dst in repr_set and src not in repr_set and src not in exclude_ids:
            neighbours.setdefault(src, set()).add(dst)
    counts = Counter({d: len(n) for d, n in neighbours.items()})
    return [
        d for d, c in counts.most_common()
        if c >= _COCITATION_MIN_NEIGHBOURS
    ][:_COCITATION_CAP]


def _citation_proximity(
    corpus, candidate_ids: list[str], represented_docs: list[str],
) -> dict[str, float]:
    """Per-candidate citation centrality within the concept's own evidence.

    For each candidate doc, count how many of the concept's current
    evidence docs it shares a citation edge with -- either it cites, or is
    cited by, that evidence doc -- normalised as ``min(count, 3) / 3``.

    Cost-neutral: one targeted read of the ``graph_edges`` ``references``
    rows restricted to the candidate x evidence document pairs (both
    directions), so the whole in-memory knowledge graph is never built.
    Returns 0.0 for every candidate when there is no evidence, the
    citation table is unavailable, or a doc has no such edge.
    """
    import sqlite3

    prox = {cid: 0.0 for cid in candidate_ids}
    if not candidate_ids or not represented_docs:
        return prox
    if not corpus.sqlite_path.exists():
        return prox
    cand_set = set(candidate_ids)
    repr_set = set(represented_docs)
    cand_ph = ",".join("?" * len(candidate_ids))
    repr_ph = ",".join("?" * len(represented_docs))
    con = None
    try:
        con = sqlite3.connect(str(corpus.sqlite_path))
        rows = con.execute(
            "SELECT src_id, dst_id FROM graph_edges WHERE kind='references' AND ("
            f"(src_id IN ({cand_ph}) AND dst_id IN ({repr_ph})) OR "
            f"(src_id IN ({repr_ph}) AND dst_id IN ({cand_ph})))",
            candidate_ids + represented_docs + represented_docs + candidate_ids,
        ).fetchall()
    except sqlite3.Error:
        return prox
    finally:
        if con is not None:
            con.close()
    # Distinct evidence-doc neighbours per candidate (either edge direction).
    neighbours: dict[str, set[str]] = {cid: set() for cid in candidate_ids}
    for src, dst in rows:
        if src in cand_set and dst in repr_set:
            neighbours[src].add(dst)
        if dst in cand_set and src in repr_set:
            neighbours[dst].add(src)
    for cid, reps in neighbours.items():
        prox[cid] = min(len(reps), 3) / 3.0
    return prox


@app.command("concept-recall")
def cmd_concept_recall(
    concept: str = typer.Argument(...),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    run: Path | None = typer.Option(None, "--run"),
    top_docs: int = typer.Option(
        12, "--top-docs",
        help="Number of most-relevant corpus docs to treat as candidates.",
    ),
    rank: str = typer.Option(
        "semantic", "--rank",
        help=(
            "Relevance ranking: semantic (default, embedding similarity -- the "
            "signal the build-evidence gather retrieves on, so the gate scores "
            "docs the way the evidence was gathered) | all (multi-signal) | "
            "bm25 (cheap sqlite metadata, no embedder). semantic/all fall back "
            "to bm25 when the corpus has no usable vectors."
        ),
    ),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Recall signal: does *concept*'s evidence represent the corpus's
    most-relevant sources?

    Ranks the top ``--top-docs`` corpus documents by relevance to the
    concept title + aliases, compares that candidate set against the
    distinct documents already in the slug's evidence ledger, and reports
    which candidates are missing, whether every publication-era bucket is
    represented, the section-type diversity of the committed evidence, and
    the share of evidence records concentrated in a single document.

    The default ranking is ``semantic`` -- the embedding-similarity signal
    ``build-evidence`` gathers on -- so the candidate set the gate checks
    coverage against matches how the evidence was actually retrieved. A
    lexical ``bm25`` default diverges from a semantic gather and
    false-negatives well-gathered pages (a device page whose relevant
    sources rarely repeat its title verbatim); plain ``all`` reintroduces
    that lexical component. ``semantic``/``all`` load an embedder and need
    corpus vectors, degrading to ``bm25`` when absent.
    """
    import math
    import sqlite3
    from collections import Counter

    from ..api import Corpus
    from ..corpus.chunks import list_documents
    from ..corpus.queries import QueryError, search_chunks
    from ..corpus.store.routing import sqlite_available

    if rank not in {"all", "semantic", "bm25"}:
        cli_error(EXIT_VALIDATION, error="bad_rank", rank=rank)
    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    if not corpus_dir.is_dir():
        cli_error(EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir))
    corpus = Corpus(root=corpus_dir)
    card = load_card(bundle, concept)
    if not card.front:
        cli_error(EXIT_VALIDATION, error="concept_not_found", slug=concept)

    # Query terms: the page title plus each alias (author: prefixes are
    # stripped to a plain name so the relevance search reads cleanly).
    queries: list[str] = []
    title = card.page_id if isinstance(card.page_id, str) else ""
    if title.strip():
        queries.append(title)
    for alias in card.front.get("aliases") or []:
        if not isinstance(alias, str) or not alias.strip():
            continue
        if alias.lower().startswith("author:"):
            queries.append(alias.split(":", 1)[1].replace("_", " "))
        else:
            queries.append(alias)

    # Candidate docs: best relevance per doc across all query terms,
    # ranked most-relevant first. BM25 scores are negated so that, like
    # semantic cosine, a larger value is a better match.
    candidate_docs: list[dict] = []
    relevance_pool: set[str] = set()
    rank_used = rank

    def _rank_pass(mode: str) -> dict[str, float]:
        # bm25's sqlite score is a cost (lower is better), so it is negated to
        # match the semantic/all convention that a larger value is a better
        # match. QueryError (missing vectors / embedder) yields no hits.
        found: dict[str, float] = {}
        higher_better = mode in {"semantic", "all", "hybrid"}
        for q in queries:
            try:
                hits = search_chunks(
                    corpus, q,
                    top_k=max(top_docs * 5, 50),
                    rank=mode,
                    exclude_kinds=_RECALL_EXCLUDE_KINDS,
                )
            except QueryError:
                hits = []
            for h in hits:
                did = h.get("doc_id")
                if not did:
                    continue
                raw = float(h.get("score", 0.0))
                rel = raw if higher_better else -raw
                if did not in found or rel > found[did]:
                    found[did] = rel
        return found

    if sqlite_available(corpus.root) and queries:
        best = _rank_pass(rank)
        # all/semantic need corpus vectors; when they are absent the pass comes
        # back empty -- fall back to bm25 so the gate still runs everywhere.
        if not best and rank in {"all", "semantic"}:
            rank_used = "bm25"
            best = _rank_pass("bm25")
        relevance_pool = set(best)
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:top_docs]
        docs_by_id = {d.id: d for d in list_documents(corpus)}
        for did, rel in ranked:
            doc = docs_by_id.get(did)
            year = _coerce_year((doc.metadata if doc else {}).get("year"))
            candidate_docs.append(
                {"doc_id": did, "year": year, "score": round(rel, 6),
                 "source": "relevance"}
            )

    # Represented side: distinct docs + per-doc record share + section
    # diversity, all from the slug's active evidence ledger.
    active = [r for r in read_evidence(bundle, concept) if r.status == "active"]
    represented_docs = sorted({r.doc_id for r in active})
    represented_set = set(represented_docs)

    # Candidate expansion: add the docs the concept's LITERATURE collectively
    # cites (co-citation neighbours), not just keyword hits. The anchor is the
    # union of the represented docs AND the top relevance candidates -- a
    # seminal paper a broad title query buries (the Grillo aggregative-growth
    # miss) is still cited by the specific nucleation/growth papers that DO
    # rank, so co-citing from that literature surfaces it.
    # Anchor on the WIDER relevance pool (all docs the concept query surfaced,
    # ~5x top_docs), not just the top_docs that became direct candidates: the
    # specific papers that cite a buried seminal work rank in this pool even
    # when they miss the top_docs cut, so co-citing from it reaches the work.
    anchor_docs = sorted(relevance_pool | represented_set)
    existing_ids = {c["doc_id"] for c in candidate_docs} | represented_set
    docs_by_id_all = {d.id: d for d in list_documents(corpus)}
    for did in _cocitation_candidates(corpus, anchor_docs, existing_ids):
        doc = docs_by_id_all.get(did)
        year = _coerce_year((doc.metadata if doc else {}).get("year"))
        candidate_docs.append(
            {"doc_id": did, "year": year, "score": 0.0, "source": "cocitation"}
        )

    # Re-rank candidates by relevance blended with citation proximity: a
    # candidate that cites, or is cited by, the concept's current evidence
    # docs is more clearly part of this concept's literature than one that
    # merely matches keywords. Co-citation candidates carry no keyword score,
    # so proximity is what ranks them.
    proximity = _citation_proximity(
        corpus, [c["doc_id"] for c in candidate_docs], represented_docs
    )
    for c in candidate_docs:
        c["citation_proximity"] = round(proximity.get(c["doc_id"], 0.0), 4)

    def _combined(c: dict) -> float:
        return max(c["score"], 0.0) + _PROXIMITY_WEIGHT * c["citation_proximity"]

    candidate_docs.sort(key=lambda c: (-_combined(c), -c["score"], c["doc_id"]))
    doc_record_counts = Counter(r.doc_id for r in active)
    total_records = len(active)
    max_doc_share = (
        max(doc_record_counts.values()) / total_records if total_records else 0.0
    )

    section_types_represented: list[str] = []
    represented_chunk_ids = [r.chunk_id for r in active if r.chunk_id]
    if corpus.sqlite_path.exists() and represented_chunk_ids:
        con = sqlite3.connect(str(corpus.sqlite_path))
        try:
            placeholders = ",".join("?" * len(represented_chunk_ids))
            rows = con.execute(
                f"SELECT DISTINCT section_type FROM chunks "
                f"WHERE chunk_id IN ({placeholders})",
                represented_chunk_ids,
            ).fetchall()
            section_types_represented = sorted(
                {(r[0] or "body") for r in rows}
            )
        finally:
            con.close()

    # Missing candidates, most-relevant first (candidate_docs is presorted).
    missing_docs = [
        c for c in candidate_docs if c["doc_id"] not in represented_set
    ]

    # Publication-era buckets over candidate years: early(<=p25) /
    # recent(>=p75) / middle. Docs lacking a parseable year are skipped.
    years = [c["year"] for c in candidate_docs if c["year"] is not None]
    counts = {b: [0, 0] for b in ("early", "middle", "recent")}  # [total, repr]
    if years:
        p25 = _percentile(years, 0.25)
        p75 = _percentile(years, 0.75)
        for c in candidate_docs:
            y = c["year"]
            if y is None:
                continue
            if y <= p25:
                b = "early"
            elif y >= p75:
                b = "recent"
            else:
                b = "middle"
            counts[b][0] += 1
            if c["doc_id"] in represented_set:
                counts[b][1] += 1
    year_buckets = {
        b: {"total": t, "represented": r} for b, (t, r) in counts.items()
    }
    empty_buckets = [
        b for b, v in year_buckets.items()
        if v["total"] > 0 and v["represented"] == 0
    ]

    min_represented = min(8, math.ceil(0.6 * len(candidate_docs)))
    # Recall is measured against the CANDIDATE set: how many of the
    # most-relevant docs the page actually cites -- not the raw count of
    # whatever docs it happens to cite. A page citing 11 arbitrary docs while
    # covering only 5 of 12 candidates is a recall MISS, not a pass.
    n_candidates_covered = len(
        represented_set & {c["doc_id"] for c in candidate_docs}
    )
    recall_ok = (
        n_candidates_covered >= min_represented
        and not empty_buckets
        and max_doc_share <= 0.35
    )

    recall = {
        "candidate_docs": candidate_docs,
        "represented_docs": represented_docs,
        "missing_docs": missing_docs,
        "year_buckets": year_buckets,
        "empty_buckets": empty_buckets,
        "section_types_represented": section_types_represented,
        "max_doc_share": round(max_doc_share, 4),
        "min_represented": min_represented,
        "n_candidates_covered": n_candidates_covered,
        "rank_used": rank_used,
        "recall_ok": recall_ok,
    }
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "slug": concept, "recall": recall}))
        return
    typer.echo(
        f"{concept}: recall_ok={recall_ok}  "
        f"candidates_covered={n_candidates_covered}/{len(candidate_docs)} "
        f"(min {min_represented})  represented={len(represented_docs)}  "
        f"missing={len(missing_docs)}  "
        f"max_doc_share={max_doc_share:.2f}  "
        f"empty_buckets={','.join(empty_buckets) or '-'}  "
        f"section_types={','.join(section_types_represented) or '-'}"
    )


# -------------------------------------------------------------- add-gap-note

_GAP_NOTE_TYPES = ["future_work", "unclear", "debated", "understudied",
                   "contradiction"]


@app.command("add-gap-note")
def cmd_add_gap_note(
    chunk_id: str = typer.Option(
        ..., "--chunk-id",
        help="Canonical id or chunk:<short> handle the gap is stated in.",
    ),
    gap_type: str = typer.Option(
        ..., "--type",
        help="future_work | unclear | debated | understudied | contradiction.",
    ),
    gap: str = typer.Option(..., "--gap", help="One-sentence statement of the gap."),
    quote: str = typer.Option(
        ..., "--quote", help="Exact literal quote from the chunk stating the gap.",
    ),
    contradicts_chunk_id: str = typer.Option("", "--contradicts-chunk-id"),
    contradicts_quote: str = typer.Option("", "--contradicts-quote"),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Append a grounded literature-gap note to ``work/notes/literature_gaps.md``.

    A gap is one a chunk explicitly STATES -- an open question, a genuine
    contradiction between two sources, or an understudied point the text names.
    NEVER infer a gap from absent coverage, sparse data, or general knowledge.
    The quote is verified to appear literally in the named chunk (and the
    contradicting quote in its chunk) so a gap cannot be fabricated; the P5
    explorer has ``Bash(wikify *)`` access, so this is how it records the gaps
    the GAP wave surfaces. ``type=contradiction`` requires both
    ``--contradicts-chunk-id`` and ``--contradicts-quote``.
    """
    import sqlite3

    from ..api import Corpus
    from ..grounding import is_grounded

    if gap_type not in _GAP_NOTE_TYPES:
        cli_error(EXIT_VALIDATION, error="bad_gap_type", type=gap_type,
                  allowed=_GAP_NOTE_TYPES)
    if gap_type == "contradiction" and not (contradicts_chunk_id and contradicts_quote):
        cli_error(EXIT_VALIDATION, error="contradiction_requires_second_chunk")
    bundle = _resolve_bundle(run)
    if not corpus_dir.is_dir():
        cli_error(EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir))
    corpus = Corpus(root=corpus_dir)
    canonical_ids, suffix_index = build_suffix_index(corpus.sqlite_path)

    def _resolve_and_read(raw: str) -> tuple[str, str | None]:
        resolved = resolve_chunk_id(
            raw, suffix_index, canonical_ids, sqlite_path=corpus.sqlite_path,
        ) or raw
        con = sqlite3.connect(str(corpus.sqlite_path))
        try:
            row = con.execute(
                "SELECT text FROM chunks WHERE chunk_id=?", (resolved,)
            ).fetchone()
        finally:
            con.close()
        return resolved, (row[0] if row else None)

    resolved_cid, text = _resolve_and_read(chunk_id)
    if text is None:
        cli_error(EXIT_VALIDATION, error="chunk_not_found", chunk_id=chunk_id)
    if not is_grounded(quote, text):
        cli_error(EXIT_VALIDATION, error="quote_not_in_chunk", chunk_id=resolved_cid)
    contradicts_cid = "-"
    if gap_type == "contradiction":
        contradicts_cid, c_text = _resolve_and_read(contradicts_chunk_id)
        if c_text is None:
            cli_error(EXIT_VALIDATION, error="contradicts_chunk_not_found",
                      chunk_id=contradicts_chunk_id)
        if not is_grounded(contradicts_quote, c_text):
            cli_error(EXIT_VALIDATION, error="contradicts_quote_not_in_chunk",
                      chunk_id=contradicts_cid)

    def _clean(s: str) -> str:
        return " ".join((s or "").split()).replace('"', "'")

    notes = bundle.work_dir / "notes" / "literature_gaps.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    c_quote = _clean(contradicts_quote) if gap_type == "contradiction" else "-"
    line = (
        f"- chunk_id: {resolved_cid}; type: {gap_type}; gap: {_clean(gap)}; "
        f'quote: "{_clean(quote)}"; contradicts: {contradicts_cid}; '
        f'contradicts_quote: "{c_quote}"'
    )
    with notes.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if fmt == "json":
        typer.echo(json.dumps({
            "ok": True, "path": str(notes), "chunk_id": resolved_cid,
            "type": gap_type,
        }))
        return
    typer.echo(f"gap note added: {gap_type} @ {resolved_cid}")


# -------------------------------------------------------------- notebook-init


@app.command("notebook-init")
def cmd_notebook_init(
    concept: str = typer.Argument(...),
    seed_docs: str = typer.Option(
        "[]", "--seed-docs", help='JSON list of seed doc handles.'
    ),
    stencil: str | None = typer.Option(None, "--stencil"),
    kind: str | None = typer.Option(
        None, "--kind",
        help="Override card kind (defaults to the work card's kind).",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Create a ``work/concepts/<slug>/notebook.md`` skeleton.

    Idempotent: returns the existing notebook if one is already on
    disk. The investigate explorer calls this on first acceptance for a
    new slug.
    """
    from ..bundle.work.notebook import init_notebook, notebook_path

    concept = _clean_slug_arg(concept)
    bundle = _resolve_bundle(run)
    try:
        seeds = json.loads(seed_docs)
        if not isinstance(seeds, list):
            raise ValueError("seed-docs must be a JSON list")
    except (json.JSONDecodeError, ValueError) as exc:
        cli_error(EXIT_VALIDATION, error="bad_seed_docs", message=str(exc))
    card = load_card(bundle, concept)
    effective_kind = kind or (card.kind if card.front else "article")
    init_notebook(
        bundle,
        slug=concept,
        kind=effective_kind,
        seed_docs=seeds,
        kind_stencil=stencil,
    )
    p = notebook_path(bundle, concept)
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "slug": concept,
                    "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
                }
            )
        )
        return
    typer.echo(f"notebook at {p.relative_to(bundle.root)}")


__all__ = ["app"]
