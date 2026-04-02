"""Post-ingest corpus refresh routines."""

from __future__ import annotations

import threading
from pathlib import Path

from rich.console import Console

console = Console()


def get_vocab_cache_path() -> Path:
    """Return the cached corpus vocabulary path."""
    from scholarforge.config import settings

    return settings.data_dir / "corpus_vocabulary.json"


def load_corpus_vocabulary() -> list[str]:
    """Load cached corpus vocabulary. Returns empty list if no cache exists."""
    import json

    cache = get_vocab_cache_path()
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    return []


def save_corpus_vocabulary(vocabulary: list[str]) -> None:
    """Persist the cached corpus vocabulary."""
    import json

    cache = get_vocab_cache_path()
    cache.write_text(json.dumps(vocabulary, ensure_ascii=False), encoding="utf-8")


def run_incremental_refresh(paper_id: str) -> None:
    """Fast post-ingestion refresh for a single paper."""
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import embed_summaries, query_similar
    from scholarforge.store.models import Chunk, FigureRef, Paper
    from scholarforge.vault.linker import _extract_declared_keywords, _to_display
    from scholarforge.vault.writer import ensure_vault_dirs, write_paper_note

    ensure_vault_dirs()

    with get_session() as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            return

        chunks = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()
        full_text = "\n\n".join(c.content for c in chunks)
        search_text = f"{paper.title or ''} {paper.summary or ''} {full_text}"

        declared = _extract_declared_keywords(search_text)

        if declared:
            topics = [_to_display(kw) for kw in declared]
        else:
            from scholarforge.vault.linker import _match_corpus_vocabulary

            vocab = load_corpus_vocabulary()
            matched = _match_corpus_vocabulary(search_text, vocab)
            topics = [_to_display(kw) for kw in matched]

        from scholarforge.store.models import PaperTopic

        with get_session() as topic_session:
            existing_topics = topic_session.exec(
                select(PaperTopic).where(PaperTopic.paper_id == paper.id)
            ).all()
            for pt in existing_topics:
                topic_session.delete(pt)
            topic_session.flush()
            is_declared = bool(declared)
            for topic in topics:
                topic_session.add(
                    PaperTopic(paper_id=paper.id, topic=topic, is_declared=is_declared)
                )
            topic_session.commit()

        embed_summaries([paper])
        from scholarforge.store.embeddings import embed_chunks

        embed_chunks(chunks)

        similar_pairs = query_similar(paper.id, n_results=5)
        all_papers = session.exec(select(Paper)).all()
        id_to_display: dict[str, str] = {p.id: p.display_name() for p in all_papers}
        similar_names = [id_to_display[sid] for sid, _ in similar_pairs if sid in id_to_display]

        frs = session.exec(select(FigureRef).where(FigureRef.paper_id == paper.id)).all()
        figure_refs = [(fr.figure_key, fr.caption_text) for fr in frs] or None

        write_paper_note(
            paper,
            chunks_count=len(chunks),
            figures_count=len(frs) if frs else 0,
            topics=topics if topics else None,
            similar_to=similar_names if similar_names else None,
            figure_refs=figure_refs,
            full_text=full_text,
        )

    console.print(
        f"[dim]  Incremental: {len(topics)} topics, {len(similar_names)} similar papers[/dim]"
    )


def run_background_refresh() -> None:
    """Spawn a background thread to refresh cross-paper signals."""

    def _refresh() -> None:
        try:
            refresh_corpus()
        except RuntimeError as exc:
            if "interpreter shutdown" in str(exc) or "cannot schedule" in str(exc):
                return
            console.print(f"[yellow]Background refresh error:[/yellow] {exc}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Background refresh error:[/yellow] {exc}")

    thread = threading.Thread(target=_refresh, daemon=False, name="corpus-refresh")
    thread.start()
    console.print("[dim]  Background corpus refresh started...[/dim]")


def refresh_corpus(new_paper_ids: set[str] | None = None) -> None:
    """Run all batch post-ingestion refresh steps."""
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from sqlmodel import func, select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import embed_summaries, get_all_similar
    from scholarforge.store.models import Chunk, Citation, FigureRef, Paper
    from scholarforge.vault.coupler import compute_coupling
    from scholarforge.vault.linker import compute_all_links, write_topic_notes
    from scholarforge.vault.writer import ensure_vault_dirs, write_paper_note

    ensure_vault_dirs()

    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        all_chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()

    chunks_by_paper: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in all_chunks:
        chunks_by_paper[chunk.paper_id].append(chunk)

    papers_with_text: list[tuple[Paper, str]] = []
    for paper in papers:
        full_text = "\n\n".join(c.content for c in chunks_by_paper.get(paper.id, []))
        papers_with_text.append((paper, full_text))

    paper_ids = [p.id for p in papers]
    console.print(f"[bold]Running batch steps on {len(papers)} papers...[/bold]")

    from scholarforge.extract.cite_match import build_citation_graph
    from scholarforge.extract.figure_refs import extract_figure_refs
    from scholarforge.store.models import PaperTopic

    citations_by_paper: dict[str, list[str]] = {}
    with get_session() as session:
        for paper in papers:
            cites = session.exec(select(Citation).where(Citation.paper_id == paper.id)).all()
            if cites:
                citations_by_paper[paper.id] = [c.raw_text for c in cites]

    def _task_topics():
        result = compute_all_links(papers_with_text)
        save_corpus_vocabulary(result[1])
        return result

    def _task_citations():
        return build_citation_graph(papers, citations_by_paper)

    def _task_figure_refs():
        targets = (
            [(p, t) for p, t in papers_with_text if p.id in new_paper_ids]
            if new_paper_ids
            else papers_with_text
        )
        with get_session() as sess:
            for paper, text in targets:
                new_refs = extract_figure_refs(text, paper.id)
                old_refs = sess.exec(select(FigureRef).where(FigureRef.paper_id == paper.id)).all()
                for old in old_refs:
                    sess.delete(old)
                for ref in new_refs:
                    sess.merge(ref)
            sess.commit()
            return sess.exec(select(func.count(FigureRef.id))).one()

    def _task_embed():
        import logging

        from scholarforge.store.embeddings import embed_chunks

        logger = logging.getLogger("scholarforge.ingest")
        logger.info("Embedding %d paper summaries...", len(papers))
        n_summaries = embed_summaries(papers)
        logger.info("Summaries embedded: %d", n_summaries)
        logger.info("Embedding %d chunks...", len(all_chunks))
        n_chunks = embed_chunks(all_chunks)
        logger.info("Chunks embedded: %d", n_chunks)
        return {"summaries": n_summaries, "chunks": n_chunks}

    def _task_coupling():
        return compute_coupling(paper_ids)

    results: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="batch") as pool:
        futures = {
            pool.submit(_task_topics): "topics",
            pool.submit(_task_citations): "citations",
            pool.submit(_task_figure_refs): "figure_refs",
            pool.submit(_task_embed): "embed",
            pool.submit(_task_coupling): "coupling",
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                console.print(f"[green]  {name} done[/green]")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]  {name} failed: {exc}[/red]")
                raise

    per_paper_links, corpus_vocabulary, paper_declared = results["topics"]  # type: ignore[misc]
    citation_graph: dict[str, list[str]] = results["citations"]  # type: ignore[assignment]
    total_figure_refs: int = results["figure_refs"]  # type: ignore[assignment]
    embedded: dict[str, int] = results["embed"]  # type: ignore[assignment]
    coupling_map: dict[str, list[str]] = results["coupling"]  # type: ignore[assignment]

    with get_session() as session:
        existing_topics = session.exec(select(PaperTopic)).all()
        for pt in existing_topics:
            session.delete(pt)
        session.flush()
        for paper in papers:
            links = per_paper_links.get(paper.id, {"topics": []})
            is_declared = bool(paper_declared.get(paper.id))
            for topic in links["topics"]:
                session.add(PaperTopic(paper_id=paper.id, topic=topic, is_declared=is_declared))
        session.commit()

    cite_count = sum(len(v) for v in citation_graph.values())
    console.print(
        f"[green]  Phase 1 complete: {len(corpus_vocabulary)} vocab, "
        f"{cite_count} citations, {total_figure_refs} fig refs, "
        f"{embedded} embedded[/green]"
    )

    similar_map = get_all_similar(paper_ids, n_results=5)
    console.print("[green]  k-NN similarity computed[/green]")

    id_to_display = {paper.id: paper.display_name() for paper in papers}

    paper_figure_refs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with get_session() as session:
        all_frs = session.exec(select(FigureRef)).all()
        for fr in all_frs:
            paper_figure_refs[fr.paper_id].append((fr.figure_key, fr.caption_text))

    topic_papers: dict[str, list[str]] = defaultdict(list)
    for paper in papers:
        links = per_paper_links.get(paper.id, {"topics": []})
        display = id_to_display[paper.id]
        for topic in links["topics"]:
            topic_papers[topic].append(display)

    write_topic_notes(topic_papers)

    from scholarforge.vault.writer import vault_dir, write_graph_config

    write_graph_config()

    authors_dir = vault_dir() / "authors"
    if authors_dir.exists():
        for file in authors_dir.iterdir():
            if file.suffix == ".md":
                file.unlink()

    text_by_paper = {paper.id: text for paper, text in papers_with_text}

    for paper in papers:
        chunks_count = len(chunks_by_paper.get(paper.id, []))
        figures_count = len(paper_figure_refs.get(paper.id, []))
        links = per_paper_links.get(paper.id, {"topics": []})

        similar_names = [
            id_to_display[sid] for sid in similar_map.get(paper.id, []) if sid in id_to_display
        ]
        coupled_names = [
            id_to_display[cid] for cid in coupling_map.get(paper.id, []) if cid in id_to_display
        ]
        cites_names = [
            id_to_display[cid] for cid in citation_graph.get(paper.id, []) if cid in id_to_display
        ]

        write_paper_note(
            paper,
            chunks_count=chunks_count,
            figures_count=figures_count,
            topics=links["topics"],
            cites=cites_names if cites_names else None,
            similar_to=similar_names if similar_names else None,
            cites_same=coupled_names if coupled_names else None,
            figure_refs=paper_figure_refs.get(paper.id) or None,
            full_text=text_by_paper.get(paper.id),
        )

    from scholarforge.config import settings
    from scholarforge.zotero.bibtex_library import rebuild_bibtex_library

    rebuild_bibtex_library(papers, settings.data_dir)

    try:
        from scholarforge.store.precompute import precompute_all

        precompute_all()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Precompute cache warning: {exc}[/yellow]")

    console.print(
        f"[green]Batch complete: {len(papers)} paper notes regenerated with all signals[/green]"
    )
