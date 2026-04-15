"""Iteration persistence helpers for refine/merge runs."""

import json
from datetime import datetime, timezone
from typing import cast

from ..meter import CostMeter
from ..models import Evidence, PageKind, WikiPage
from ..paths import BundlePaths
from ..store.wiki_files import write_page as write_page_file
from ..store.wiki_index import build_index
from .explorer import ExplorerState
from .write_prep import crosslink


def load_existing_pages(bundle: BundlePaths) -> list[WikiPage]:
    """Load prior wiki pages from a bundle dir as ``WikiPage`` objects."""
    from ..store.wiki_bundle import parse_page

    pages: list[WikiPage] = []
    for sub in ("articles", "people"):
        page_dir = bundle.root / sub
        if not page_dir.exists():
            continue
        for path in sorted(page_dir.glob("*.md")):
            try:
                parsed = parse_page(path)
            except Exception:
                continue
            pages.append(
                WikiPage(
                    id=parsed.id,
                    kind=cast(PageKind, parsed.kind),
                    title=parsed.title,
                    aliases=list(parsed.aliases),
                    body_markdown=parsed.body_clean,
                    evidence=[
                        Evidence(
                            marker=ev.marker,
                            chunk_id=ev.chunk_id,
                            doc_id=ev.doc_id,
                            quote=ev.quote,
                            locator=ev.locator,
                        )
                        for ev in parsed.evidence
                    ],
                    links=list(parsed.links),
                    provenance=dict(parsed.provenance or {}),
                )
            )
    return pages


def updated_page_provenance(
    *,
    existing: dict,
    run_id: str,
    model_id: str,
    strategy_name: str,
    iteration: str,
    drafted: bool,
) -> dict:
    prov = dict(existing or {})
    history = list(prov.get("history", []))
    history.append(
        {
            "run_id": run_id,
            "iteration": iteration,
            "model": model_id,
            "strategy": strategy_name,
            "drafted_body": drafted,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    prov.update(
        {
            "run_id": run_id,
            "model": model_id,
            "strategy": strategy_name,
            "iteration": iteration,
            "current_run_id": run_id,
            "history": history,
        }
    )
    return prov


def append_run_history(bundle: BundlePaths, snapshot: dict) -> None:
    bundle.ensure()
    bundle.run_history_path.parent.mkdir(parents=True, exist_ok=True)
    with bundle.run_history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, separators=(",", ":"), default=str) + "\n")


def save_coverage_memory(bundle: BundlePaths, state: ExplorerState, *, run_id: str) -> None:
    bundle.meta_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "seen_chunks": sorted(state.seen_chunks),
        "doc_seen_counts": dict(state.doc_seen_counts),
        "coverage_residuals": state.coverage_residuals,
    }
    bundle.coverage_memory_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_coverage_memory(bundle: BundlePaths) -> dict:
    if not bundle.coverage_memory_path.exists():
        return {}
    try:
        return json.loads(bundle.coverage_memory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def run_merge_iteration(
    bundle: BundlePaths,
    merge_from_bundle: BundlePaths | None,
    meter: CostMeter,
    *,
    model_id: str,
    strategy_name: str,
) -> None:
    if merge_from_bundle is None:
        raise ValueError("merge iteration requires merge_from_bundle")
    merged = [
        p
        for p in merge_pages(load_existing_pages(bundle), load_existing_pages(merge_from_bundle))
        if p.evidence
    ]
    merged = crosslink(merged)
    for page in merged:
        page.provenance = updated_page_provenance(
            existing=(page.provenance or {}),
            run_id=meter._run_id,  # noqa: SLF001
            model_id=model_id,
            strategy_name=strategy_name,
            iteration="merge",
            drafted=bool(page.body_markdown.strip()),
        )
        write_page_file(bundle, page)
    build_index(bundle, merged).save()
    meter.write_snapshot(bundle.run_path)
    snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    snap.update(
        {
            "iteration": "merge",
            "merge_from": str(merge_from_bundle.root),
            "n_pages": len(merged),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    bundle.run_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    append_run_history(bundle, snap)


def merge_pages(left: list[WikiPage], right: list[WikiPage]) -> list[WikiPage]:
    by_id: dict[str, WikiPage] = {}
    alias_to_id: dict[str, str] = {}
    for page in left:
        by_id[page.id] = page
        _index_page_aliases(alias_to_id, page, page.id)
    for page in right:
        key = alias_to_id.get(_normalize_title(page.title))
        if key is None:
            key = page.id
            by_id[key] = page
            _index_page_aliases(alias_to_id, page, key)
            continue
        _merge_page_into(by_id[key], page)
    return list(by_id.values())


def _index_page_aliases(alias_to_id: dict[str, str], page: WikiPage, page_id: str) -> None:
    alias_to_id[_normalize_title(page.title)] = page_id
    for alias in page.aliases:
        alias_to_id[_normalize_title(alias)] = page_id


def _merge_page_into(target: WikiPage, source: WikiPage) -> None:
    seen_ev = {(e.chunk_id, e.quote) for e in target.evidence}
    for ev in source.evidence:
        tup = (ev.chunk_id, ev.quote)
        if tup in seen_ev:
            continue
        seen_ev.add(tup)
        target.evidence.append(
            Evidence(
                marker=f"e{len(target.evidence) + 1}",
                chunk_id=ev.chunk_id,
                doc_id=ev.doc_id,
                quote=ev.quote,
                locator=ev.locator,
            )
        )
    if len((source.body_markdown or "").strip()) > len((target.body_markdown or "").strip()):
        target.body_markdown = source.body_markdown
    target.links = sorted(set(target.links) | set(source.links))
    target.aliases = sorted(set(target.aliases) | set(source.aliases))


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())
