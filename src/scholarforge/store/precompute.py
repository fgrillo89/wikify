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


# ── Science vibes ────────────────────────────────────────────────────────────


def cache_science_vibes(vibes: dict[str, list[float]]) -> None:
    path = _ensure_cache_dir() / "science_vibes.json"
    path.write_text(json.dumps(vibes), encoding="utf-8")


def load_science_vibes() -> dict[str, list[float]] | None:
    path = _CACHE_DIR / "science_vibes.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── Boilerplate chunk IDs ────────────────────────────────────────────────────


def cache_boilerplate_ids(chunk_ids: set[str]) -> None:
    path = _ensure_cache_dir() / "boilerplate_ids.json"
    path.write_text(json.dumps(sorted(chunk_ids)), encoding="utf-8")


def load_boilerplate_ids() -> set[str] | None:
    path = _CACHE_DIR / "boilerplate_ids.json"
    if not path.exists():
        return None
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


# ── Divergent gap pairs ──────────────────────────────────────────────────────


def cache_divergent_gaps(gaps: list[dict]) -> None:
    path = _ensure_cache_dir() / "divergent_gaps.json"
    path.write_text(json.dumps(gaps, default=str), encoding="utf-8")


def load_divergent_gaps() -> list[dict] | None:
    path = _CACHE_DIR / "divergent_gaps.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── Concept links ────────────────────────────────────────────────────────────


def cache_concept_links(links: list[dict]) -> None:
    path = _ensure_cache_dir() / "concept_links.json"
    path.write_text(json.dumps(links, default=str), encoding="utf-8")


def load_concept_links() -> list[dict] | None:
    path = _CACHE_DIR / "concept_links.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── Compute helpers ──────────────────────────────────────────────────────────


def _compute_boilerplate_ids() -> set[str]:
    """Find chunks that appear nearly identically in 5+ papers.

    Uses ChromaDB k-NN: if a chunk's 10 nearest neighbors span 5+
    distinct papers with similarity > 0.9, it's boilerplate.
    """
    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.embeddings import _store, get_chunk_embeddings

    chunks = load_corpus_chunks()
    if not chunks:
        return set()

    # Build chunk_id -> paper_id mapping
    chunk_paper = {c.id: c.paper_id for c in chunks}

    # Get all embeddings
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    boilerplate: set[str] = set()
    collection = _store.chunk_collection

    # Process in batches
    batch_size = 200
    for i in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[i : i + batch_size]
        batch_embs = [stored[cid] for cid in batch_ids if cid in stored]
        if not batch_embs:
            continue

        results = collection.query(
            query_embeddings=batch_embs,
            n_results=10,
            include=["distances"],
        )

        for j, cid in enumerate(batch_ids):
            if cid not in stored or j >= len(results["ids"]):
                continue
            neighbor_ids = results["ids"][j]
            distances = results["distances"][j]

            # Count distinct papers among close neighbors (distance < 0.1 = sim > 0.9)
            close_papers: set[str] = set()
            for nid, dist in zip(neighbor_ids, distances):
                if dist < 0.1 and nid != cid and nid in chunk_paper:
                    close_papers.add(chunk_paper[nid])

            if len(close_papers) >= 5:
                boilerplate.add(cid)

    logger.info("Found %d boilerplate chunks out of %d", len(boilerplate), len(all_ids))
    return boilerplate


def _compute_divergent_gaps(
    science_vibes: dict[str, list[float]],
    boilerplate_ids: set[str],
) -> list[dict]:
    """Find papers that share citation context but diverge in conclusions.

    High coupling + high conclusion distance = real research gap.
    """
    from sqlmodel import select

    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import get_chunk_embeddings
    from scholarforge.store.models import Paper
    from scholarforge.vault.coupler import compute_coupling

    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    paper_ids = list(papers.keys())

    # Get coupling with lower threshold
    coupling_map = compute_coupling(paper_ids, min_strength=2)

    # Get conclusion chunks per paper (excluding boilerplate)
    chunks = load_corpus_chunks()
    conclusion_types = {"conclusion", "discussion"}
    paper_conclusion_chunks: dict[str, list] = {}
    for c in chunks:
        if c.section_type in conclusion_types and c.id not in boilerplate_ids:
            paper_conclusion_chunks.setdefault(c.paper_id, []).append(c)

    # Fetch conclusion chunk embeddings
    conc_ids = [c.id for cs in paper_conclusion_chunks.values() for c in cs]
    stored = get_chunk_embeddings(conc_ids)

    # Compute mean conclusion embedding per paper
    paper_conc_embs: dict[str, np.ndarray] = {}
    for pid, conc_chunks in paper_conclusion_chunks.items():
        embs = [stored[c.id] for c in conc_chunks if c.id in stored]
        if embs:
            mean_emb = np.mean(embs, axis=0)
            norm = np.linalg.norm(mean_emb)
            if norm > 0:
                mean_emb = mean_emb / norm
            paper_conc_embs[pid] = mean_emb

    # Find coupled-but-divergent pairs
    gaps = []
    seen_pairs: set[tuple[str, str]] = set()

    for pid_a, coupled_ids in coupling_map.items():
        for pid_b in coupled_ids:
            pair = tuple(sorted([pid_a, pid_b]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            if pid_a not in paper_conc_embs or pid_b not in paper_conc_embs:
                continue

            # Conclusion distance
            sim = float(np.dot(paper_conc_embs[pid_a], paper_conc_embs[pid_b]))
            conc_distance = 1.0 - sim

            if conc_distance < 0.3:
                continue  # too similar — not a real gap

            # Count shared references (coupling strength)
            # Both papers appear in each other's coupling list
            strength = sum(1 for cid in coupling_map.get(pid_a, []) if cid == pid_b) + sum(
                1 for cid in coupling_map.get(pid_b, []) if cid == pid_a
            )
            # Minimum coupling is already enforced by compute_coupling
            strength = max(strength, 2)

            pa = papers.get(pid_a)
            pb = papers.get(pid_b)
            if not pa or not pb:
                continue

            gaps.append(
                {
                    "paper_a": pa.display_name(),
                    "paper_b": pb.display_name(),
                    "coupling_strength": strength,
                    "conclusion_distance": round(conc_distance, 3),
                    "rationale": (
                        f"Both cite shared references but conclusions diverge "
                        f"(distance={conc_distance:.2f}). "
                        f"No third paper reconciles this."
                    ),
                }
            )

    # Sort by coupling * distance (most interesting first)
    gaps.sort(key=lambda g: g["coupling_strength"] * g["conclusion_distance"], reverse=True)
    logger.info("Found %d divergent gap pairs", len(gaps))
    return gaps[:20]


def _compute_concept_links_v2(
    science_vibes: dict[str, list[float]],
    boilerplate_ids: set[str],
) -> list[dict]:
    """Find papers sharing results/discussion content (excluding boilerplate).

    Uses science vibes for pair selection, section-filtered chunks for
    matching, and IDF-weighted token overlap for labeling.
    """
    import re
    from collections import Counter

    from sqlmodel import select

    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import get_chunk_embeddings
    from scholarforge.store.models import Paper

    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    science_types = {"results", "discussion", "conclusion", "body"}
    chunks = load_corpus_chunks()

    # Filter to science chunks, excluding boilerplate + short/copyright chunks
    _boilerplate_patterns = {"creative commons", "no competing", "declare no", "©", "license"}
    science_chunks = [
        c
        for c in chunks
        if c.section_type in science_types
        and c.id not in boilerplate_ids
        and c.token_count > 50  # skip very short chunks
        and not any(bp in c.content.lower()[:200] for bp in _boilerplate_patterns)
    ]

    # Group by paper
    paper_sci_chunks: dict[str, list] = {}
    for c in science_chunks:
        paper_sci_chunks.setdefault(c.paper_id, []).append(c)

    # Fetch embeddings
    sci_ids = [c.id for c in science_chunks]
    stored = get_chunk_embeddings(sci_ids)

    # Build simple IDF for token labeling
    stopwords = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "they",
        "their",
        "can",
        "may",
        "will",
        "would",
        "could",
        "should",
        "not",
        "no",
        "also",
        "than",
        "more",
        "most",
        "such",
        "as",
        "which",
        "where",
        "when",
        "how",
        "what",
        "who",
        "each",
        "all",
        "both",
        "between",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "into",
        "over",
        "under",
        "using",
        "based",
        "due",
        "however",
        "while",
        "since",
        "because",
        "although",
        "respectively",
        "figure",
        "fig",
        "table",
        # Copyright/license boilerplate
        "anonymous",
        "peer",
        "creativecommons",
        "licenses",
        "visit",
        "permissions",
        "copyright",
        "license",
        "licensed",
        "creative",
        "commons",
        "attribution",
        "article",
        "journal",
        "published",
        "publisher",
        "springer",
        "wiley",
        "elsevier",
        "ieee",
        "doi",
        "https",
        "http",
        "www",
        "org",
        "com",
        "author",
        "authors",
        "declare",
        "competing",
        "interest",
        "interests",
        "financial",
        "conflict",
        "acknowledgments",
        "funding",
        "grant",
        "supported",
    }
    doc_freq: Counter = Counter()
    for pid, p_chunks in paper_sci_chunks.items():
        paper_tokens = set()
        for c in p_chunks:
            words = re.findall(r"[a-z]{3,}", c.content.lower())
            paper_tokens.update(w for w in words if w not in stopwords)
        for token in paper_tokens:
            doc_freq[token] += 1

    total_papers = len(paper_sci_chunks)

    # Paper pair selection using science vibes
    pids = list(science_vibes.keys())
    vibe_matrix = np.array([science_vibes[pid] for pid in pids])
    paper_sims = vibe_matrix @ vibe_matrix.T

    links = []
    pairs_checked = 0
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            sim = float(paper_sims[i, j])
            if not (0.65 <= sim <= 0.90):
                continue
            pairs_checked += 1
            if pairs_checked > 300:
                break

            pid_a, pid_b = pids[i], pids[j]
            ca = paper_sci_chunks.get(pid_a, [])
            cb = paper_sci_chunks.get(pid_b, [])
            if not ca or not cb:
                continue

            embs_a = np.array([stored[c.id] for c in ca if c.id in stored])
            embs_b = np.array([stored[c.id] for c in cb if c.id in stored])
            if len(embs_a) == 0 or len(embs_b) == 0:
                continue

            na = np.linalg.norm(embs_a, axis=1, keepdims=True)
            na[na == 0] = 1
            embs_a = embs_a / na
            nb = np.linalg.norm(embs_b, axis=1, keepdims=True)
            nb[nb == 0] = 1
            embs_b = embs_b / nb

            chunk_sim = embs_a @ embs_b.T
            bi, bj = np.unravel_index(np.argmax(chunk_sim), chunk_sim.shape)
            best_sim = float(chunk_sim[bi, bj])

            if best_sim < 0.75:
                continue

            # Build IDF-weighted shared label from the two matching chunks
            chunk_a_text = ca[bi].content.lower()
            chunk_b_text = cb[bj].content.lower()
            tokens_a = set(re.findall(r"[a-z]{3,}", chunk_a_text)) - stopwords
            tokens_b = set(re.findall(r"[a-z]{3,}", chunk_b_text)) - stopwords
            shared = tokens_a & tokens_b

            if not shared:
                continue

            # Rank by IDF (rarer words are more specific)
            import math

            idf_scored = [
                (token, math.log(total_papers / max(doc_freq[token], 1))) for token in shared
            ]
            idf_scored.sort(key=lambda x: x[1], reverse=True)
            label = " ".join(token for token, _ in idf_scored[:5])

            pa = papers.get(pid_a)
            pb = papers.get(pid_b)
            if not pa or not pb:
                continue

            links.append(
                {
                    "paper_a": pa.display_name(),
                    "paper_b": pb.display_name(),
                    "chunk_sim": round(best_sim, 3),
                    "shared_label": label,
                    "section_a": ca[bi].section_path or ca[bi].section_type,
                    "section_b": cb[bj].section_path or cb[bj].section_type,
                }
            )

        if pairs_checked > 300:
            break

    links.sort(key=lambda lnk: lnk["chunk_sim"], reverse=True)
    logger.info("Found %d concept links", len(links))
    return links[:30]


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
            key.endswith("s") and not key.endswith("ss") and not key.endswith("us") and len(key) > 4
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

    # 5. Science vibes (results/discussion/conclusion only)
    from scholarforge.store.embeddings import get_science_vibe_vectors

    science_vibes = get_science_vibe_vectors()
    if science_vibes:
        cache_science_vibes(science_vibes)
        logger.info("Cached %d science vibe vectors", len(science_vibes))

    # 6. Boilerplate chunk IDs
    boilerplate_ids = _compute_boilerplate_ids()
    cache_boilerplate_ids(boilerplate_ids)
    logger.info("Cached %d boilerplate IDs", len(boilerplate_ids))

    # 7. Divergent gap pairs
    try:
        gaps = _compute_divergent_gaps(science_vibes, boilerplate_ids)
        cache_divergent_gaps(gaps)
        logger.info("Cached %d divergent gap pairs", len(gaps))
    except Exception as exc:
        logger.warning("Divergent gap computation failed: %s", exc)

    # 8. Concept links (section-filtered)
    try:
        links = _compute_concept_links_v2(science_vibes, boilerplate_ids)
        cache_concept_links(links)
        logger.info("Cached %d concept links", len(links))
    except Exception as exc:
        logger.warning("Concept link computation failed: %s", exc)

    elapsed = time.time() - start
    logger.info("Precompute_all completed in %.1fs", elapsed)
