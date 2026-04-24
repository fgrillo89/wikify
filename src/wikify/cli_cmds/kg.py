"""wikify kg ... — deterministic knowledge-graph queries for skill workflows."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..baselines.pipeline import BaselineConfig, select_evidence_chunks_for_page
from ..distill.preload import preload_corpus
from ..distill.seed import (
    SeedSelectionConfig,
    doc_embeddings,
    greedy_seed_select,
    pagerank_normalised,
)
from ..paths import CorpusPaths
from ..session import load_session

app = typer.Typer(add_completion=False, help="Corpus knowledge-graph queries.")


def _preload(corpus_root: Path):
    return preload_corpus(CorpusPaths(corpus_root))


@app.command("seeds")
def cmd_seeds(
    session_path: Path = typer.Option(..., "--session"),
    max_seeds: int | None = typer.Option(None, "--max-seeds"),
) -> None:
    """Emit the greedy seed document and abstract chunk IDs for the session."""
    session = load_session(session_path)
    preloaded = _preload(Path(session.corpus_root))

    baseline_cfg = BaselineConfig()
    seed_cfg = SeedSelectionConfig(
        pagerank_weight=baseline_cfg.pagerank_weight,
        max_seeds=max_seeds or baseline_cfg.max_seeds,
    )
    embeds, doc_order = doc_embeddings(preloaded.chunks, preloaded.vectors)
    pr_norm = pagerank_normalised(preloaded.knowledge_graph, doc_order)
    seed_doc_ids = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=seed_cfg.max_seeds,
        cfg=seed_cfg,
    )
    seed_chunk_ids: list[str] = []
    for did in seed_doc_ids:
        chunk = preloaded.knowledge_graph.source(did).abstract_chunk()
        if chunk is not None:
            seed_chunk_ids.append(chunk["id"])
    typer.echo(
        json.dumps(
            {
                "seed_doc_ids": list(seed_doc_ids),
                "seed_chunk_ids": seed_chunk_ids,
            }
        )
    )


@app.command("abstracts")
def cmd_abstracts(
    corpus: Path = typer.Option(..., "--corpus"),
    doc_ids: str = typer.Option(..., "--doc-ids", help="JSON array of document ids."),
) -> None:
    """Emit the canonical abstract chunks for each supplied doc id."""
    ids = json.loads(doc_ids)
    if not isinstance(ids, list):
        raise typer.BadParameter("--doc-ids must be a JSON array")
    preloaded = _preload(corpus)
    out = []
    for did in ids:
        chunk = preloaded.knowledge_graph.source(did).abstract_chunk()
        if chunk is None:
            continue
        out.append(
            {
                "doc_id": did,
                "chunk_id": chunk.get("id"),
                "section_type": chunk.get("section_type"),
                "text_len": len(chunk.get("text") or ""),
            }
        )
    typer.echo(json.dumps({"abstracts": out}))


@app.command("evidence")
def cmd_evidence(
    session_path: Path = typer.Option(..., "--session"),
    page_id: str = typer.Option(..., "--page-id"),
    top_k: int = typer.Option(8, "--top-k"),
    max_per_source: int = typer.Option(2, "--max-per-source"),
) -> None:
    """Emit the top-k evidence chunk ids for one page title."""
    session = load_session(session_path)
    preloaded = _preload(Path(session.corpus_root))

    chunk_ids = select_evidence_chunks_for_page(
        page_title=page_id,
        kg=preloaded.knowledge_graph,
        top_k=top_k,
        max_per_source=max_per_source,
    )
    typer.echo(json.dumps({"page_id": page_id, "chunk_ids": chunk_ids}))


__all__ = ["app"]
