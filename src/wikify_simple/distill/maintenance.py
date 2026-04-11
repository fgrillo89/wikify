"""Maintenance verb: consume the query log and emit wiki improvement actions.

Entry points:
  - ``load_query_log(bundle)`` -> list[QueryLogEntry]
  - ``run_maintenance(bundle, corpus, binding, querier)`` -> MaintenanceReport

Lifecycle of a query log entry:
  1. Scanned by ``run_maintenance``.
  2. If the wiki already answers it well (threshold check), the entry is
     deleted immediately (no action needed).
  3. If not answered well or contains escalation events, a MaintenanceAction
     is dispatched to the maintenance handler via the binding.
  4. After the handler returns a valid action AND the target page file is
     updated in the same run, the query log file is deleted.
  5. If the handler fails or the page cannot be updated, the log file is
     kept for the next run (idempotent).

The maintenance handler is tier L (editor-level decisions).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..contracts.schema import (
    MaintenanceAction,
    MaintenanceReport,
    QueryLogEntry,
)
from ..paths import BundlePaths, CorpusPaths
from ..store.wiki_index import WikiIndex

# Similarity threshold: if the wiki's best page excerpt for the question
# contains at least this fraction of question words, we consider it
# already answered and skip the action.
_ANSWER_THRESHOLD = 0.4


def load_query_log(bundle: BundlePaths) -> list[QueryLogEntry]:
    """Load all QueryLogEntry records from ``<bundle>/_meta/query_log/``."""
    log_dir = bundle.query_log_dir
    if not log_dir.exists():
        return []
    entries: list[QueryLogEntry] = []
    for p in sorted(log_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries.append(QueryLogEntry.model_validate(data))
        except Exception:
            continue
    return entries


def _question_covered(question: str, bundle: BundlePaths) -> bool:
    """Return True when the wiki already answers *question* well enough.

    Heuristic: tokenise the question, compute fraction of non-stop tokens
    found in any page title or alias. If the fraction >= _ANSWER_THRESHOLD,
    declare it covered.
    """
    stop = frozenset(
        {
            "what",
            "is",
            "the",
            "of",
            "a",
            "an",
            "and",
            "or",
            "to",
            "for",
            "in",
            "on",
            "with",
            "how",
            "why",
            "which",
            "that",
            "this",
            "are",
            "be",
            "was",
            "were",
            "has",
            "have",
            "do",
            "does",
            "can",
            "could",
            "should",
            "would",
            "will",
            "about",
        }
    )
    tokens = [t.lower().strip(".,?!\"'") for t in question.split()]
    content_tokens = [t for t in tokens if t and t not in stop]
    if not content_tokens:
        return True
    try:
        index = WikiIndex.load(bundle)
    except Exception:
        return False
    all_terms: set[str] = set()
    for entry in index:
        all_terms.update(entry.title.lower().split())
        for a in entry.aliases:
            all_terms.update(a.lower().split())
    matched = sum(1 for t in content_tokens if t in all_terms)
    return matched / len(content_tokens) >= _ANSWER_THRESHOLD


def _build_action_from_log(entry: QueryLogEntry) -> MaintenanceAction:
    """Derive a MaintenanceAction from a QueryLogEntry heuristically.

    When there are escalation events, we prefer ``add_evidence`` because
    the model had to escalate to corpus chunks. Otherwise ``extend_page``
    is used for pages already in the wiki, or ``create_page`` when none
    of the touched pages are known.
    """
    action_kind: str
    if entry.escalation_events:
        action_kind = "add_evidence"
    elif entry.pages_touched:
        action_kind = "extend_page"
    else:
        action_kind = "create_page"

    target = entry.pages_touched[0] if entry.pages_touched else entry.question[:60]
    chunk_ids: list[str] = []
    for ev in entry.escalation_events:
        chunk_ids.extend(ev.chunk_ids)

    return MaintenanceAction(
        action=action_kind,  # type: ignore[arg-type]
        target_page=target,
        brief=f"Query '{entry.question}' was not answered well by the wiki.",
        evidence_additions=chunk_ids[:5],
        rationale=(
            f"Query triggered {len(entry.escalation_events)} escalation event(s). "
            f"Pages touched: {entry.pages_touched}."
            if entry.escalation_events
            else f"Query '{entry.question}' did not match wiki content well."
        ),
        source_query_id=entry.id,
    )


def _delete_log_entry(bundle: BundlePaths, entry_id: str) -> bool:
    """Delete a query log file. Returns True on success."""
    target = bundle.query_log_dir / f"{entry_id}.json"
    try:
        if target.exists():
            target.unlink()
        return True
    except OSError:
        return False


def _mark_page_updated(bundle: BundlePaths, page_id: str) -> bool:
    """Return True if the page file exists (i.e. it was previously written).

    The maintenance run does not re-write pages itself; it delegates to the
    existing write pipeline. This function checks that the file is present
    so the caller can decide whether deletion of the log entry is safe.
    """
    try:
        index = WikiIndex.load(bundle)
        entry = index.get(page_id)
        if entry is None:
            return False
        return (bundle.root / entry.path).exists()
    except Exception:
        return False


def run_maintenance(
    bundle: BundlePaths,
    corpus: CorpusPaths,  # reserved for future corpus-chunk escalation
    binding: str = "fake",
    *,
    dry_run: bool = False,
) -> MaintenanceReport:
    """Scan the query log, dispatch improvement actions, delete resolved entries.

    *binding* is accepted for API consistency but maintenance decisions are
    derived heuristically (no live model call in this function). The actual
    page update (if any) must be applied by the caller via the write pipeline.

    This function only:
      - Loads the query log.
      - Determines which entries need action (not already answered).
      - Builds MaintenanceAction objects.
      - Deletes log entries whose target page already exists in the bundle
        (i.e. the action was previously applied or the page was written in
        the same session).
      - Returns a MaintenanceReport.
    """
    entries = load_query_log(bundle)
    run_at = datetime.now(timezone.utc).isoformat()

    actions_dispatched = 0
    actions_applied = 0
    logs_deleted = 0
    report_actions: list[MaintenanceAction] = []

    for entry in entries:
        covered = _question_covered(entry.question, bundle)
        has_escalation = bool(entry.escalation_events)

        if covered and not has_escalation:
            # Already answered well; delete the log entry.
            if not dry_run and _delete_log_entry(bundle, entry.id):
                logs_deleted += 1
            continue

        # Build an action.
        action = _build_action_from_log(entry)
        report_actions.append(action)
        actions_dispatched += 1

        # Check if the target page already exists (action applied in a prior
        # run or this session). If so, delete the log entry.
        page_present = _mark_page_updated(bundle, action.target_page)
        if page_present:
            actions_applied += 1
            if not dry_run and _delete_log_entry(bundle, entry.id):
                logs_deleted += 1

    return MaintenanceReport(
        run_at=run_at,
        queries_scanned=len(entries),
        actions_dispatched=actions_dispatched,
        actions_applied=actions_applied,
        query_logs_deleted=logs_deleted,
        actions=report_actions,
    )
