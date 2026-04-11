"""Build and persist the corpus-level sampler index.

Everything in ``build_sampler_index`` is a pure function of the corpus
(docs, chunks, graph, vectors). Extracted verbatim from
``distill/pipeline.py::_build_sampler_state`` so distill iterations can
load it instead of rebuilding from scratch.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from ..models import Chunk, Document


def build_sampler_index(
    docs: list[Document],
    chunks: list[Chunk],
    graph,
    vectors,  # noqa: ARG001  # reserved for future vector-side fields
) -> dict:
    """Compute the pure corpus-only sampler state as a serialisable dict.

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
        chunks_by_doc[c.doc_id].append(c.id)
        chunk_to_doc[c.id] = c.doc_id
        # abstract proxy: mirrors the original _build_sampler_state logic which
        # assigns every chunk (condition always true), so the last chunk wins.
        abstract_by_doc[c.doc_id] = c.id
        sp = list(c.section_path or [])
        if sp and sp[0] == "__image__":
            caption_chunk_ids.append(c.id)
        else:
            content_chunk_ids.append(c.id)

    neighbours: dict[str, set[str]] = defaultdict(set)
    for a, b in graph.edges.get("similar_strong", []):
        neighbours[a].add(b)
        neighbours[b].add(a)
    for a, b in graph.edges.get("co_section", []):
        neighbours[a].add(b)
        neighbours[b].add(a)

    all_chunk_ids = [c.id for c in chunks]
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


def save_sampler_index(path: Path, index: dict) -> None:
    """Persist the sampler index as JSON."""
    path.write_text(json.dumps(index), encoding="utf-8")


def load_sampler_index(path: Path) -> dict | None:
    """Load the sampler index from disk, or return None if absent/unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[sampler_index] failed to load {path}: {exc}\n")
        return None
