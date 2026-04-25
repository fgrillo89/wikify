"""Deterministic housekeeping — ``wikify work tend``.

W4 scope:
- Expire stale per-concept claims.
- Dedup each concept's ``evidence.jsonl``.
- Regenerate ``work/index.md`` from concept frontmatter.

Out of W4 scope (deferred to a follow-up):
- Inbox consolidation (apply evidence/concept/merge/query_feedback
  suggestions back into concept folders). Needs canonicalize +
  corpus access + the writer/refiner subagent loop. The CLI verb
  exists but the action is a no-op in this PR; tend reports that
  inbox files are present but does not drain them.
"""

from __future__ import annotations

from pathlib import Path

from ...api import Bundle
from .card import list_concept_slugs, load_card
from .claim import expire_stale_claims, list_claims
from .evidence import dedup_evidence, read_evidence
from .inbox import list_inbox_files


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


def tend_bundle(bundle: Bundle) -> dict:
    """Run the full tend pass and return a summary dict."""
    summary: dict = {}
    summary["claims_expired"] = expire_stale_claims(bundle)
    deduped = 0
    for slug in list_concept_slugs(bundle):
        deduped += dedup_evidence(bundle, slug)
    summary["evidence_records_deduped"] = deduped
    regenerate_work_index(bundle)
    summary["index_path"] = str(bundle.work_index_path)
    summary["concepts"] = len(list_concept_slugs(bundle))
    summary["claims_active"] = len(list_claims(bundle))
    summary["inbox_files"] = list_inbox_files(bundle)
    return summary
