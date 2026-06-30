"""DAG primitives + the refresh DAG.

Pipelines in ``wikify.ingest`` are expressed as an ordered list of waves.
Each wave contains one or more steps that run in parallel; waves
themselves execute sequentially so a later wave may depend on the
results of any earlier one.

Two wave shapes are supported:

- ``kind="threads"`` (default): steps run on a ``ThreadPoolExecutor``.
  Good for I/O + CPU helpers that don't clash on the GIL.  All refresh
  waves use this kind.

- ``kind="mixed"``: exactly two steps run concurrently under one asyncio
  event loop — step[0] is a network-bound async coroutine (invoked
  directly) and step[1] is a GPU/CPU-bound sync function (dispatched to
  ``loop.run_in_executor``).  Used by the ingest DAG's ``resolve+parse``
  wave so DOI batch resolution (HTTP) and Docling content parsing
  (process pool) overlap on the wall clock instead of serialising.

Each step is a ``Callable[[dict], None]`` (or ``Awaitable[None]`` for
mixed waves) that reads from and publishes into a shared ``ctx`` dict
constructed by the caller.

To add a new derived artifact: define ``_refresh_<name>(ctx)`` below and
register it in the appropriate ``Wave`` in ``REFRESH_DAG`` (or introduce
a new wave).  To add a new ingest phase, see ``ingest_steps.py`` and
``INGEST_DAG`` defined there.
"""

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from .bibtex import (
    enrich_doc_metadata,
    read_existing_bib_titles,
    write_corpus_bibliography,
)
from .coupling import compute_coupling
from .topics import extract_topics, write_topics

# ---------------------------------------------------------------------------
# DAG primitives
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """A single pipeline operation: a name plus the function that runs it.

    ``fn`` is normally ``Callable[[dict], None]``.  In a ``kind="mixed"``
    wave the first step's ``fn`` may be an async coroutine function
    (``Callable[[dict], Awaitable[None]]``) — it is awaited directly
    inside the wave's event loop.  The second step is always sync and
    is dispatched to an executor.
    """

    name: str
    fn: Callable[[dict], None] | Callable[[dict], Awaitable[None]]


@dataclass
class Wave:
    """A group of steps that run concurrently within a single stage.

    ``kind`` selects the concurrency primitive:

    - ``"threads"`` (default): steps run on a ``ThreadPoolExecutor``.
    - ``"mixed"``: exactly two steps run under one asyncio event loop;
      step[0] is awaited as a coroutine, step[1] is run in a worker
      thread via ``run_in_executor``.  Used when one step is network-
      bound (async HTTP) and the other is CPU/GPU-bound (process pool
      owned inside the step) so they overlap cleanly.
    """

    label: str
    steps: list[Step]
    kind: Literal["threads", "mixed"] = "threads"


def run_dag(dag: list[Wave], ctx: dict, *, timings: dict) -> None:
    """Execute ``dag`` over ``ctx``, recording per-wave timings.

    Waves run sequentially.  Within each wave the ``kind`` field picks
    the dispatcher: ``threads`` for the standard thread-pool fan-out,
    ``mixed`` for the async + worker-thread pair used by ingest pass
    2 + 3.  Exceptions propagate to the caller.
    """
    from .pipeline import _timed

    for wave in dag:
        with _timed(timings, wave.label):
            if wave.kind == "mixed":
                _run_mixed_wave(wave, ctx)
            elif len(wave.steps) == 1:
                wave.steps[0].fn(ctx)
            else:
                with ThreadPoolExecutor(max_workers=len(wave.steps)) as pool:
                    futs = {pool.submit(step.fn, ctx): step.name for step in wave.steps}
                    for fut in futs:
                        fut.result()  # propagate exceptions


def _run_mixed_wave(wave: Wave, ctx: dict) -> None:
    """Run a mixed wave: step[0] async, step[1] sync, gathered.

    The first step's ``fn`` must be an async coroutine function.  The
    second step's ``fn`` is a plain sync callable; it's dispatched to a
    one-thread executor so both progress concurrently.  Exceptions from
    either raise out via ``asyncio.gather``.
    """
    if len(wave.steps) != 2:
        raise ValueError(
            f"mixed wave {wave.label!r} needs exactly 2 steps, got {len(wave.steps)}"
        )
    async_step, sync_step = wave.steps
    if not inspect.iscoroutinefunction(async_step.fn):
        raise TypeError(
            f"mixed wave {wave.label!r}: step[0] {async_step.name!r} "
            f"must be an async coroutine function"
        )

    async def _drive() -> None:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            sync_fut = loop.run_in_executor(pool, sync_step.fn, ctx)
            await asyncio.gather(async_step.fn(ctx), sync_fut)

    asyncio.run(_drive())


# ---------------------------------------------------------------------------
# Refresh steps
# ---------------------------------------------------------------------------


def _refresh_doc_similarity(ctx: dict) -> None:
    """Compute doc-level embedding similarity (independent of citations)."""
    from .pipeline import _compute_doc_similarity

    _compute_doc_similarity(ctx["docs"], ctx["pairs"], ctx["store"])


def _refresh_citation_edges(ctx: dict) -> None:
    """Compute citation links + bibliographic coupling (needs enriched citations)."""
    from .pipeline import _resolve_citations

    _resolve_citations(ctx["docs"])
    coupling = compute_coupling(ctx["docs"], min_strength=3, top_k=5)
    for doc in ctx["docs"]:
        doc.cites_same = coupling.get(doc.id, [])


def _refresh_topics(ctx: dict) -> None:
    vocab = extract_topics(ctx["pairs"], declared_per_doc=ctx["declared"])
    write_topics(ctx["paths"], vocab)


def _refresh_openalex(ctx: dict) -> None:
    """Resolve citations via OpenAlex API (DOI + bulk reference expansion).

    Seeds the resolver with the corpus papers' own DOIs so corpus titles
    land in the title index that Phase 4 fuzzy-matches against -- that
    is what surfaces no-DOI bibs as in-corpus references. Scopes the
    Phase 3 referenced_works expansion to those same corpus DOIs so
    Wave C does not spend ~half its wall-clock fetching the references
    of out-of-corpus papers (whose 2nd-degree refs are also out-of-
    corpus and almost never match a corpus paper).
    """
    if not ctx.get("resolve_bibliography_doi", False):
        return
    import asyncio
    import os

    from wikify.citations.db import DatabaseManager
    from wikify.citations.resolver import AsyncResolver

    all_cits = []
    for doc in ctx["docs"]:
        all_cits.extend(doc.citations or [])
    if not all_cits:
        return

    corpus_dois: list[str] = []
    for doc in ctx["docs"]:
        d = (doc.metadata or {}).get("doi")
        if d:
            corpus_dois.append(str(d))
    seed_dicts: list[dict] = [
        {"raw_text": "", "doi": d, "title": "", "year": None}
        for d in corpus_dois
    ]

    db_path = ctx["paths"].root / ".citestore.db"

    async def _run() -> None:
        async with DatabaseManager(db_path) as db:
            email = os.environ.get("OPENALEX_EMAIL", "wikify@example.com")
            resolver = AsyncResolver(
                db,
                email=email,
                expand_references=True,
                expand_corpus_dois=set(corpus_dois) or None,
            )
            try:
                cit_dicts = [c.to_dict() if hasattr(c, "to_dict") else c for c in all_cits]
                full_input = seed_dicts + cit_dicts
                results = await resolver.resolve_batch(full_input)
                bib_results = results[len(seed_dicts):]
            finally:
                await resolver.close()

        # Map results back onto CitationEntry objects
        result_by_text: dict[str, object] = {}
        for r in bib_results:
            if r.source_text:
                result_by_text[r.source_text] = r

        for cit in all_cits:
            raw = cit.raw_text if hasattr(cit, "raw_text") else cit.get("raw_text", "")
            r = result_by_text.get(raw)
            if r is None or r.work is None:
                continue
            w = r.work
            cit.resolution = "openalex"
            cit.title = w.title
            cit.authors = w.authors
            cit.year = w.year or cit.year
            cit.venue = w.journal
            cit.volume = w.volume
            cit.pages = (
                f"{w.first_page}--{w.last_page}".strip("-")
                if w.first_page or w.last_page
                else ""
            )
            cit.publisher = w.publisher
            cit.doi = w.doi or cit.doi

    asyncio.run(_run())


def _refresh_cite_heuristics(ctx: dict) -> None:
    """Enrich citations with heuristic parsing + (tiered) DOI resolution.

    ``ctx["cite_resolution"]`` drives the speed/completeness trade-off:

    - ``"off"``:       heuristic parse only; no network work. Fastest.
    - ``"crossref"``:  CrossRef batch only; no doi.org fallback. Default.
    - ``"full"``:      CrossRef + doi.org fallback. Legacy; slow on cold
                       caches (5-40 min on corpora with many refs).
    """
    from .cite_parse import enrich_citations

    mode = str(ctx.get("cite_resolution") or "crossref").lower()
    if mode == "off":
        enrich_citations(
            ctx["docs"],
            cache_path=ctx["paths"].root / ".citestore.db",
            use_doi=False,
        )
        return

    enrich_citations(
        ctx["docs"],
        cache_path=ctx["paths"].root / ".citestore.db",
        use_doi=True,
        skip_content_neg=(mode != "full"),
    )


def _refresh_doc_enrichment(ctx: dict) -> None:
    """Apply title/author/DOI/venue fallbacks to every doc and persist
    the enriched list back into ``ctx["docs"]``.

    Without this, the title fallback (``"Word Document"`` placeholder ->
    filename-derived title) only reaches the bibtex writer's local copy,
    so the persisted ``documents`` rows and SQLite graph edges keep the
    raw placeholder. Running this step before bibliography, doc resave,
    and the SQLite re-projection makes every downstream consumer see the
    cleaned metadata.

    The previous run's ``corpus_papers.bib`` is loaded once and passed
    in as ``bib_titles`` so title resolution always contends with bib —
    a clean bib title from a prior refresh can heal stuck-state titles
    that survive every other heuristic. No loop risk: the bib is read
    at the start of this wave and rewritten at the end of Wave D.
    """
    resolve_doi = True
    bib_titles = read_existing_bib_titles(ctx["paths"])
    ctx["docs"] = [
        enrich_doc_metadata(
            ctx["paths"], doc,
            resolve_doi=resolve_doi,
            doi_lookup=None,
            bib_titles=bib_titles,
        )
        for doc in ctx["docs"]
    ]


def _refresh_bibliography(ctx: dict) -> None:
    # ``ctx["docs"]`` was already enriched in the dedicated
    # ``doc_enrichment`` wave; ``write_corpus_bibliography`` re-runs the
    # enrichment defensively (idempotent for already-clean docs) so it
    # remains correct when called outside the DAG. The returned citation
    # index is stashed on ``ctx`` so the SQLite-store wave can populate
    # bib_entries.local_key.
    resolve_doi = True
    result = write_corpus_bibliography(
        ctx["paths"],
        ctx["docs"],
        resolve_doi=resolve_doi,
    )
    ctx["citation_index"] = result.get("index")


def _refresh_doc_resave(ctx: dict) -> None:
    from .pipeline import _resave_docs

    _resave_docs(ctx["paths"], ctx["docs"])


def _refresh_sqlite_store(ctx: dict) -> None:
    """Project the final docs/chunks/vectors into `wikify.db`.

    Runs after every other refresh wave so it sees the final docs,
    chunks, vectors, and citation index. Idempotent: re-running the
    DAG over the same inputs reproduces the same SQLite content. The
    embedder fingerprint comes from ``current_backend()`` (the env-var
    driven backend that ``_embed_chunks_incremental`` actually used)
    so the very first ingest can write ``embedding_spaces`` even
    though no prior space row exists to read back from.
    """
    from wikify.corpus.store.sync import write_corpus
    from wikify.corpus.vectors_meta import VectorsMeta

    from ..embedding import current_backend

    paths = ctx["paths"]
    store = ctx.get("store")
    meta: VectorsMeta | None = None
    if store is not None:
        be = current_backend()
        dim = (
            int(store.matrix.shape[1])
            if getattr(store, "matrix", None) is not None and store.matrix.size
            else int(be.get("dim") or 0)
        )
        meta = VectorsMeta(
            backend=str(be["backend"]),
            dim=dim,
            model=be.get("model"),  # type: ignore[arg-type]
        )
    write_corpus(
        paths,
        ctx["docs"],
        ctx["chunks"],
        store,
        meta,
        citation_index=ctx.get("citation_index"),
    )


# ---------------------------------------------------------------------------
# DAG declaration
# ---------------------------------------------------------------------------


REFRESH_DAG: list[Wave] = [
    # Wave A: independent steps (no citation dependency).
    # Images and equations get projected into `wikify.db` by Wave G's
    # ``project_documents``; their query-time views are derived from the
    # ``assets`` table directly, so no separate index step is needed here.
    Wave(
        label="wave A (similarity+topics)",
        steps=[
            Step("doc_similarity", _refresh_doc_similarity),
            Step("topics", _refresh_topics),
        ],
    ),
    # Wave B: heuristic enrichment (always, zero API calls except DOI negotiation)
    Wave(
        label="wave B (heuristic enrichment)",
        steps=[
            Step("cite_heuristics", _refresh_cite_heuristics),
        ],
    ),
    # Wave C: OpenAlex enrichment (optional, overwrites heuristics with authoritative data)
    Wave(
        label="wave C (openalex enrichment)",
        steps=[
            Step("openalex", _refresh_openalex),
        ],
    ),
    # Wave C': metadata enrichment (title/author/venue/DOI fallback).
    # Must run before Wave D so bibliography, doc resave, and the
    # SQLite store all see cleaned doc records.
    Wave(
        label="wave C' (doc metadata enrichment)",
        steps=[
            Step("doc_enrichment", _refresh_doc_enrichment),
        ],
    ),
    # Wave D: citation graph + bibliography (depend on enriched citations
    # and enriched doc metadata).
    Wave(
        label="wave D (edges+bibliography)",
        steps=[
            Step("citation_edges", _refresh_citation_edges),
            Step("bibliography", _refresh_bibliography),
        ],
    ),
    # Wave F: persist enriched docs and citation-derived edges.
    Wave(
        label="wave F (resave)",
        steps=[
            Step("doc_resave", _refresh_doc_resave),
        ],
    ),
    # Wave G: SQLite query store dual-write from final docs/chunks/vectors.
    Wave(
        label="wave G (sqlite store)",
        steps=[
            Step("sqlite_store", _refresh_sqlite_store),
        ],
    ),
]
