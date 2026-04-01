"""Pre-computed artifacts cached at ingest time for fast generation.

These artifacts depend only on corpus state (papers, chunks, embeddings)
and are deterministic for a given corpus. They are recomputed during
`run_batch_steps` and cached to disk. The fast_generate pipeline loads
them instead of recomputing, reducing the 35s pre-compute to ~5s.

Cache location: data/cache/precomputed/
Invalidation: cleared by run_batch_steps before recomputing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from scholarforge.config import settings

logger = logging.getLogger(__name__)

_CACHE_DIR = settings.data_dir / "cache" / "precomputed"


def _ensure_cache_dir() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def clear_cache() -> None:
    """Clear all pre-computed caches. Called at start of run_batch_steps."""
    import shutil

    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR)
    _ensure_cache_dir()
    logger.info("Pre-computed cache cleared")


# ── Paper vibe vectors ───────────────────────────────────────────────────────


def cache_vibe_vectors(vibes: dict[str, list[float]]) -> None:
    """Save paper vibe vectors to cache."""
    path = _ensure_cache_dir() / "vibes.json"
    path.write_text(json.dumps(vibes), encoding="utf-8")


def load_vibe_vectors() -> dict[str, list[float]] | None:
    """Load cached vibe vectors, or None if not cached."""
    path = _CACHE_DIR / "vibes.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── KMeans cluster centroids ─────────────────────────────────────────────────


def cache_kmeans(centroids: np.ndarray, labels: np.ndarray) -> None:
    """Save KMeans centroids and labels."""
    d = _ensure_cache_dir()
    np.save(d / "kmeans_centroids.npy", centroids)
    np.save(d / "kmeans_labels.npy", labels)


def load_kmeans() -> tuple[np.ndarray, np.ndarray] | None:
    """Load cached KMeans centroids and labels."""
    c_path = _CACHE_DIR / "kmeans_centroids.npy"
    l_path = _CACHE_DIR / "kmeans_labels.npy"
    if not c_path.exists() or not l_path.exists():
        return None
    try:
        return np.load(c_path), np.load(l_path)
    except Exception:  # noqa: BLE001
        return None


# ── Graph metrics ────────────────────────────────────────────────────────────


def cache_graph_metrics(metrics_dict: dict) -> None:
    """Save serialized GraphMetrics."""
    path = _ensure_cache_dir() / "graph_metrics.json"
    path.write_text(json.dumps(metrics_dict, default=str), encoding="utf-8")


def load_graph_metrics() -> dict | None:
    """Load cached graph metrics."""
    path = _CACHE_DIR / "graph_metrics.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── Topic embeddings ─────────────────────────────────────────────────────────


def cache_topic_embeddings(topic_names: list[str], embeddings: np.ndarray) -> None:
    """Save topic name embeddings."""
    d = _ensure_cache_dir()
    (d / "topic_names.json").write_text(json.dumps(topic_names), encoding="utf-8")
    np.save(d / "topic_embeddings.npy", embeddings)


def load_topic_embeddings() -> tuple[list[str], np.ndarray] | None:
    """Load cached topic embeddings."""
    names_path = _CACHE_DIR / "topic_names.json"
    embs_path = _CACHE_DIR / "topic_embeddings.npy"
    if not names_path.exists() or not embs_path.exists():
        return None
    try:
        names = json.loads(names_path.read_text(encoding="utf-8"))
        embs = np.load(embs_path)
        return names, embs
    except Exception:  # noqa: BLE001
        return None


# ── Master precompute function ───────────────────────────────────────────────


def precompute_all() -> None:
    """Compute and cache all artifacts. Called from run_batch_steps."""
    import time

    from sklearn.cluster import KMeans

    start = time.time()
    clear_cache()

    # 1. Vibe vectors
    from scholarforge.store.embeddings import get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()
    if vibes:
        cache_vibe_vectors(vibes)
        logger.info("Cached %d vibe vectors", len(vibes))

    # 2. KMeans on chunk embeddings
    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.embeddings import get_chunk_embeddings

    chunks = load_corpus_chunks()
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)
    corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
    if len(corpus_embs) > 20:
        corpus_norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
        corpus_norms[corpus_norms == 0] = 1
        corpus_embs_normed = corpus_embs / corpus_norms

        n_clusters = min(12, len(corpus_embs_normed) // 10)
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(corpus_embs_normed)
        cache_kmeans(kmeans.cluster_centers_, labels)
        logger.info("Cached KMeans: %d clusters, %d chunks", n_clusters, len(labels))

    # 3. Graph metrics
    from scholarforge.graph.metrics import compute_metrics

    metrics = compute_metrics()
    cache_graph_metrics(
        {
            "pagerank": metrics.pagerank,
            "pagerank_mixed": metrics.pagerank_mixed,
            "degree_centrality": metrics.degree_centrality,
            "betweenness_centrality": metrics.betweenness_centrality,
            "hub_papers": metrics.hub_papers,
            "bridge_papers": metrics.bridge_papers,
            "peripheral_papers": metrics.peripheral_papers,
        }
    )
    logger.info("Cached graph metrics")

    # 4. Topic embeddings
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import _store
    from scholarforge.store.models import PaperTopic

    with get_session() as session:
        all_topics = session.exec(select(PaperTopic)).all()

    # Normalize topics
    topic_papers: dict[str, set[str]] = {}
    for t in all_topics:
        if not (3 <= len(t.topic) <= 60) or "<" in t.topic:
            continue
        key = t.topic.strip().lower()
        if key.endswith("ies") and len(key) > 5:
            key = key[:-3] + "y"
        elif (
            key.endswith("s")
            and not key.endswith("ss")
            and not key.endswith("us")
            and len(key) > 4
        ):
            key = key[:-1]
        display = key.title() if len(key) > 4 else key.upper()
        topic_papers.setdefault(display, set()).add(t.paper_id)

    sig_topics = sorted(t for t, p in topic_papers.items() if len(p) >= 5)
    if sig_topics:
        model = _store.model
        t_embs = model.encode(sig_topics, show_progress_bar=False, batch_size=64)
        cache_topic_embeddings(sig_topics, t_embs)
        logger.info("Cached %d topic embeddings", len(sig_topics))

    elapsed = time.time() - start
    logger.info("Precompute_all completed in %.1fs", elapsed)
