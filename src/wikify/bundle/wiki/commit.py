"""``wiki commit`` — promote a validated response to the canonical wiki page.

The commit gate enforces the contract from
``docs/skill-centric-execution-plan.md``: a page only reaches
``wiki/articles/<slug>.md`` (or ``wiki/people/<slug>.md``) once
``validation.json`` for that response has ``ok=true`` and the
quote-grounding check has passed.

Side effects of a successful commit:

1. Wiki page is written to ``wiki/articles/`` or ``wiki/people/``.
2. Concept card status is set to ``committed``; ``wiki_path`` is
   recorded.
3. Per-attempt artifacts (draft / response / validation) are
   garbage-collected from the concept folder.
4. A ``page_committed`` event is appended to ``run/events.jsonl``.

The wiki graph + page-vector projections are rebuilt by the legacy
``post_commit.py::rebuild_wiki_graph`` only when ``ensure_projections``
is True; the v2 default is False because the workflow can defer the
rebuild to ``wiki build graph`` so a hot loop does not pay the
embedding cost on every commit.
"""

from __future__ import annotations

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
from ..run.state import load_state
from ..work.card import load_card, save_card

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
    import re

    out: list[Evidence] = []
    pattern = re.compile(
        r'^\[\^e(?P<n>\d+)\]:\s*(?P<doc_id>[^>\s]+)(?:[^>]*)>\s*"(?P<quote>.+?)"\s*$',
        re.MULTILINE,
    )
    for m in pattern.finditer(body_markdown):
        out.append(
            Evidence(
                marker=f"e{m.group('n')}",
                chunk_id="",
                doc_id=m.group("doc_id"),
                quote=m.group("quote"),
            )
        )
    return out


def commit_page(
    bundle: Bundle,
    *,
    slug: str,
    actor: str = "cli",
    ensure_projections: bool = False,
) -> CommitResult:
    """Promote ``slug``'s validated response to the v2 wiki layout.

    Raises :class:`CommitGateError` when the precondition fails.
    """
    draft_p = draft_path(bundle, slug)
    response_p = response_path(bundle, slug)
    verdict_p = validation_path(bundle, slug)

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

    response = read_json(response_p)
    response.pop("schema_version", None)

    page_id = response.get("page_id") or load_card(bundle, slug).page_id
    page_kind = response.get("page_kind") or load_card(bundle, slug).kind
    body_markdown = response["body_markdown"]
    evidence = _parse_evidence_from_body(body_markdown)

    page = WikiPage(
        id=page_id,
        kind=page_kind,
        title=page_id,
        aliases=load_card(bundle, slug).aliases,
        body_markdown=body_markdown,
        evidence=evidence,
    )

    target_dir = (
        bundle.wiki_articles_dir if page_kind == "article" else bundle.wiki_people_dir
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}.md"
    page_path = target_dir / filename
    page_path.write_text(_render_page(page), encoding="utf-8")

    # Update concept card.
    card = load_card(bundle, slug)
    card.front["status"] = "committed"
    card.front["wiki_path"] = str(page_path.relative_to(bundle.root)).replace("\\", "/")
    save_card(bundle, slug, card)

    # Garbage-collect per-attempt artifacts.
    gc_attempt(bundle, slug)

    # Emit event.
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
            },
        ),
    )

    if ensure_projections:
        rebuild_projections(bundle)

    return CommitResult(
        page_path=page_path, page_id=page_id, kind=page_kind, slug=slug
    )


def rebuild_projections(bundle: Bundle) -> None:
    """Rebuild ``derived/`` projections from the committed wiki.

    W6 MVP scope: regenerate ``derived/index.json`` with the page list.
    Full graph + page-vector rebuild (``derived/graph.json`` and
    ``derived/vectors.npz``) is delegated to ``wiki build graph`` and
    ``wiki build vectors`` — the full implementation lives in
    ``post_commit.py``; we leave that as a follow-up.
    """
    from .derived import rebuild_index

    rebuild_index(bundle)


# --- Page rendering (v2) -----------------------------------------------


def _render_page(page: WikiPage) -> str:
    lines: list[str] = ["---"]
    lines.append(f"id: {page.id}")
    lines.append(f"kind: {page.kind}")
    lines.append(f"title: {page.title}")
    lines.append(f"aliases: [{', '.join(page.aliases)}]")
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
