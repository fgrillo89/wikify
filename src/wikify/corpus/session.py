"""Long-lived corpus query session used by interactive CLI surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from ..api import Corpus
from ..models import Chunk, Document
from . import queries
from .chunks import (
    all_chunks,
    list_documents,
    read_knowledge_graph,
    read_vector_store,
)


@dataclass
class CorpusSearchSession:
    """Reusable in-process corpus search context.

    Direct CLI commands are one-shot and reload Python state every time.
    This session keeps lightweight doc/chunk indexes warm immediately and
    loads vector/model/graph resources lazily on first semantic search.
    """

    corpus: Corpus

    def __post_init__(self) -> None:
        self._docs: list[Document] = list_documents(self.corpus)
        self._chunks: list[Chunk] = all_chunks(self.corpus)
        self._doc_by_id = {doc.id: doc for doc in self._docs}
        self._chunk_by_id = {chunk.id: chunk for chunk in self._chunks}
        self._vectors = None
        self._embed = None
        self._kg = None

    @property
    def n_docs(self) -> int:
        return len(self._docs)

    @property
    def n_chunks(self) -> int:
        return len(self._chunks)

    def list_docs(self) -> list[str]:
        return sorted(self._doc_by_id)

    def list_chunks(self, doc_id: str) -> list[str]:
        return [chunk.id for chunk in self._chunks if chunk.doc_id == doc_id]

    def get_doc(self, doc_id: str) -> Document | None:
        return self._doc_by_id.get(doc_id)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._chunk_by_id.get(chunk_id)

    def search_text(self, needle: str, *, top_k: int) -> list[dict]:
        needle_lower = needle.lower()
        out: list[dict] = []
        for chunk in self._chunks:
            if needle_lower in chunk.text.lower():
                out.append(
                    {
                        "id": chunk.id,
                        "doc_id": chunk.doc_id,
                        "preview": chunk.text[:160],
                    }
                )
                if len(out) >= top_k:
                    break
        return out

    def search_semantic(self, query: str, *, top_k: int) -> list[dict]:
        kg = self._semantic_kg()
        return list(kg.chunks().search(query, top_k=top_k))

    def search_docs_semantic(
        self,
        query: str,
        *,
        top_k: int,
        chunk_pool: int | None = None,
    ) -> list[dict]:
        """Return papers ranked by their best semantic chunk hit."""
        pool = chunk_pool or max(top_k * 5, top_k)
        return self._rank_docs_from_hits(self.search_semantic(query, top_k=pool), top_k)

    def search_docs_text(
        self,
        needle: str,
        *,
        top_k: int,
        chunk_pool: int | None = None,
    ) -> list[dict]:
        """Return papers ranked by count of literal chunk matches."""
        pool = chunk_pool or max(top_k * 5, top_k)
        return self._rank_docs_from_hits(self.search_text(needle, top_k=pool), top_k)

    def sample_docs(
        self,
        *,
        max_docs: int,
        strategy: str = "diverse",
        pagerank_weight: float = 0.7,
    ) -> list[str]:
        return queries.sample_docs(
            self.corpus,
            max_docs=max_docs,
            strategy=strategy,
            pagerank_weight=pagerank_weight,
        )

    def _semantic_kg(self):
        if self._kg is not None:
            return self._kg
        from ..corpus.vectors_meta import read_meta
        from ..embedding import embedder_for

        self._vectors = read_vector_store(self.corpus)
        meta = read_meta(self.corpus.vectors_path)
        self._embed = (
            embedder_for(meta.backend, meta.model, mode="query") if meta else None
        )
        self._kg = read_knowledge_graph(
            self.corpus,
            vectors=self._vectors,
            embed_fn=self._embed,
        )
        return self._kg

    def _rank_docs_from_hits(self, hits: list[dict], top_k: int) -> list[dict]:
        grouped: dict[str, dict] = {}
        for hit in hits:
            doc_id = str(hit.get("doc_id") or hit.get("source_id") or "")
            chunk_id = str(hit.get("id") or "")
            if not doc_id:
                continue
            score = float(hit.get("score", 0.0) or 0.0)
            entry = grouped.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "title": self._doc_by_id.get(doc_id).title
                    if doc_id in self._doc_by_id
                    else "",
                    "best_score": score,
                    "n_chunks": 0,
                    "best_chunk_id": chunk_id,
                    "chunk_ids": [],
                },
            )
            entry["n_chunks"] += 1
            entry["chunk_ids"].append(chunk_id)
            if score > float(entry["best_score"]):
                entry["best_score"] = score
                entry["best_chunk_id"] = chunk_id
        return sorted(
            grouped.values(),
            key=lambda item: (
                -float(item["best_score"]),
                -int(item["n_chunks"]),
                str(item["doc_id"]),
            ),
        )[:top_k]


__all__ = ["CorpusSearchSession"]
