"""Build and persist the corpus-level explorer index.

Everything in ``build_explorer_index`` is a pure function of the corpus
(docs, chunks, graph, vectors). Extracted verbatim from
``distill/pipeline.py::_build_sampler_state`` so distill iterations can
load it instead of rebuilding from scratch.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from ..models import Chunk, Document

# Section types that carry no extractable knowledge — skip them at explorer
# index build time so the global samplers never dispatch them. Mirrors the
# in-memory fallback path in ``distill/pipeline.py``. Hard-coded here
# (instead of imported from ``distill.extract.dossier``) to keep ingest
# free of distill-side dependencies.
_SKIP_SECTION_TYPES: frozenset[str] = frozenset(
    {"references", "acknowledgments", "appendix"}
)


def build_explorer_index(
    docs: list[Document],
    chunks: list[Chunk],
    graph,
    vectors,  # noqa: ARG001  # reserved for future vector-side fields
) -> dict:
    """Compute the pure corpus-only explorer state as a serialisable dict.

    Returns a dict with keys:
        version, chunks_by_doc, chunk_to_doc, abstract_chunk_by_doc,
        neighbors_by_chunk, chunk_degree, caption_chunk_ids,
        content_chunk_ids, doc_ids_sorted
    """
    chunks_by_doc: dict[str, list[str]] = defaultdict(list)
    abstract_by_doc: dict[str, str] = {}
    chunk_to_doc: dict[str, str] = {}
    caption_chunk_ids: list[str] = []
    content_chunk_ids: list[str] = []

    for c in chunks:
        # Skip references / acknowledgments / appendix chunks: they carry
        # no extractable knowledge and the in-memory fallback path skips
        # them too. Without this filter the index path would dispatch
        # citation entries and ack paragraphs to the extractor and waste
        # budget on bibliography prose.
        if c.section_type in _SKIP_SECTION_TYPES:
            continue
        chunks_by_doc[c.doc_id].append(c.id)
        chunk_to_doc[c.id] = c.doc_id
        # First chunk per doc is the abstract proxy. The previous version
        # checked ``c.id not in abstract_by_doc`` but the dict is keyed by
        # ``doc_id`` (not chunk id), so the typo made every chunk
        # overwrite the entry — the saved abstract was actually the LAST
        # chunk of the doc (often references). Sampler global-jump
        # strategies dispatch the abstract chunk first via
        # ``_doc_chunks_or_empty`` and were silently leading with refs.
        if c.doc_id not in abstract_by_doc:
            abstract_by_doc[c.doc_id] = c.id
        sp = list(c.section_path or [])
        if sp and sp[0] == "__image__":
            caption_chunk_ids.append(c.id)
        else:
            content_chunk_ids.append(c.id)

    # Restrict the neighbour graph to chunks the sampler will actually
    # see (post-skip). Edges that touch a skipped chunk are dropped — we
    # don't want similar_strong neighbours pointing into a references
    # chunk that the sampler can never dispatch.
    kept_set: set[str] = set(chunk_to_doc.keys())
    neighbours: dict[str, set[str]] = defaultdict(set)
    for a, b in graph.edges.get("similar_strong", []):
        if a not in kept_set or b not in kept_set:
            continue
        neighbours[a].add(b)
        neighbours[b].add(a)
    for a, b in graph.edges.get("co_section", []):
        if a not in kept_set or b not in kept_set:
            continue
        neighbours[a].add(b)
        neighbours[b].add(a)

    all_chunk_ids = sorted(kept_set)
    neighbour_map: dict[str, list[str]] = {
        cid: sorted(neighbours[cid]) for cid in all_chunk_ids if cid in neighbours
    }
    chunk_degree: dict[str, int] = {
        cid: len(neighbour_map.get(cid, [])) for cid in all_chunk_ids
    }

    doc_ids_sorted = sorted(chunks_by_doc.keys())

    return {
        "version": 1,
        "chunks_by_doc": dict(chunks_by_doc),
        "chunk_to_doc": chunk_to_doc,
        "abstract_chunk_by_doc": abstract_by_doc,
        "neighbors_by_chunk": neighbour_map,
        "chunk_degree": chunk_degree,
        "caption_chunk_ids": caption_chunk_ids,
        "content_chunk_ids": content_chunk_ids,
        "doc_ids_sorted": doc_ids_sorted,
    }


def save_explorer_index(path: Path, index: dict) -> None:
    """Persist the explorer index as JSON."""
    path.write_text(json.dumps(index), encoding="utf-8")


def load_explorer_index(path: Path) -> dict | None:
    """Load the explorer index from disk, or return None if absent/unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[explorer_index] failed to load {path}: {exc}\n")
        return None
