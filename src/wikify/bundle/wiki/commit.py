"""``wiki commit`` — promote a validated response to the canonical wiki page.

The commit gate only allows a page to reach ``wiki/articles/<slug>.md``
or ``wiki/people/<slug>.md`` after ``validation.json`` for that
response has ``ok=true`` and the quote-grounding check has passed.

Side effects of a successful commit:

1. Wiki page is written to ``wiki/articles/`` or ``wiki/people/``.
2. Concept card status is set to ``committed``; ``wiki_path`` is
   recorded.
3. Per-attempt artifacts (draft / response / validation) are
   garbage-collected from the concept folder.
4. A ``page_committed`` event is appended to ``run/events.jsonl``.

The wiki graph + page-vector projections are rebuilt only when
``ensure_projections=True`` is passed; the default is False so a
hot commit loop does not pay the embedding cost. The agent runs
``wikify wiki build graph`` / ``wikify wiki build vectors`` once at
the end of a workflow.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ...api import Bundle
from ...models import Evidence, WikiPage
from ..draft.artifact import (
    draft_path,
    gc_attempt,
    read_json,
    response_path,
    validation_path,
)
from ..run.events import Event, append_event
from ..run.lock import run_lock
from ..run.state import load_state
from ..work.card import load_card, save_card
from ..work.evidence import read_evidence

_REF_DEF_RE_TEMPLATE = (
    r'\[\^e(\d+)\]:\s*(?P<rest>.*)'
)


class CommitGateError(RuntimeError):
    """Raised when the commit precondition is not met (no validation.ok=true)."""


@dataclass(frozen=True)
class CommitResult:
    page_path: Path
    page_id: str
    kind: str
    slug: str


def _parse_evidence_from_body(body_markdown: str) -> list[Evidence]:
    """Extract ``[^eN]: <doc_id> > "<quote>"`` entries from the References block."""
    from .page import _extract_evidence

    return [
        Evidence(
            marker=ev.marker,
            chunk_id=ev.chunk_id,
            doc_id=ev.doc_id,
            quote=ev.quote,
            locator=ev.locator,
        )
        for ev in _extract_evidence(body_markdown)
    ]


def _norm_tokens(text: str) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+-]*", text.lower())
        if len(token) > 2
    }


def _phrase_present(text: str, phrase: str) -> bool:
    import re

    phrase = phrase.strip()
    if not phrase:
        return False
    return re.search(rf"\b{re.escape(phrase.lower())}\b", text.lower()) is not None


def _infer_links(bundle: Bundle, *, page: WikiPage) -> list[str]:
    """Infer stable wiki links from current committed pages.

    Writers cannot emit body wikilinks and ``WriteResponse`` has no
    ``links`` field. This deterministic projection gives the wiki graph a
    useful baseline signal from explicit mentions and shared evidence.
    """
    from .page import load_bundle

    wiki_root = bundle.wiki_dir
    if not wiki_root.exists():
        return []
    existing = load_bundle(wiki_root).pages
    page_text = page.body_markdown.lower()
    page_docs = {ev.doc_id for ev in page.evidence if ev.doc_id}
    page_tokens = _norm_tokens(page.title + "\n" + page.body_markdown)
    scored: list[tuple[int, str]] = []
    for other in existing:
        if other.id == page.id:
            continue
        names = [other.title, other.id, *list(other.aliases or [])]
        direct = any(_phrase_present(page_text, name) for name in names)
        other_docs = {ev.doc_id for ev in other.evidence if ev.doc_id}
        doc_overlap = len(page_docs & other_docs)
        other_tokens = _norm_tokens(other.title + "\n" + other.body_clean)
        token_overlap = len(page_tokens & other_tokens)
        score = (1000 if direct else 0) + doc_overlap * 100 + token_overlap
        if score > 0 and (direct or doc_overlap > 0):
            scored.append((score, other.id))
    return [page_id for _, page_id in sorted(scored, reverse=True)[:5]]


def relevant_committed_artifacts(bundle: Bundle, doc_ids) -> list[str]:
    """Committed data-artifact ids whose backing claims share a source
    DOCUMENT with ``doc_ids`` (a page's active-evidence doc set).

    Relevance is DOC-level, not chunk-level. The DATA wave deliberately
    harvests the number-dense chunks the P1-P5 article explorers skip, so a
    data artifact and the article it generalizes are grounded in disjoint
    chunk sets by construction -- a chunk intersection is always empty.
    Matching on the source document (the artifact's backing claims resolved
    to their ``doc_id``) instead lets an artifact flag its article.
    Deterministic (sorted); empty when the bundle has no claim store yet.
    Both ``commit_page`` (write-time snapshot) and ``work refine-candidates``
    (live check) call this with the page's active-evidence doc ids, so a
    refreshed page converges.
    """
    ids = list(dict.fromkeys(d for d in (doc_ids or []) if d))
    if not ids or not bundle.claims_db_path.exists():
        return []
    from ...data.store import DataStore

    store = DataStore.open(bundle.root)
    try:
        placeholders = ",".join("?" * len(ids))
        rows = store.con.execute(
            "SELECT DISTINCT a.artifact_id FROM data_artifacts a "
            "JOIN data_artifact_claims ac ON ac.artifact_id = a.artifact_id "
            "JOIN data_points p ON p.claim_id = ac.claim_id "
            f"WHERE p.doc_id IN ({placeholders}) AND a.status = 'committed'",
            ids,
        )
        return sorted({r["artifact_id"] for r in rows})
    finally:
        store.close()


def commit_page(
    bundle: Bundle,
    *,
    slug: str,
    actor: str = "cli",
    ensure_projections: bool = False,
    owner: str | None = None,
    lock_ttl_seconds: int = 60,
) -> CommitResult:
    """Promote ``slug``'s validated response to the wiki layout.

    Acquires the bundle ``run/lock`` for the duration of the mutation
    sequence (write page, update card, gc, emit event) so a parallel
    process cannot interleave a concurrent commit. Raises
    :class:`CommitGateError` when the precondition fails;
    ``LockHeldError`` propagates if another process holds the lock.
    """
    draft_p = draft_path(bundle, slug)
    response_p = response_path(bundle, slug)
    verdict_p = validation_path(bundle, slug)

    # Pre-flight checks (cheap; do not need the lock).
    if not draft_p.is_file():
        raise CommitGateError(f"draft.json missing for {slug}")
    if not response_p.is_file():
        raise CommitGateError(f"response.json missing for {slug}")
    if not verdict_p.is_file():
        raise CommitGateError(
            f"validation.json missing for {slug}; run `wikify draft check` first"
        )
    verdict = read_json(verdict_p)
    if not verdict.get("ok"):
        raise CommitGateError(
            f"validation.json for {slug} has ok=false; cannot commit"
        )

    lock_owner = owner or f"commit-page/pid-{os.getpid()}"
    with run_lock(bundle, owner=lock_owner, ttl_seconds=lock_ttl_seconds):
        # Re-read the verdict under the lock so a concurrent invalidation is caught.
        verdict = read_json(verdict_p)
        if not verdict.get("ok"):
            raise CommitGateError(
                f"validation.json for {slug} has ok=false; cannot commit"
            )

        response = read_json(response_p)
        response.pop("schema_version", None)

        page_id = response.get("page_id") or load_card(bundle, slug).page_id
        page_kind = response.get("page_kind") or load_card(bundle, slug).kind
        body_markdown = response["body_markdown"]
        figures = response.get("figures") or []
        evidence = _parse_evidence_from_body(body_markdown)

        page = WikiPage(
            id=page_id,
            kind=page_kind,
            title=page_id,
            aliases=load_card(bundle, slug).aliases,
            body_markdown=body_markdown,
            evidence=evidence,
            figures=figures,
        )
        page.links = _infer_links(bundle, page=page)

        target_dir = (
            bundle.wiki_articles_dir
            if page_kind == "article"
            else bundle.wiki_people_dir
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        page_path = target_dir / f"{slug}.md"
        page_path.write_text(_render_page(page), encoding="utf-8")
        if figures:
            page_path.with_suffix(".figures.json").write_text(
                json.dumps(figures, indent=2), encoding="utf-8"
            )
        else:
            fig_sidecar = page_path.with_suffix(".figures.json")
            if fig_sidecar.exists():
                fig_sidecar.unlink()

        # Refine baseline = the evidence the DRAFT was built from, i.e. what
        # the writer actually had available at build time, read back off
        # draft.json (already validated present above). NOT a fresh live-ledger
        # read: chunks appended between ``draft build`` and here would inflate
        # the baseline with evidence the committed page never saw, so the page
        # would never re-trigger when it should. A page whose writer cited only
        # a subset of its gathered chunks still converges, because the baseline
        # counts the whole gathered set. ``work refine-candidates`` compares
        # this against the live active-evidence count.
        evidence_total = len(read_json(draft_p).get("evidence") or [])

        # Data-artifact relevance snapshot: the committed data artifacts that
        # share a source document with this page's live active evidence. Read
        # from the same active-evidence ledger ``work refine-candidates``
        # checks against, so a re-commit records the now-current set and the
        # page stops flagging as a ``new_data`` refine candidate.
        active_doc_ids = [
            r.doc_id for r in read_evidence(bundle, slug) if r.status == "active"
        ]
        data_artifacts_seen = relevant_committed_artifacts(bundle, active_doc_ids)

        card = load_card(bundle, slug)
        card.front["status"] = "committed"
        card.front["wiki_path"] = str(
            page_path.relative_to(bundle.root)
        ).replace("\\", "/")
        save_card(bundle, slug, card)

        gc_attempt(bundle, slug)
        _project_wiki_page(bundle, page=page, slug=slug)

        try:
            run_id = load_state(bundle).run_id
        except FileNotFoundError:
            run_id = ""
        append_event(
            bundle,
            Event(
                run_id=run_id,
                type="page_committed",
                actor=actor,
                page_id=page_id,
                data={
                    "path": str(page_path.relative_to(bundle.root)).replace("\\", "/"),
                    "slug": slug,
                    "kind": page_kind,
                    "evidence_count": len(evidence),
                    "evidence_total": evidence_total,
                    "data_artifacts_seen": data_artifacts_seen,
                },
            ),
        )

    # Incrementally embed the page so semantic wiki_find sees it next round
    # (F26). Done AFTER the lock is released and AFTER `page_committed` is
    # recorded, so a slow/hanging embedder cannot extend the locked critical
    # path past its TTL or leave a committed page with no commit event. The
    # finalize `wiki rebuild` remains the backstop; a failure is recorded as
    # `page_embedding_failed`, never silently swallowed.
    _embed_committed_page_best_effort(
        bundle, page=page, slug=slug, actor=actor,
        owner=lock_owner, ttl_seconds=lock_ttl_seconds,
    )

    if ensure_projections:
        rebuild_projections(bundle)

    return CommitResult(
        page_path=page_path, page_id=page_id, kind=page_kind, slug=slug
    )


def _embed_committed_page_best_effort(
    bundle: Bundle, *, page: WikiPage, slug: str, actor: str,
    owner: str, ttl_seconds: int,
) -> None:
    try:
        from .derived import embed_committed_page

        # Re-acquire the run lock so the embedding's wiki.db write serialises
        # with a concurrent `wiki rebuild` (which also locks); a held lock
        # raises LockHeldError and is recorded below rather than racing.
        with run_lock(bundle, owner=owner, ttl_seconds=ttl_seconds):
            embed_committed_page(bundle, page)
    except Exception as exc:  # noqa: BLE001 - embedding is an optional accelerant
        try:
            run_id = load_state(bundle).run_id
        except FileNotFoundError:
            run_id = ""
        append_event(
            bundle,
            Event(
                run_id=run_id,
                type="page_embedding_failed",
                actor=actor,
                page_id=page.id,
                data={"error": f"{type(exc).__name__}: {exc}", "slug": slug},
            ),
        )


def _project_wiki_page(bundle: Bundle, *, page: WikiPage, slug: str) -> None:
    """Persist the just-committed page to wiki.db (idempotent per page)."""
    from .store import open_wiki_store, upsert_wiki_page

    con = open_wiki_store(bundle.sqlite_path)
    try:
        upsert_wiki_page(
            con,
            page_id=page.id,
            slug=slug,
            title=page.title or page.id,
            kind=page.kind,
            body=page.body_markdown,
            frontmatter={"aliases": list(page.aliases or [])},
            evidence=[
                {"chunk_id": e.chunk_id, "doc_id": e.doc_id, "marker": e.marker}
                for e in page.evidence or []
            ],
            links=list(page.links or []),
        )
    finally:
        con.close()


def rebuild_projections(bundle: Bundle) -> None:
    """Rebuild every ``derived/`` projection from the committed wiki.

    Calls :func:`bundle.wiki.derived.rebuild_index`,
    :func:`rebuild_graph`, and :func:`rebuild_vectors` in sequence.
    The vectors rebuild is the most expensive step (per-page
    embedding); call this only at the end of a workflow.
    """
    from .derived import rebuild_graph, rebuild_index, rebuild_vectors

    rebuild_index(bundle)
    rebuild_graph(bundle)
    rebuild_vectors(bundle)


# --- Page rendering ----------------------------------------------------


def _render_page(page: WikiPage) -> str:
    lines: list[str] = ["---"]
    lines.append(f"id: {page.id}")
    lines.append(f"kind: {page.kind}")
    lines.append(f"title: {page.title}")
    lines.append(f"aliases: {json.dumps(list(page.aliases or []))}")
    lines.append(f"links: {json.dumps(list(page.links or []))}")
    if page.kind == "person":
        lines.append("tags: [author]")
    lines.append("---")
    lines.append("")

    body = page.body_markdown.strip()
    if not body.startswith(f"# {page.title}"):
        lines.append(f"# {page.title}")
        lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines) + "\n"
