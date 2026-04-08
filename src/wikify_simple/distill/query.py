"""Query mode: retrieve evidence from a wiki bundle and synthesise an answer.

Pure Python retrieval (no model calls). Uses ``WikiIndex`` as the primary
read surface; falls back to page-body embeddings for phrases the alias
map misses. Bundle is never mutated by a query call.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from ..agents.protocols import Querier
from ..agents.schema import QueryAnswer, QueryEvidence, QueryRequest
from ..ingest.topics import _PHRASE_RE
from ..paths import BundlePaths
from ..store.bundle_embeddings import load_or_compute
from ..store.wiki_index import WikiIndex

QUERY_PROMPT = "wikify_simple/query/v1"
_MAX_CANDIDATES = 12
_BODY_EXCERPT_CHARS = 600
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


def run(
    *,
    bundle: BundlePaths,
    corpus,  # CorpusPaths, unused today but accepted for future wiring
    question: str,
    querier: Querier,
    embed: Callable[[Sequence[str]], np.ndarray],
    model_id: str = "haiku",
    tier: str = "exploit",
    cache_root: Path | None = None,
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

    # 3. Embedding fallback for missed phrases. Loads pages via _parse_page
    #    and caches page-body embeddings beside the bundle index.
    if missed and len(index) > 0:
        from ..eval.bundle import _parse_page

        pages_for_embed: list = []
        ordered_ids: list[str] = []
        for entry in index:
            page_path = bundle.root / entry.path
            if not page_path.exists():
                continue
            try:
                pages_for_embed.append(_parse_page(page_path))
                ordered_ids.append(entry.id)
            except Exception:
                continue
        if pages_for_embed:
            # Cache page embeddings under the query cache dir so the bundle
            # stays untouched (mtime invariance is part of the query contract).
            embed_cache_dir = cache_root / "embeddings" / bundle.root.name
            embed_cache_dir.mkdir(parents=True, exist_ok=True)
            _ids, page_mat = load_or_compute(
                _FakeBundleForEmbed(embed_cache_dir),
                pages_for_embed,
                embed,
            )
            q_mat = embed(missed)
            sims = q_mat @ page_mat.T
            for row in sims:
                order = np.argsort(-row)
                for j in order[:3]:
                    pid = ordered_ids[int(j)]
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
        if len(candidates) >= _MAX_CANDIDATES:
            break
    candidates = candidates[:_MAX_CANDIDATES]

    # 5. Build evidence.
    from ..eval.bundle import _parse_page

    evidence: list[QueryEvidence] = []
    for pid in candidates:
        entry = index.get(pid)
        if entry is None:
            continue
        page_path = bundle.root / entry.path
        if not page_path.exists():
            continue
        try:
            page = _parse_page(page_path)
        except Exception:
            continue
        evidence.append(
            QueryEvidence(
                page_id=pid,
                page_title=entry.title,
                body_excerpt=(page.body_clean or "")[:_BODY_EXCERPT_CHARS],
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

    return answer


class _FakeBundleForEmbed:
    """Shim so ``load_or_compute`` sees a ``.root`` attribute.

    ``load_or_compute`` only reads ``bundle.root`` for the cache path; the
    pages iterable is passed separately. We want the cache file under the
    wiki bundle so it stays beside ``_index.json``.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
