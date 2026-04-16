"""Pre-load all heavy corpus state once so multiple iterations can reuse it.

The corpus files (chunks, vectors, knowledge graph) are immutable between
ingest runs. ``preload_corpus`` reads them all once and returns a
``PreloadedCorpus`` that the pipeline can accept directly via
``run_with_preloaded``.
"""

from dataclasses import dataclass

from ..embedding import embedder_for
from ..models import Chunk, Document
from ..paths import CorpusPaths
from ..store.bibliography import load_citation_index
from ..store.corpus import (
    all_chunks,
    list_documents,
    read_knowledge_graph,
    read_vector_store,
)
from ..store.equations_index import EquationIndex
from ..store.images_index import ImageIndex


@dataclass
class PreloadedCorpus:
    """All corpus state that is expensive to load and immutable between ingests."""

    corpus_paths: CorpusPaths
    docs: list[Document]
    docs_by_id: dict[str, Document]
    chunks: list[Chunk]
    chunks_by_id: dict[str, Chunk]
    images_index: ImageIndex
    equations_index: EquationIndex
    vectors: object  # VectorStore
    knowledge_graph: object  # KnowledgeGraph
    persona_text: str  # contents of corpus/persona.txt, or "" if absent
    citation_index: dict


def preload_corpus(corpus: CorpusPaths) -> PreloadedCorpus:
    """Load the corpus once. Returns a ``PreloadedCorpus`` for repeated use."""
    docs = list_documents(corpus)
    docs_by_id: dict[str, Document] = {d.id: d for d in docs}
    chunks = all_chunks(corpus)
    chunks_by_id: dict[str, Chunk] = {c.id: c for c in chunks}
    images_index = ImageIndex.load(corpus)
    equations_index = EquationIndex.load(corpus.equations_index_path)
    vectors = read_vector_store(corpus)
    # Resolve the embedder so KG vector search (search_chunks, similar_to)
    # works during guided-mode tool-calling. Without this, search() returns [].
    from ..store.vectors_meta import read_meta

    vmeta = read_meta(corpus.vectors_path)
    # KG search uses query-mode embedding so task prefixes match how the
    # user's question should be encoded against passage-embedded chunks.
    embed_fn = (
        embedder_for(vmeta.backend, vmeta.model, mode="query") if vmeta else None
    )
    knowledge_graph = read_knowledge_graph(corpus, vectors=vectors, embed_fn=embed_fn)
    citation_index = load_citation_index(corpus)
    persona_text = ""
    if corpus.persona_path.exists():
        persona_text = corpus.persona_path.read_text(encoding="utf-8").strip()
    return PreloadedCorpus(
        corpus_paths=corpus,
        docs=docs,
        docs_by_id=docs_by_id,
        chunks=chunks,
        chunks_by_id=chunks_by_id,
        images_index=images_index,
        equations_index=equations_index,
        vectors=vectors,
        knowledge_graph=knowledge_graph,
        persona_text=persona_text,
        citation_index=citation_index,
    )
