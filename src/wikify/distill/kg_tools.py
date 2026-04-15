"""KG tools for the guided-mode orchestrator.

Each function executes a local KG query (no LLM call) and returns a
JSON-serializable result. These are the "free" tools the orchestrator
can call during multi-turn tool-calling before committing to a
terminal action (sample_chunks, write_now, done).

See study-design.md, "KG tools" table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph
    from ..meter import CostMeter
    from ..models import WikiPage


def search_chunks(
    kg: KnowledgeGraph,
    *,
    query: str,
    top_k: int = 10,
    source_id: str | None = None,
) -> list[dict]:
    """Vector search over corpus chunks, optionally scoped to one source."""
    if source_id:
        return kg.source(source_id).chunks().search(query, top_k=top_k)
    return kg.chunks().search(query, top_k=top_k)


def get_source_info(kg: KnowledgeGraph, *, source_id: str) -> dict:
    """Return metadata for a single source."""
    hit = kg.source(source_id).first()
    return hit or {"error": f"source {source_id!r} not found"}


def list_sources(
    kg: KnowledgeGraph,
    *,
    sort_by: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List corpus sources, optionally sorted by pagerank/citation_count/year."""
    qb = kg.sources(kind="corpus")
    if sort_by:
        qb = qb.top(limit, by=sort_by)
    return qb.collect()[:limit]


def get_citations(
    kg: KnowledgeGraph,
    *,
    source_id: str,
    direction: str = "references",
) -> list[dict]:
    """Get citations for a source. direction: 'references' or 'cited_by'."""
    src = kg.source(source_id)
    if direction == "cited_by":
        return src.cited_by().collect()
    return src.references().collect()


def get_coverage(snapshot: dict) -> dict:
    """Return coverage state from the sampler snapshot."""
    return {
        "content_stats": snapshot.get("content_stats", {}),
        "doc_coverage": snapshot.get("doc_coverage", {}),
        "residual_histogram": snapshot.get("residual_histogram", {}),
        "top_gap_chunks": snapshot.get("top_gap_chunks", [])[:10],
    }


def get_pages(pages: list[WikiPage]) -> list[dict]:
    """Return summary of current wiki pages."""
    return [
        {
            "id": p.id,
            "title": p.title,
            "kind": p.kind,
            "n_evidence": len(p.evidence),
            "has_body": bool(p.body_markdown.strip()),
        }
        for p in pages
    ]


def get_budget(meter: CostMeter, budget_target: float) -> dict:
    """Return current budget state."""
    spent = meter.spent_haiku_eq
    return {
        "spent_haiku_eq": round(spent, 1),
        "remaining_haiku_eq": round(max(0.0, budget_target - spent), 1),
        "budget_target_haiku_eq": round(budget_target, 1),
    }


# -- KG tool names used by the multi-turn dispatch to distinguish
#    free tools (executed locally) from terminal actions.
KG_TOOL_NAMES: frozenset[str] = frozenset({
    "search_chunks",
    "get_source_info",
    "list_sources",
    "get_citations",
    "get_coverage",
    "get_pages",
    "get_budget",
})


# -- Tool schemas advertised to the orchestrator in the request payload.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_chunks": {
        "description": "Vector search over corpus chunks.",
        "parameters": {
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "default": 10},
            "source_id": {
                "type": "string",
                "default": None,
                "description": "Scope to one source",
            },
        },
    },
    "get_source_info": {
        "description": "Get metadata for a single source.",
        "parameters": {
            "source_id": {"type": "string", "description": "Source ID"},
        },
    },
    "list_sources": {
        "description": "List corpus sources.",
        "parameters": {
            "sort_by": {
                "type": "string",
                "default": None,
                "description": "pagerank | citation_count | year",
            },
            "limit": {"type": "integer", "default": 20},
        },
    },
    "get_citations": {
        "description": "Get citations for a source.",
        "parameters": {
            "source_id": {"type": "string"},
            "direction": {
                "type": "string",
                "default": "references",
                "description": "references | cited_by",
            },
        },
    },
    "get_coverage": {
        "description": "Return coverage state and residual histogram.",
        "parameters": {},
    },
    "get_pages": {
        "description": "Return summary of current wiki pages.",
        "parameters": {},
    },
    "get_budget": {
        "description": "Return current budget state.",
        "parameters": {},
    },
}
