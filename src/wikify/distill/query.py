"""Query mode: retrieve evidence from a wiki bundle and synthesise an answer.

Pure Python retrieval (no model calls). Uses ``WikiIndex`` as the primary
read surface; falls back to page-body embeddings for phrases the alias
map misses. Bundle is never mutated by a query call.

Helpers ``read_wiki_page`` and ``read_corpus_chunks`` are pure Python
functions usable by the query handler skill without a model call.
Query results are optionally persisted to ``<bundle>/_meta/query_log/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..ingest.topics import _PHRASE_RE
from ..paths import BundlePaths, CorpusPaths
from ..prompts import load_prompt
from ..schema import (
    EscalationEvent,
    QueryAnswer,
    QueryEvidence,
    QueryLogEntry,
    QueryRequest,
)
from ..store.wiki_index import WikiIndex
from ..types import ModelTier, Querier

if TYPE_CHECKING:
    from ..store.wiki_graph import WikiKnowledgeGraph
from ..config import BODY_EXCERPT_CHARS, MAX_CANDIDATES

QUERY_PROMPT = load_prompt("wikify/query").name
_STOP = frozenset(
    {
        "what",
        "is",
        "the",
        "of",
        "a",
        "an",
        "and",
        "or",
        "to",
        "for",
        "in",
        "on",
        "with",
        "how",
        "why",
        "which",
        "that",
        "this",
        "are",
        "be",
        "was",
        "were",
        "has",
        "have",
        "do",
        "does",
        "can",
        "could",
        "should",
        "would",
        "will",
        "about",
    }
)


def _load_wiki_graph(
    bundle: BundlePaths,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
) -> WikiKnowledgeGraph | None:
    """Load the wiki knowledge graph from the bundle, or None if absent."""
    if not bundle.graph_path.exists():
        return None
    from ..store.vectors import load_vectors
    from ..store.wiki_graph import load_wiki_graph

    vectors = None
    if bundle.wiki_vectors_path.exists():
        vectors = load_vectors(bundle.wiki_vectors_path)
    return load_wiki_graph(bundle.graph_path, vectors=vectors, embed_fn=embed_fn)


def read_wiki_page(bundle: BundlePaths, page_id: str) -> str | None:
    """Return the full markdown body of *page_id* from the bundle, or None.

    Pure Python. No model calls. The bundle is never mutated.
    """
    index = WikiIndex.load(bundle)
    entry = index.get(page_id)
    if entry is None:
        return None
    page_path = bundle.root / entry.path
    if not page_path.exists():
        return None
    return page_path.read_text(encoding="utf-8")


def read_corpus_chunks(corpus: CorpusPaths, chunk_ids: list[str]) -> list[dict]:
    """Return chunk dicts for *chunk_ids* from the corpus chunks directory.

    Each result is ``{id, doc_id, text}``. Missing chunk ids are silently
    skipped. Pure Python. No model calls. Capped at 5 chunks per call to
    keep token spend predictable.
    """
    from ..store.corpus import read_chunks_by_id

    chunks = read_chunks_by_id(corpus, chunk_ids, limit=5)
    return [
        {"id": c.id, "doc_id": c.doc_id, "text": c.text}
        for c in chunks
    ]


def persist_query_log(
    bundle: BundlePaths,
    *,
    question: str,
    answer: QueryAnswer,
    pages_touched: list[str],
    links_followed: list[str] | None = None,
    escalation_events: list[EscalationEvent] | None = None,
    model_id: str = "",
    tier: ModelTier | str = "",
) -> str:
    """Write one QueryLogEntry to ``<bundle>/_meta/query_log/<id>.json``.

    Returns the entry id. Uses atomic rename so a partial write is never
    visible. The log directory is created on demand.
    """
    entry_id = uuid.uuid4().hex
    asked_at = datetime.now(timezone.utc).isoformat()
    entry = QueryLogEntry(
        id=entry_id,
        question=question,
        asked_at=asked_at,
        answer_text=answer.text,
        pages_touched=pages_touched,
        links_followed=links_followed or [],
        escalation_events=escalation_events or [],
        model_id=model_id,
        tier=tier,
    )
    log_dir = bundle.query_log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / f"{entry_id}.json"
    fd, tmp = tempfile.mkstemp(prefix=".ql-", dir=str(log_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(entry.model_dump_json(indent=2))
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return entry_id


def run(
    *,
    bundle: BundlePaths,
    corpus,  # CorpusPaths, unused today but accepted for future wiring
    question: str,
    querier: Querier,
    embed: Callable[[Sequence[str]], np.ndarray],
    model_id: str = ModelTier.MEDIUM.value,
    tier: ModelTier | str = ModelTier.MEDIUM,
    cache_root: Path | None = None,
    save_log: bool = True,
) -> QueryAnswer:
    index = WikiIndex.load(bundle)

    # cache key = sha256(question + sha256(_index.json bytes) + QUERY_PROMPT)
    index_path = bundle.root / "_index.json"
    index_bytes = index_path.read_bytes() if index_path.exists() else b""
    index_hash = hashlib.sha256(index_bytes).hexdigest()
    cache_key = hashlib.sha256((question + index_hash + QUERY_PROMPT).encode("utf-8")).hexdigest()
    cache_root = cache_root or Path("data/cache/query")
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_file = cache_root / f"{cache_key}.json"
    if cache_file.exists():
        try:
            d = json.loads(cache_file.read_text(encoding="utf-8"))
            return QueryAnswer(
                text=d["text"],
                citations=list(d["citations"]),
                chunks=list(d["chunks"]),
                follow_ups=list(d.get("follow_ups", [])),
            )
        except Exception:
            pass

    # 1. Tokenise question into noun-phrase-ish candidates.
    lower = question.lower()
    phrases: list[str] = []
    seen: set[str] = set()
    for m in _PHRASE_RE.finditer(lower):
        p = " ".join(m.group(1).split())
        words = p.split()
        if all(w in _STOP for w in words):
            continue
        if p in seen:
            continue
        seen.add(p)
        phrases.append(p)
    # also single tokens as fallback
    for tok in lower.split():
        tok = "".join(c for c in tok if c.isalnum() or c == "-")
        if len(tok) < 3 or tok in _STOP or tok in seen:
            continue
        seen.add(tok)
        phrases.append(tok)

    # 2. Resolve aliases (cheap deterministic pass).
    roots: list[str] = []
    missed: list[str] = []
    for p in phrases:
        hit = index.resolve_alias(p)
        if hit and hit not in roots:
            roots.append(hit)
        else:
            missed.append(p)

    # 3. Embedding fallback for missed phrases via wiki graph vector search.
    from ..store.wiki_bundle import parse_page

    if missed and len(index) > 0:
        wkg = _load_wiki_graph(bundle, embed)
        if wkg is not None:
            query_text = " ".join(missed)
            hits = wkg.search(query_text, top_k=5)
            for h in hits:
                pid = h["id"]
                if pid not in roots:
                    roots.append(pid)

    # 4. Expand.
    candidates: list[str] = list(roots)
    for rid in roots:
        entry = index.get(rid)
        if entry is None:
            continue
        for doc_id in entry.doc_ids:
            for pid in index.pages_for_doc(doc_id):
                if pid not in candidates:
                    candidates.append(pid)
        for link in entry.links:
            if link in index and link not in candidates:
                candidates.append(link)
        if len(candidates) >= MAX_CANDIDATES:
            break
    candidates = candidates[:MAX_CANDIDATES]

    # 5. Build evidence.
    evidence: list[QueryEvidence] = []
    for pid in candidates:
        entry = index.get(pid)
        if entry is None:
            continue
        page_path = bundle.root / entry.path
        if not page_path.exists():
            continue
        try:
            page = parse_page(page_path)
        except Exception:
            continue
        evidence.append(
            QueryEvidence(
                page_id=pid,
                page_title=entry.title,
                body_excerpt=(page.body_clean or "")[:BODY_EXCERPT_CHARS],
                citations=list(entry.links),
            )
        )

    # 6. Follow-ups: links adjacent to candidates, ranked by frequency.
    cand_set = set(candidates)
    follow_counts: Counter[str] = Counter()
    for pid in candidates:
        entry = index.get(pid)
        if entry is None:
            continue
        for link in entry.links:
            if link in cand_set:
                continue
            if link in index:
                follow_counts[link] += 1
    follow_ups = [pid for pid, _ in follow_counts.most_common(3)]

    # 7. Call the querier.
    req = QueryRequest(
        question=question,
        evidence=evidence,
        prompt_template=QUERY_PROMPT,
        model_id=model_id,
        tier=tier,
    )
    resp = querier.answer(req)
    answer = QueryAnswer(
        text=resp.answer.text,
        citations=resp.answer.citations or [ev.page_id for ev in evidence],
        chunks=resp.answer.chunks,
        follow_ups=resp.answer.follow_ups or follow_ups,
    )

    # atomic write of cache.
    try:
        fd, tmp = tempfile.mkstemp(prefix=".q-", dir=str(cache_root))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "text": answer.text,
                    "citations": list(answer.citations),
                    "chunks": list(answer.chunks),
                    "follow_ups": list(answer.follow_ups),
                },
                f,
            )
        os.replace(tmp, cache_file)
    except Exception:
        pass

    if save_log:
        try:
            persist_query_log(
                bundle,
                question=question,
                answer=answer,
                pages_touched=list(candidates),
                model_id=model_id,
                tier=tier,
            )
        except Exception:
            pass  # log persistence must never break the answer path

    return answer


