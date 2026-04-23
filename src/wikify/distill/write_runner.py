"""Shared write pass used by the standard pipeline and the baseline.

Both ``distill/pipeline.py`` (mid-session ``write_now`` and end-of-extract
write phase) and ``baselines/pipeline.py`` need the same per-page write
loop: budget-gated, validator-tolerant, with rolling-average write-cost
budget pre-checks. Keeping it in one place is what lets us refactor the
write contract without forking the two surfaces.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..meter import BudgetExceededError, CostMeter
from ..models import WikiPage
from ..paths import BundlePaths
from ..types import Writer
from .dossier import DossierStore
from .write_prep import WriteRequestConfig, build_write_request, is_writable_page

if TYPE_CHECKING:
    from .strategy import StrategyConfig


def run_write_pass(
    pages: list,
    max_concepts: int,
    writer: Writer,
    meter: CostMeter,
    strategy: StrategyConfig,
    bundle: BundlePaths,
    briefs: dict,
    dossier_store: DossierStore,
    chunks_by_id: dict,
    images_index: object,
    write_req_cfg: WriteRequestConfig,
    author_ctx: dict,
    citation_index: dict,
    knowledge_graph: object,
    budget_haiku_eq: float,
    verbalize: bool,
    write_rejections: list[dict],
    equations_index: object | None = None,
) -> None:
    """Run a write pass over pages.

    Used at end-of-extraction by the standard pipeline, mid-session by
    guided ``write_now``, and as the only write surface in the
    abstract-first baseline. The rolling-average write cost is recomputed
    per call so the pre-budget check stays honest as the writer's actual
    cost drifts.
    """
    avg_write_cost = 30_000.0
    n_writes_completed = 0
    try:
        for page in pages[:max_concepts]:
            if not is_writable_page(page):
                continue
            if meter.spent_haiku_eq + avg_write_cost > budget_haiku_eq * 1.05:
                write_rejections.append({"page_id": page.id, "reason": "budget_truncated"})
                continue
            spent_before = meter.spent_haiku_eq
            req = build_write_request(
                page,
                pages,
                briefs,
                dossier_store,
                chunks_by_id,
                images_index,
                write_req_cfg,
                author_ctx,
                citation_index,
                knowledge_graph=knowledge_graph,
                equations_index=equations_index,
            )
            try:
                resp = writer.write(req)
            except ValidationError as exc:
                sys.stderr.write(
                    f"[{meter._run_id}] writer REJECTED page={page.id!r}: "  # noqa: SLF001
                    f"{type(exc).__name__}: {str(exc)[:200]}\n"
                )
                write_rejections.append({"page_id": page.id, "error": str(exc)[:500]})
                continue
            page.body_markdown = resp.body_markdown
            page.equations = [eq.model_dump() for eq in resp.equations]
            if verbalize:
                append_verbalize(
                    bundle, meter._run_id, "write", page.id, resp.reasoning,  # noqa: SLF001
                )
            call_cost = meter.spent_haiku_eq - spent_before
            n_writes_completed += 1
            avg_write_cost = (
                avg_write_cost * (n_writes_completed - 1) + call_cost
            ) / n_writes_completed
    except BudgetExceededError:
        pass


def rebuild_wiki_graph(bundle: BundlePaths, pages: list[WikiPage]) -> None:
    """Build and persist the wiki knowledge graph + page vectors.

    Called by both the standard pipeline (after the write loop) and the
    baseline (after its own write pass) to materialise the wiki-side
    graph + per-page embeddings on disk.
    """
    from ..embedding import current_backend, embed_passages, embedder_for
    from ..store.vectors import save_vectors
    from ..store.wiki_graph import (
        build_wiki_graph,
        build_wiki_vectors,
        save_wiki_graph,
    )

    # Build uses passage embedding (indexing wiki page bodies); the graph
    # stores a query-mode callable because search() encodes user queries.
    wiki_vectors = build_wiki_vectors(pages, embed_passages)
    backend = current_backend()
    query_embed = embedder_for(
        str(backend["backend"]), backend.get("model"), mode="query",
    )
    wkg = build_wiki_graph(pages, vectors=wiki_vectors, embed_fn=query_embed)
    save_wiki_graph(bundle.graph_path, wkg)
    if wiki_vectors.ids:
        save_vectors(bundle.wiki_vectors_path, wiki_vectors)


def append_verbalize(
    bundle: BundlePaths,
    run_id: str,
    role: str,
    rid: str,
    reasoning: str,
) -> None:
    """Append one handler-reasoning line to ``_meta/verbalize.jsonl``.

    Called only when the run was invoked with ``verbalize=True`` and
    the handler populated a non-empty ``reasoning`` field in its
    response. Silent no-op for empty reasoning so the log stays tight.
    """
    if not reasoning:
        return
    path = bundle.verbalize_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "when": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "rid": rid,
        "reasoning": reasoning,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
