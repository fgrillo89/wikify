"""Resolve citation markers in chunk text to citation metadata.

Bridges inline references like ``[1-3]`` to CitationEntry objects from
the document's reference list, and identifies which cited works are also
in the corpus (enabling chunk retrieval from cited papers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import CitationEntry

if TYPE_CHECKING:
    from ..models import Chunk, Document

_MARKER_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")


@dataclass
class ResolvedRef:
    """A citation marker resolved to its metadata and corpus link."""

    entry: CitationEntry
    in_corpus: bool = False
    corpus_doc_id: str = ""


class RefLookup:
    """Resolve [N] markers to citation metadata and corpus chunks.

    Built once per distill session from the loaded corpus data.
    """

    def __init__(
        self,
        docs: list[Document],
        chunks: list[Chunk] | None = None,
        vector_store: object | None = None,
    ) -> None:
        # Build doc_id -> {ord -> CitationEntry}
        # Also detect whether ordinals are 0-based or 1-based per doc.
        self._ord_maps: dict[str, dict[int, CitationEntry]] = {}
        self._ord_base: dict[str, int] = {}  # doc_id -> min ordinal (0 or 1)
        for doc in docs:
            om: dict[int, CitationEntry] = {}
            for cit in doc.citations:
                om[cit.ord] = cit
            self._ord_maps[doc.id] = om
            if om:
                self._ord_base[doc.id] = min(om.keys())

        # Build DOI -> corpus doc_id
        self._doi_to_doc: dict[str, str] = {}
        for doc in docs:
            doi = (doc.metadata.get("doi") or "").lower().strip()
            if doi:
                self._doi_to_doc[doi] = doc.id

        # Build title prefix -> corpus doc_id (for non-DOI matching)
        self._title_to_doc: dict[str, str] = {}
        for doc in docs:
            if doc.title and len(doc.title) > 15:
                self._title_to_doc[doc.title.lower()[:50]] = doc.id

        # Build corpus doc_id -> cites set (from doc.cites)
        self._doc_cites: dict[str, set[str]] = {}
        for doc in docs:
            if doc.cites:
                self._doc_cites[doc.id] = set(doc.cites)

        # Chunk index for corpus-chunk retrieval
        self._chunks_by_doc: dict[str, list[Chunk]] = {}
        if chunks:
            for ck in chunks:
                self._chunks_by_doc.setdefault(ck.doc_id, []).append(ck)

        self._vector_store = vector_store

    def parse_markers(self, text: str) -> list[int]:
        """Parse citation markers like [1-3], [4,5] from text."""
        nums: list[int] = []
        for m in _MARKER_RE.finditer(text):
            for part in m.group(1).split(","):
                part = part.strip()
                if "-" in part:
                    a, b = part.split("-", 1)
                    try:
                        nums.extend(range(int(a.strip()), int(b.strip()) + 1))
                    except ValueError:
                        pass
                else:
                    try:
                        nums.append(int(part))
                    except ValueError:
                        pass
        return sorted(set(nums))

    def resolve_markers(
        self, chunk_text: str, doc_id: str
    ) -> list[ResolvedRef]:
        """Parse [N] markers from text and resolve to citation entries.

        Tries both 0-based and 1-based ordinal matching.
        """
        ords = self.parse_markers(chunk_text)
        if not ords:
            return []

        ord_map = self._ord_maps.get(doc_id, {})
        if not ord_map:
            return []

        cited_corpus_ids = self._doc_cites.get(doc_id, set())
        results: list[ResolvedRef] = []

        # Markers are 1-based ([1] = first ref). Ordinals may be 0-based
        # (ord=0 = first ref) or 1-based depending on extract_citations.
        # Compute the offset: if min ordinal is 0, subtract 1 from marker.
        base = self._ord_base.get(doc_id, 0)
        offset = 0 if base >= 1 else -1  # 0-based ords need [N] -> N-1

        for n in ords:
            entry = ord_map.get(n + offset)
            if not entry:
                # Try the other convention as fallback
                entry = ord_map.get(n) or ord_map.get(n - 1)
            if not entry:
                continue

            in_corpus, corpus_doc_id = self._check_in_corpus(
                entry, cited_corpus_ids
            )
            results.append(ResolvedRef(
                entry=entry,
                in_corpus=in_corpus,
                corpus_doc_id=corpus_doc_id,
            ))

        return results

    def _check_in_corpus(
        self, entry: CitationEntry, cited_corpus_ids: set[str]
    ) -> tuple[bool, str]:
        """Check if a citation entry corresponds to a corpus paper."""
        # Strategy 1: DOI match
        if entry.doi:
            corpus_id = self._doi_to_doc.get(entry.doi.lower().strip())
            if corpus_id:
                return True, corpus_id

        # Strategy 2: title prefix match against known corpus cites
        if entry.title and len(entry.title) > 15:
            prefix = entry.title.lower()[:50]
            corpus_id = self._title_to_doc.get(prefix)
            if corpus_id and corpus_id in cited_corpus_ids:
                return True, corpus_id

        # Strategy 3: fuzzy match author+year against doc.cites
        # (doc.cites was computed by ingest's fuzzy matching)
        # This is already captured by cited_corpus_ids

        return False, ""

    def find_corpus_chunks(
        self,
        corpus_doc_id: str,
        concept_query: str,
        *,
        top_k: int = 3,
    ) -> list[Chunk]:
        """Find chunks from a corpus paper relevant to a concept.

        Uses keyword overlap as a baseline. If a vector store is
        available, uses similarity search instead.
        """
        doc_chunks = self._chunks_by_doc.get(corpus_doc_id, [])
        if not doc_chunks:
            return []

        # TODO: use vector_store.query() when available for true
        # similarity search. For now, keyword overlap.
        query_words = set(
            w.lower() for w in concept_query.split() if len(w) > 4
        )
        if not query_words:
            return doc_chunks[:top_k]

        scored: list[tuple[int, Chunk]] = []
        for ck in doc_chunks:
            ck_words = set(
                w.lower() for w in ck.text[:300].split() if len(w) > 4
            )
            overlap = len(query_words & ck_words)
            if overlap > 0:
                scored.append((overlap, ck))

        scored.sort(key=lambda x: -x[0])
        return [ck for _, ck in scored[:top_k]]
