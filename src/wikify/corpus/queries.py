"""Read-only corpus query helpers — the surface ``cli/corpus.py`` calls.

Wraps the existing fluent KG (``corpus/graph.py``), the seed selector
(``corpus/seed.py``), and the on-disk corpus loaders (``corpus/chunks.py``)
into one cohesive module that the CLI can drive without sprinkling
imports across handlers.

Handle grammar (used by ``corpus show <handle>``)::

    doc:<doc_id>
    chunk:<chunk_id>
"""

from __future__ import annotations

from ..api import Corpus
from ..models import Chunk, Document
from .chunks import (
    all_chunks,
    list_documents,
    read_chunks,
    read_knowledge_graph,
    read_vector_store,
)

# ---------------------------------------------------------------- listing


def list_doc_ids(corpus: Corpus) -> list[str]:
    """Return every document id in the corpus, sorted."""
    return sorted(d.id for d in list_documents(corpus))


def list_chunks_for_doc(corpus: Corpus, doc_id: str) -> list[Chunk]:
    """Return the chunks for one document, in original order."""
    return read_chunks(corpus, doc_id)


def list_files(corpus: Corpus) -> list[str]:
    """Return on-disk filenames under the corpus root, relative to root."""
    out: list[str] = []
    for p in sorted(corpus.root.rglob("*")):
        if p.is_file():
            out.append(str(p.relative_to(corpus.root)))
    return out


# ------------------------------------------------------------------- show


def get_doc(corpus: Corpus, doc_id: str) -> Document | None:
    """Return the ``Document`` record for *doc_id* or ``None``."""
    for d in list_documents(corpus):
        if d.id == doc_id:
            return d
    return None


def get_chunk(corpus: Corpus, chunk_id: str) -> Chunk | None:
    """Return the ``Chunk`` for *chunk_id* (full corpus scan)."""
    for c in all_chunks(corpus):
        if c.id == chunk_id:
            return c
    return None


def parse_handle(handle: str) -> tuple[str, str]:
    """Split a ``kind:id`` handle. Raise ``ValueError`` if malformed."""
    if ":" not in handle:
        raise ValueError(
            f"handle must be 'kind:id' (e.g. 'doc:paper_A' or "
            f"'chunk:paper_A__c0001'); got {handle!r}"
        )
    kind, _, ident = handle.partition(":")
    return kind, ident


# ------------------------------------------------------------------- find


def search_chunks(corpus: Corpus, query: str, *, top_k: int = 8) -> list[dict]:
    """Semantic search over chunk embeddings; returns ranked chunk dicts.

    Each result has ``id``, ``score``, and ``doc_id`` (so the agent can
    follow up with ``corpus show chunk:<id>`` or ``corpus show
    doc:<doc_id>``).
    """
    vs = read_vector_store(corpus)
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for

    meta = read_meta(corpus.vectors_path)
    embed = (
        embedder_for(meta.backend, meta.model, mode="query") if meta else None
    )
    kg = read_knowledge_graph(corpus, vectors=vs, embed_fn=embed)
    return list(kg.chunks().search(query, top_k=top_k))


def search_text(corpus: Corpus, needle: str, *, top_k: int = 50) -> list[dict]:
    """Literal substring grep over chunk text. Cheap, no embedding load."""
    needle_lower = needle.lower()
    out: list[dict] = []
    for c in all_chunks(corpus):
        if needle_lower in c.text.lower():
            out.append({"id": c.id, "doc_id": c.doc_id, "preview": c.text[:160]})
            if len(out) >= top_k:
                break
    return out


def find_seeds(
    corpus: Corpus,
    *,
    max_seeds: int,
    pagerank_weight: float,
) -> list[str]:
    """Return the greedy-submodular seed document ids.

    Both knobs are caller-supplied; the CLI surface defines the
    user-facing defaults (``corpus find --seed --max <n>
    --pagerank-weight <w>``). No defaults live here so callers cannot
    silently rely on a hidden policy.
    """
    from .seed import doc_embeddings, greedy_seed_select, pagerank_normalised

    chunks = all_chunks(corpus)
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    embeds, doc_order = doc_embeddings(chunks, vs)
    pr_norm = pagerank_normalised(kg, doc_order)
    return list(
        greedy_seed_select(
            doc_order=doc_order,
            doc_embeddings=embeds,
            pr_norm=pr_norm,
            max_seeds=max_seeds,
            pagerank_weight=pagerank_weight,
        )
    )


# ------------------------------------------------------------------- check


def check_corpus(corpus: Corpus) -> dict:
    """Lightweight corpus health summary used by ``corpus check``."""
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    out: dict = {
        "root": str(corpus.root),
        "n_docs": len(docs),
        "n_chunks": len(chunks),
        "has_vectors": corpus.vectors_path.exists(),
        "has_knowledge_graph": corpus.knowledge_graph_path.exists(),
        "has_manifest": corpus.manifest_path.exists(),
    }
    try:
        from .field_detect import detect_field, detect_field_scores

        out["field"] = detect_field(corpus)
        out["field_scores"] = detect_field_scores(corpus)[:5]
    except Exception as exc:
        out["field"] = None
        out["field_error"] = str(exc)
    return out


# --------------------------------------------------------- evidence helper


def select_evidence_chunks(
    corpus: Corpus,
    *,
    page_title: str,
    top_k: int = 8,
    max_per_source: int = 2,
) -> list[str]:
    """Per-page evidence helper. Returns a list of chunk ids."""
    vs = read_vector_store(corpus)
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for

    meta = read_meta(corpus.vectors_path)
    embed = (
        embedder_for(meta.backend, meta.model, mode="query") if meta else None
    )
    kg = read_knowledge_graph(corpus, vectors=vs, embed_fn=embed)
    seen_per_source: dict[str, int] = {}
    out: list[str] = []
    hits = kg.chunks().search(page_title, top_k=top_k * 4)
    for h in hits:
        src = h.get("source_id") or h.get("doc_id") or ""
        if seen_per_source.get(src, 0) >= max_per_source:
            continue
        out.append(h["id"])
        seen_per_source[src] = seen_per_source.get(src, 0) + 1
        if len(out) >= top_k:
            break
    return out
