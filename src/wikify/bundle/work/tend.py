"""Deterministic housekeeping — ``wikify work tend``.

Scope:
- Expire stale per-concept claims.
- Dedup each concept's ``evidence.jsonl``.
- Drain the inbox into concept folders:
    * ``evidence_suggestions.jsonl``  appends to target concept's evidence ledger
    * ``query_feedback.jsonl``        marks ``affected_pages`` ``needs_refine``
    * ``concept_suggestions.jsonl``   creates concept folders for any
                                      title that has no existing concept
    * ``merge_suggestions.jsonl``     marks both concepts ``needs_refine``
                                      so the merge target is reviewed by hand
- Regenerate ``work/index.md`` from concept frontmatter.

Each inbox file is fully drained on success (truncated to zero records).
Deeper merge / canonicalisation work — actually merging two concept
folders, or running the writer subagent on ``needs_refine`` pages — is
the workflow skill's responsibility, not Python's.
"""

from __future__ import annotations

from pathlib import Path

from ...api import Bundle
from ...corpus.handles import HandleIndex
from .card import (
    create_concept,
    list_concept_slugs,
    load_card,
    save_card,
    slugify,
)
from .chunk_ids import build_suffix_index, corpus_path_from_bundle, resolve_chunk_id
from .claim import expire_stale_claims, list_claims
from .evidence import EvidenceRecord, append_evidence, dedup_evidence, read_evidence
from .inbox import list_inbox_files, read_inbox, truncate_inbox


def _consolidate_evidence_suggestions(bundle: Bundle) -> int:
    """Drain ``evidence_suggestions.jsonl`` into target concept ledgers.

    Records have shape ``{"concept": <slug>, "chunk_id", "doc_id",
    "quote", "score", ...}``. The slug is required; records without
    one are dropped. Returns the count appended.
    """
    records = read_inbox(bundle, "evidence_suggestions")
    if not records:
        return 0
    by_slug: dict[str, list[EvidenceRecord]] = {}
    for r in records:
        slug = r.get("concept") or r.get("slug")
        if not slug or "chunk_id" not in r or "doc_id" not in r:
            continue
        by_slug.setdefault(slug, []).append(
            EvidenceRecord.model_validate(
                {k: v for k, v in r.items() if k not in {"concept", "slug"}}
            )
        )
    appended = 0
    for slug, recs in by_slug.items():
        # Only append if the concept actually exists.
        card = load_card(bundle, slug)
        if not card.front:
            continue
        appended += append_evidence(bundle, slug, recs)
    truncate_inbox(bundle, "evidence_suggestions")
    return appended


def _consolidate_concept_suggestions(bundle: Bundle, keep_inbox: bool = False) -> int:
    """Drain ``concept_suggestions.jsonl`` into new concept folders.

    Records have shape ``{"title": <str>, "kind": "article|person",
    "aliases": [...]}``. Existing concepts (matched by slug) are
    skipped. Returns the count of concepts created.

    With ``keep_inbox=True`` the inbox file is preserved post-drain so
    the orchestrator can re-inspect or replay the suggestions.
    """
    records = read_inbox(bundle, "concept_suggestions")
    if not records:
        return 0
    existing = set(list_concept_slugs(bundle))
    created = 0
    for r in records:
        title = r.get("title")
        if not title:
            continue
        s = slugify(title)
        if s in existing:
            continue
        kind = r.get("kind", "article")
        if kind not in {"article", "person"}:
            kind = "article"
        aliases = r.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        seed_doc_handles = r.get("seed_doc_handles") or []
        if not isinstance(seed_doc_handles, list):
            seed_doc_handles = []
        seed_doc_handles = [str(h) for h in seed_doc_handles if isinstance(h, str)]
        create_concept(
            bundle,
            page_id=title,
            kind=kind,
            aliases=list(aliases),
            seed_doc_handles=seed_doc_handles or None,
        )
        existing.add(s)
        created += 1
    if not keep_inbox:
        truncate_inbox(bundle, "concept_suggestions")
    return created


def _mark_needs_refine(bundle: Bundle, slug: str) -> bool:
    card = load_card(bundle, slug)
    if not card.front:
        return False
    card.front["needs_refine"] = True
    save_card(bundle, slug, card)
    return True


def _consolidate_query_feedback(bundle: Bundle) -> int:
    """Drain ``query_feedback.jsonl``, marking affected pages ``needs_refine``.

    Records have shape ``{"affected_pages": [<title or slug>, ...], ...}``.
    A title is matched to a slug via :func:`slugify`. Returns the count
    of cards updated.
    """
    records = read_inbox(bundle, "query_feedback")
    if not records:
        return 0
    existing = set(list_concept_slugs(bundle))
    n = 0
    for r in records:
        affected = r.get("affected_pages") or []
        if not isinstance(affected, list):
            continue
        for item in affected:
            if not isinstance(item, str) or not item:
                continue
            slug = item if item in existing else slugify(item)
            if slug in existing and _mark_needs_refine(bundle, slug):
                n += 1
    truncate_inbox(bundle, "query_feedback")
    return n


def _consolidate_merge_suggestions(bundle: Bundle) -> int:
    """Drain ``merge_suggestions.jsonl``, marking both concepts ``needs_refine``.

    Records have shape ``{"a": <slug-or-title>, "b": <slug-or-title>}``.
    The actual merge (folder coalesce) is a workflow-skill action.
    Returns the count of cards marked.
    """
    records = read_inbox(bundle, "merge_suggestions")
    if not records:
        return 0
    existing = set(list_concept_slugs(bundle))
    n = 0
    for r in records:
        for key in ("a", "b"):
            item = r.get(key)
            if not isinstance(item, str) or not item:
                continue
            slug = item if item in existing else slugify(item)
            if slug in existing and _mark_needs_refine(bundle, slug):
                n += 1
    truncate_inbox(bundle, "merge_suggestions")
    return n


def regenerate_work_index(bundle: Bundle) -> Path:
    """Write ``work/index.md`` summarising concepts + claims + inbox state."""
    bundle.work_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Work index")
    lines.append("")

    slugs = list_concept_slugs(bundle)
    lines.append(f"Concepts: {len(slugs)}")
    if slugs:
        lines.append("")
        lines.append("| slug | page_id | kind | status | needs_refine | evidence |")
        lines.append("|---|---|---|---|---|---|")
        for slug in slugs:
            card = load_card(bundle, slug)
            ev_count = len(read_evidence(bundle, slug))
            lines.append(
                f"| {slug} | {card.page_id} | {card.kind} | {card.status} | "
                f"{str(card.needs_refine).lower()} | {ev_count} |"
            )

    claims = list_claims(bundle)
    lines.append("")
    lines.append(f"Active claims: {len(claims)}")
    for c in claims:
        lines.append(
            f"- {c.get('slug', '?')}: owner={c.get('owner', '?')} "
            f"acquired_at={c.get('acquired_at', '?')}"
        )

    inbox_files = list_inbox_files(bundle)
    lines.append("")
    lines.append(f"Inbox files: {len(inbox_files)}")
    for f in inbox_files:
        lines.append(f"- {f}")

    text = "\n".join(lines) + "\n"
    bundle.work_index_path.write_text(text, encoding="utf-8")
    return bundle.work_index_path


def _sweep_staging_files(bundle: Bundle) -> int:
    """Remove ``work/evidence_staging/<slug>.jsonl`` files whose records are
    all present in the slug's committed evidence ledger.

    Staging files carry short ``chunk:<hex>`` handles; the committed ledger
    holds canonical ids.  Each staging handle is resolved through
    :func:`~wikify.bundle.work.chunk_ids.resolve_chunk_id` before the subset
    check.  If the corpus is unreachable the file is left intact (conservative).
    A staging file is deleted only when every handle resolves to a canonical id
    that already appears in ``evidence.jsonl`` for that slug.  Files with
    unmatched records are left intact.  Returns the count of files removed.
    """
    import json as _json

    staging_dir = bundle.work_dir / "evidence_staging"
    if not staging_dir.is_dir():
        return 0

    # Build suffix index once from the corpus (if reachable).
    corpus_dir = corpus_path_from_bundle(bundle.root)
    canonical_ids: frozenset[str] = frozenset()
    suffix_index: HandleIndex = HandleIndex()
    sqlite_path = None
    if corpus_dir is not None:
        from ...api import Corpus  # noqa: PLC0415 — deferred to avoid circular import
        sqlite_path = Corpus(root=corpus_dir).sqlite_path
        canonical_ids, suffix_index = build_suffix_index(sqlite_path)

    removed = 0
    for staging_file in staging_dir.iterdir():
        if not staging_file.is_file() or staging_file.suffix != ".jsonl":
            continue
        slug = staging_file.stem
        # Read chunk_ids from the staging file.
        staging_raw: list[str] = []
        for line in staging_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = _json.loads(line)
                cid = obj.get("chunk_id")
                if cid:
                    staging_raw.append(cid)
            except Exception:
                continue
        if not staging_raw:
            # Empty staging file — safe to remove regardless of corpus.
            staging_file.unlink()
            removed += 1
            continue

        committed_ids = {r.chunk_id for r in read_evidence(bundle, slug)}

        # Resolve staging handles to canonical ids.
        # For ids that already look canonical (not a chunk: handle) and the
        # corpus is unreachable, fall through to a direct string comparison
        # so the sweep still works for ledgers that were never handle-encoded.
        # When a chunk: handle is present and the corpus is unreachable we
        # cannot validate — leave the file intact (conservative).
        resolved: set[str] = set()
        blocked = False
        for raw in staging_raw:
            if raw.startswith("chunk:"):
                # Handle form: must resolve through corpus index.
                if not canonical_ids:
                    # Corpus unreachable — cannot resolve handle; skip file.
                    blocked = True
                    break
                canon = resolve_chunk_id(
                    raw, suffix_index, canonical_ids, sqlite_path=sqlite_path
                )
                if canon is None:
                    # Unresolvable handle — treat staging as incomplete.
                    blocked = True
                    break
                resolved.add(canon)
            else:
                # Already in canonical (or raw) form; use as-is.
                resolved.add(raw)

        if blocked or not resolved:
            continue

        if resolved.issubset(committed_ids):
            staging_file.unlink()
            removed += 1
    return removed


def tend_bundle(bundle: Bundle, *, keep_inbox: bool = False) -> dict:
    """Run the full tend pass and return a summary dict.

    ``keep_inbox=True`` preserves ``concept_suggestions.jsonl`` after
    consolidation. The other inboxes always drain — they are append-on-
    action with stable semantics and no replay use case.
    """
    summary: dict = {}
    summary["claims_expired"] = expire_stale_claims(bundle)

    # Consolidate inbox first so freshly-appended evidence is deduped
    # and freshly-created concepts appear in the regenerated index.
    summary["concepts_created"] = _consolidate_concept_suggestions(
        bundle, keep_inbox=keep_inbox
    )
    summary["evidence_appended"] = _consolidate_evidence_suggestions(bundle)
    summary["query_feedback_marks"] = _consolidate_query_feedback(bundle)
    summary["merge_suggestion_marks"] = _consolidate_merge_suggestions(bundle)

    deduped = 0
    for slug in list_concept_slugs(bundle):
        deduped += dedup_evidence(bundle, slug)
    summary["evidence_records_deduped"] = deduped

    # Refresh ``evidence_chunks`` / ``evidence_docs`` on each work card
    # from the on-disk ledger so ``work show`` / ``work list`` reflect
    # the current state, not the count at card creation.
    for slug in list_concept_slugs(bundle):
        recs = read_evidence(bundle, slug)
        active = [r for r in recs if r.status == "active"]
        card = load_card(bundle, slug)
        if not card.front:
            continue
        card.front["evidence_chunks"] = len(active)
        card.front["evidence_docs"] = len({r.doc_id for r in active})
        save_card(bundle, slug, card)

    summary["staging_files_removed"] = _sweep_staging_files(bundle)

    regenerate_work_index(bundle)
    summary["index_path"] = str(bundle.work_index_path)
    summary["concepts"] = len(list_concept_slugs(bundle))
    summary["claims_active"] = len(list_claims(bundle))
    summary["inbox_files"] = list_inbox_files(bundle)
    return summary
