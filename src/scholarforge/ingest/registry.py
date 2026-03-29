"""Dispatcher: file extension -> parser.

Two ingestion modes:
- Single file: fast incremental (process only the new paper), then background
  refresh of cross-paper signals (topics, similarity, coupling).
- Batch (--parallel): parse all PDFs in parallel, then one synchronous full
  refresh at the end.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

console = Console()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}


def ingest_path(path: Path, parallel: bool = False, max_workers: int = 4) -> int:
    """Ingest a file or directory. Returns count of documents ingested."""
    if path.is_file():
        return _ingest_file(path)
    elif path.is_dir():
        files = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(sorted(path.rglob(f"*{ext}")))
        if not files:
            return 0

        if parallel and len(files) > 1:
            return _ingest_parallel(files, max_workers)
        else:
            count = 0
            for file in files:
                count += _ingest_file(file, background_refresh=False)
            # After sequential batch, do one full refresh (not N background refreshes)
            if count > 0:
                run_batch_steps()
            return count
    return 0


def _ingest_parallel(files: list[Path], max_workers: int) -> int:
    """Parse PDFs in parallel, persist sequentially."""
    from scholarforge.ingest.pdf import ParsedPaper, persist_parsed
    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

    pdf_files = [f for f in files if f.suffix.lower() == ".pdf"]
    other_files = [f for f in files if f.suffix.lower() != ".pdf"]

    # Filter out already-ingested PDFs
    to_parse = []
    with get_session() as session:
        for f in pdf_files:
            file_hash = hashlib.sha256(f.read_bytes()).hexdigest()
            existing = session.get(Paper, file_hash)
            if existing:
                console.print(f"[dim]Skipping (unchanged):[/dim] {f.name}")
            else:
                to_parse.append(f)

    if not to_parse:
        console.print("[yellow]No new papers to ingest.[/yellow]")
        return 0

    count = 0
    parsed_results: list[ParsedPaper] = []

    # Parse in parallel (CPU-bound: pymupdf4llm)
    console.print(f"[bold]Parsing {len(to_parse)} PDFs with {max_workers} workers...[/bold]")
    with Progress() as progress:
        task = progress.add_task("Parsing PDFs...", total=len(to_parse))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_parse_worker, str(f)): f for f in to_parse}
            for future in as_completed(futures):
                f = futures[future]
                try:
                    parsed = future.result()
                    parsed_results.append(parsed)
                    count += 1
                except Exception as e:
                    console.print(f"[red]Error parsing {f.name}:[/red] {e}")
                progress.advance(task)

    # Persist sequentially (SQLite + vault writes)
    console.print(f"[bold]Persisting {len(parsed_results)} papers...[/bold]")
    for parsed in parsed_results:
        persist_parsed(parsed)
        console.print(
            f"[green]Ingested:[/green] {Path(parsed.paper.source_path).name} "
            f"({len(parsed.chunks)} chunks, {len(parsed.figures)} figures)"
        )

    # Run batch steps: linking + embeddings + coupling + vault regeneration
    run_batch_steps()

    # Process other files sequentially
    for f in other_files:
        count += _ingest_file(f)

    return count


def _parse_worker(path_str: str):
    """Worker function for parallel PDF parsing (must be picklable)."""
    from pathlib import Path

    from scholarforge.ingest.pdf import parse_pdf

    return parse_pdf(Path(path_str))


def _get_vocab_cache_path() -> Path:
    """Path to the cached corpus vocabulary JSON file."""
    from scholarforge.config import settings

    return settings.data_dir / "corpus_vocabulary.json"


def _load_corpus_vocabulary() -> list[str]:
    """Load cached corpus vocabulary. Returns empty list if no cache."""
    import json

    cache = _get_vocab_cache_path()
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    return []


def _save_corpus_vocabulary(vocabulary: list[str]) -> None:
    """Save corpus vocabulary to cache."""
    import json

    cache = _get_vocab_cache_path()
    cache.write_text(json.dumps(vocabulary, ensure_ascii=False), encoding="utf-8")


def _run_incremental_steps(paper_id: str) -> None:
    """Fast O(1) post-ingestion for a single paper.

    - Extracts topics from the paper's own declared keywords + cached corpus vocab
    - Embeds its abstract into ChromaDB
    - Queries k-NN for this paper only
    - Writes this paper's vault note with all available signals
    """
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import embed_abstracts, query_similar
    from scholarforge.store.models import Chunk, FigureRef, Paper
    from scholarforge.vault.linker import _extract_declared_keywords, _to_display
    from scholarforge.vault.writer import write_paper_note

    with get_session() as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            return

        chunks = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()
        full_text = "\n\n".join(c.content for c in chunks)
        search_text = f"{paper.title or ''} {paper.abstract or ''} {full_text}"

        # ── Topics: own declared keywords + cached corpus vocabulary ─────────
        declared = _extract_declared_keywords(search_text)

        if declared:
            topics = [_to_display(kw) for kw in declared]
        else:
            # Match against cached vocabulary (O(1) file read, no DB scan)
            from scholarforge.vault.linker import _match_corpus_vocabulary

            vocab = _load_corpus_vocabulary()
            matched = _match_corpus_vocabulary(search_text, vocab)
            topics = [_to_display(kw) for kw in matched]

        # ── Embed abstract ──────────────────────────────────────────────────
        embed_abstracts([paper])

        # ── k-NN similarity (query only this paper) ────────────────────────
        similar_pairs = query_similar(paper.id, n_results=5)
        all_papers = session.exec(select(Paper)).all()
        id_to_display: dict[str, str] = {p.id: p.display_name() for p in all_papers}
        similar_names = [id_to_display[sid] for sid, _ in similar_pairs if sid in id_to_display]

        # ── Figure refs from DB ─────────────────────────────────────────────
        frs = session.exec(select(FigureRef).where(FigureRef.paper_id == paper.id)).all()
        figure_refs = [(fr.figure_key, fr.caption_text) for fr in frs] or None

        # ── Write vault note ────────────────────────────────────────────────
        chunks_count = len(chunks)
        figures_count = len(frs) if frs else 0

        write_paper_note(
            paper,
            chunks_count=chunks_count,
            figures_count=figures_count,
            topics=topics if topics else None,
            similar_to=similar_names if similar_names else None,
            figure_refs=figure_refs,
        )

    console.print(
        f"[dim]  Incremental: {len(topics)} topics, {len(similar_names)} similar papers[/dim]"
    )


def _run_background_refresh() -> None:
    """Spawn a background thread to refresh all cross-paper signals.

    Updates topic vocabulary matches, k-NN neighbors, coupling, and vault notes
    for existing papers that may be affected by the newly added paper.
    """

    def _refresh() -> None:
        try:
            run_batch_steps()
        except Exception as e:
            console.print(f"[yellow]Background refresh error:[/yellow] {e}")

    thread = threading.Thread(target=_refresh, daemon=False, name="corpus-refresh")
    thread.start()
    console.print("[dim]  Background corpus refresh started...[/dim]")


def run_batch_steps() -> None:
    """Run all batch post-ingestion steps: linking, embeddings, coupling, vault regen."""
    from sqlmodel import func, select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import embed_abstracts, get_all_similar
    from scholarforge.store.models import Chunk, Citation, FigureRef, Paper
    from scholarforge.vault.coupler import compute_coupling
    from scholarforge.vault.linker import (
        compute_all_links,
        write_topic_notes,
    )
    from scholarforge.vault.writer import write_paper_note

    # ── 1. Load all papers + text (single query for all chunks) ───────────
    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        all_chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()

    # Group chunks by paper_id
    from collections import defaultdict

    chunks_by_paper: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in all_chunks:
        chunks_by_paper[chunk.paper_id].append(chunk)

    papers_with_text: list[tuple[Paper, str]] = []
    for paper in papers:
        full_text = "\n\n".join(c.content for c in chunks_by_paper.get(paper.id, []))
        papers_with_text.append((paper, full_text))

    paper_ids = [p.id for p in papers]
    console.print(f"[bold]Running batch steps on {len(papers)} papers...[/bold]")

    # ── 2. Automatic topic extraction ─────────────────────────────────────────
    per_paper_links, corpus_vocabulary = compute_all_links(papers_with_text)
    _save_corpus_vocabulary(corpus_vocabulary)
    console.print("[green]  Topics extracted[/green]")

    # ── 3. Citation graph: match bibliography entries to corpus papers ────────
    from scholarforge.extract.cite_match import build_citation_graph

    citations_by_paper: dict[str, list[str]] = {}
    with get_session() as session:
        for paper in papers:
            cites = session.exec(select(Citation).where(Citation.paper_id == paper.id)).all()
            if cites:
                citations_by_paper[paper.id] = [c.raw_text for c in cites]

    citation_graph = build_citation_graph(papers, citations_by_paper)
    cite_count = sum(len(v) for v in citation_graph.values())
    console.print(f"[green]  Citation graph: {cite_count} cross-references resolved[/green]")

    # ── 3b. Re-extract figure/table refs (picks up improved patterns) ─────────
    from scholarforge.extract.figure_refs import extract_figure_refs

    with get_session() as session:
        for paper, text in papers_with_text:
            new_refs = extract_figure_refs(text, paper.id)
            # Clear old refs and insert new ones
            old_refs = session.exec(select(FigureRef).where(FigureRef.paper_id == paper.id)).all()
            for old in old_refs:
                session.delete(old)
            for ref in new_refs:
                session.merge(ref)
        session.commit()
        total_figure_refs = session.exec(select(func.count(FigureRef.id))).one()
    console.print(f"[green]  Extracted {total_figure_refs} figure/table refs[/green]")

    # ── 4. Abstract embeddings ───────────────────────────────────────────────
    embedded = embed_abstracts(papers)
    console.print(f"[green]  Embedded {embedded} abstracts into ChromaDB[/green]")

    # ── 5. k-NN similarity ──────────────────────────────────────────────────
    similar_map = get_all_similar(paper_ids, n_results=5)
    console.print("[green]  k-NN similarity computed[/green]")

    # ── 6. Bibliographic coupling ────────────────────────────────────────────
    coupling_map = compute_coupling(paper_ids)
    coupled_count = sum(1 for v in coupling_map.values() if v)
    console.print(f"[green]  Bibliographic coupling: {coupled_count} papers coupled[/green]")

    # ── 7. Build lookup helpers ──────────────────────────────────────────────
    # Map paper_id -> display_name for wikilink resolution
    id_to_display: dict[str, str] = {}
    for paper in papers:
        id_to_display[paper.id] = paper.display_name()

    # Load all figure refs in one query
    paper_figure_refs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with get_session() as session:
        all_frs = session.exec(select(FigureRef)).all()
        for fr in all_frs:
            paper_figure_refs[fr.paper_id].append((fr.figure_key, fr.caption_text))

    # ── 8. Write topic hub notes ───────────────────────────────────────────
    topic_papers: dict[str, list[str]] = defaultdict(list)
    for paper in papers:
        links = per_paper_links.get(paper.id, {"topics": []})
        display = id_to_display[paper.id]
        for t in links["topics"]:
            topic_papers[t].append(display)

    write_topic_notes(topic_papers)

    # ── 9. Clear author notes so they're rebuilt fresh (avoids stale wikilinks)
    from scholarforge.vault.writer import vault_dir

    authors_dir = vault_dir() / "authors"
    if authors_dir.exists():
        for f in authors_dir.iterdir():
            if f.suffix == ".md":
                f.unlink()

    # ── 10. Regenerate all paper vault notes with full data ──────────────────
    # Use already-loaded data instead of per-paper queries
    for paper in papers:
        chunks_count = len(chunks_by_paper.get(paper.id, []))
        figures_count = len(paper_figure_refs.get(paper.id, []))
        links = per_paper_links.get(paper.id, {"topics": []})

        # Resolve similar_to IDs to display names
        similar_names = [
            id_to_display[sid] for sid in similar_map.get(paper.id, []) if sid in id_to_display
        ]
        # Resolve coupling IDs to display names
        coupled_names = [
            id_to_display[cid] for cid in coupling_map.get(paper.id, []) if cid in id_to_display
        ]

        # Resolve citation graph IDs to display names
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
        )

    console.print(
        f"[green]Batch complete: {len(papers)} paper notes regenerated with all signals[/green]"
    )


def _ingest_file(path: Path, background_refresh: bool = True) -> int:
    """Ingest a single file based on extension.

    When background_refresh is True (default for single-file ingest), runs
    fast incremental steps synchronously then spawns a background thread
    to update the rest of the corpus. When False (batch mode), only parses
    and persists — caller is responsible for running batch steps.
    """
    ext = path.suffix.lower()
    paper_id: str | None = None

    if ext == ".pdf":
        from scholarforge.ingest.pdf import ingest_pdf

        paper_id = ingest_pdf(path, return_id=True)
    elif ext == ".docx":
        from scholarforge.ingest.docx import ingest_docx

        paper_id = ingest_docx(path, return_id=True)
    elif ext == ".pptx":
        from scholarforge.ingest.pptx import ingest_pptx

        paper_id = ingest_pptx(path, return_id=True)
    else:
        console.print(f"[yellow]Unsupported format:[/yellow] {path.name}")
        return 0

    if not paper_id:
        return 0

    if background_refresh:
        # Single-file mode: fast incremental for this paper, then background corpus refresh
        _run_incremental_steps(paper_id)
        _run_background_refresh()

    return 1
