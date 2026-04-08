"""Metric functions over wiki bundles.

One pure function per metric. Each takes a Bundle (and, where needed, a
small "what does the corpus look like" handle and/or an injected
embedding callable). No metric mutates anything. No metric calls an LLM.

The injected callables let the harness stay free of vector-store and
NER dependencies; the caller passes the right ones in.

See ../metrics.md for the formal definitions.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from .bundle import Bundle, Page

# A callable that turns a list of strings into an (n, d) numpy array of
# unit-norm embeddings. The harness does not own embedding; the caller
# (typically wired to the same model used by the vector store) provides it.
Embedder = Callable[[Sequence[str]], np.ndarray]


# =========================================================================
# M1 — corpus coverage residual F = mean_c (1 - max_p cos(embed(c), embed(body(p))))
# =========================================================================


def coverage_residual(
    bundle: Bundle,
    chunk_embeddings: np.ndarray,  # (n_chunks, d), unit-norm
    embed: Embedder,
) -> float:
    """M1. Mean residual distance from each corpus chunk to its nearest
    wiki page body.

    Lower is better. Range [0, 2]; in practice ~[0.3, 0.9] on real data.
    Returns 1.0 (max meaningful distance) if the bundle has no pages.
    """
    if not bundle.pages:
        return 1.0
    from ..store.bundle_embeddings import load_or_compute

    _ids, page_embeds = load_or_compute(bundle, bundle.pages, embed)
    # cos similarity = X @ Y.T because both are unit-norm
    sims = chunk_embeddings @ page_embeds.T  # (n_chunks, n_pages)
    nearest = sims.max(axis=1)  # (n_chunks,)
    return float((1.0 - nearest).mean())


# =========================================================================
# M2 — Heaps exponent over a series of bundles at increasing cost
# =========================================================================


@dataclass
class HeapsFit:
    costs: list[float]  # haiku-equivalent tokens per bundle
    n_concepts: list[int]  # concept count per bundle
    beta_global: float  # exponent fit over the whole trajectory
    beta_window: list[float]  # sliding-window exponents
    window_size: int


def heaps_exponent(
    bundles: Iterable[Bundle],
    cost_of: Callable[[Bundle], float],
    window_size: int = 3,
) -> HeapsFit:
    """M2. Fit N(C) ~ a * C^beta over a series of bundles ordered by cost.

    `cost_of` extracts the cost (in haiku-equivalent tokens) for a bundle;
    typically `lambda b: b.run_meta["cost_haiku_eq"]`.
    """
    items = sorted(bundles, key=cost_of)
    costs = [cost_of(b) for b in items]
    counts = [len(b.concepts) for b in items]

    def _fit(cs: list[float], ns: list[int]) -> float:
        if len(cs) < 2:
            return float("nan")
        xs = np.log(np.asarray(cs, dtype=float))
        ys = np.log(np.asarray(ns, dtype=float).clip(min=1))
        slope, _ = np.polyfit(xs, ys, 1)
        return float(slope)

    beta_global = _fit(costs, counts)
    beta_window: list[float] = []
    for i in range(len(items) - window_size + 1):
        beta_window.append(_fit(costs[i : i + window_size], counts[i : i + window_size]))

    return HeapsFit(
        costs=costs,
        n_concepts=counts,
        beta_global=beta_global,
        beta_window=beta_window,
        window_size=window_size,
    )


# =========================================================================
# M3 — graph crystallinity on G_evidence (doc-level cosine, top-k=10)
# =========================================================================


def _build_g_evidence(bundle: Bundle, top_k: int = 10) -> tuple[list[Page], np.ndarray]:
    """Build the doc-level evidence cosine adjacency, sparsified to top-k
    per node (union, not mutual).

    Returns (pages, dense weighted adjacency W of shape (n, n)).
    """
    pages = bundle.pages
    n = len(pages)
    if n == 0:
        return pages, np.zeros((0, 0), dtype=float)

    # binary doc-membership matrix M of shape (n, n_docs)
    doc_index: dict[str, int] = {}
    rows: list[list[int]] = [[] for _ in range(n)]
    for i, p in enumerate(pages):
        for ev in p.evidence:
            j = doc_index.setdefault(ev.doc_id, len(doc_index))
            rows[i].append(j)
    n_docs = len(doc_index)
    M = np.zeros((n, n_docs), dtype=float)
    for i, js in enumerate(rows):
        for j in js:
            M[i, j] = 1.0

    norms = np.linalg.norm(M, axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    Mn = M / safe[:, None]
    W_full = Mn @ Mn.T  # cosine weights
    np.fill_diagonal(W_full, 0.0)
    # zero out rows/cols of pages with no evidence (norm was 0)
    W_full[norms == 0, :] = 0.0
    W_full[:, norms == 0] = 0.0

    # top-k per row (union sparsification)
    if n <= top_k + 1:
        keep = W_full > 0
    else:
        keep = np.zeros_like(W_full, dtype=bool)
        for i in range(n):
            order = np.argsort(-W_full[i])
            keep[i, order[:top_k]] = True
        keep = keep | keep.T  # union -> undirected

    W = np.where(keep, W_full, 0.0)
    return pages, W


def _modularity(W: np.ndarray, communities: list[list[int]]) -> float:
    m = W.sum() / 2.0
    if m == 0:
        return 0.0
    deg = W.sum(axis=1)
    Q = 0.0
    for comm in communities:
        idx = np.asarray(comm, dtype=int)
        if idx.size == 0:
            continue
        sub = W[np.ix_(idx, idx)]
        deg_sum = deg[idx].sum()
        Q += sub.sum() / (2.0 * m) - (deg_sum / (2.0 * m)) ** 2
    return float(Q)


def _greedy_communities(W: np.ndarray) -> list[list[int]]:
    """Tiny deterministic greedy modularity-merge community detector.

    Adequate for the small wiki graphs we deal with (10^2 - 10^3 nodes).
    For larger graphs, swap in networkx / igraph.
    """
    n = W.shape[0]
    if n == 0:
        return []
    communities: list[set[int]] = [{i} for i in range(n) if W[i].sum() > 0 or n == 1]
    if not communities:
        return [[i] for i in range(n)]
    changed = True
    while changed and len(communities) > 1:
        changed = False
        best_gain = 0.0
        best_pair: tuple[int, int] | None = None
        base = _modularity(W, [sorted(c) for c in communities])
        for i in range(len(communities)):
            for j in range(i + 1, len(communities)):
                merged = (
                    communities[:i]
                    + communities[i + 1 : j]
                    + communities[j + 1 :]
                    + [communities[i] | communities[j]]
                )
                q = _modularity(W, [sorted(c) for c in merged])
                gain = q - base
                if gain > best_gain:
                    best_gain = gain
                    best_pair = (i, j)
        if best_pair is not None:
            i, j = best_pair
            communities = (
                communities[:i]
                + communities[i + 1 : j]
                + communities[j + 1 :]
                + [communities[i] | communities[j]]
            )
            changed = True
    # add isolated nodes back as singletons
    covered = set().union(*communities) if communities else set()
    for k in range(n):
        if k not in covered:
            communities.append({k})
    return [sorted(c) for c in communities]


def spectral_gap_modularity(bundle: Bundle, top_k: int = 10) -> dict[str, float]:
    """M3. Returns {'modularity': Q, 'spectral_gap': Δλ, 'n_nodes': n,
    'n_edges': e}.

    Modularity is computed on the greedy partition of G_evidence; spectral
    gap is the difference between the third- and second-smallest
    eigenvalues of the normalised Laplacian (so it is the gap *above* the
    first non-trivial eigenvalue, the standard "how cleanly does the graph
    split into a small number of communities" reading).
    """
    pages, W = _build_g_evidence(bundle, top_k=top_k)
    n = W.shape[0]
    if n < 2 or W.sum() == 0:
        return {"modularity": 0.0, "spectral_gap": 0.0, "n_nodes": float(n), "n_edges": 0.0}

    comms = _greedy_communities(W)
    Q = _modularity(W, comms)

    # normalised Laplacian: L = I - D^{-1/2} W D^{-1/2}
    deg = W.sum(axis=1)
    safe = np.where(deg > 0, deg, 1.0)
    d_inv_sqrt = 1.0 / np.sqrt(safe)
    L = np.eye(n) - (W * d_inv_sqrt[None, :]) * d_inv_sqrt[:, None]
    eigs = np.sort(np.linalg.eigvalsh(L))
    # eigs[0] ~ 0; gap above the first non-trivial eigenvalue:
    gap = float(eigs[2] - eigs[1]) if n >= 3 else 0.0

    return {
        "modularity": float(Q),
        "spectral_gap": gap,
        "n_nodes": float(n),
        "n_edges": float(int((W > 0).sum() // 2)),
    }


# =========================================================================
# M3b — g_links modularity (diagnostic overlay on the explicit links field)
# =========================================================================


def g_links_modularity(bundle: Bundle) -> dict:
    """Modularity + spectral gap on the page-link graph.

    Builds an undirected adjacency from each page's ``links`` list
    (symmetrised: an edge exists if either direction does) and runs the
    same greedy modularity + normalised-Laplacian pipeline as
    ``spectral_gap_modularity``. Diagnostic overlay: paired with M3 it
    shows how much of the crystallinity comes from explicit cross-links
    vs shared evidence.
    """
    pages = bundle.pages
    n = len(pages)
    if n < 2:
        return {"modularity": 0.0, "spectral_gap": 0.0, "n_nodes": float(n), "n_edges": 0.0}
    id_to_idx = {p.id: i for i, p in enumerate(pages)}
    W = np.zeros((n, n), dtype=float)
    for i, p in enumerate(pages):
        for link in getattr(p, "links", []) or []:
            j = id_to_idx.get(link)
            if j is None or j == i:
                continue
            W[i, j] = 1.0
            W[j, i] = 1.0
    if W.sum() == 0:
        return {"modularity": 0.0, "spectral_gap": 0.0, "n_nodes": float(n), "n_edges": 0.0}
    comms = _greedy_communities(W)
    Q = _modularity(W, comms)
    deg = W.sum(axis=1)
    safe = np.where(deg > 0, deg, 1.0)
    d_inv_sqrt = 1.0 / np.sqrt(safe)
    L = np.eye(n) - (W * d_inv_sqrt[None, :]) * d_inv_sqrt[:, None]
    eigs = np.sort(np.linalg.eigvalsh(L))
    gap = float(eigs[2] - eigs[1]) if n >= 3 else 0.0
    return {
        "modularity": float(Q),
        "spectral_gap": gap,
        "n_nodes": float(n),
        "n_edges": float(int((W > 0).sum() // 2)),
    }


# =========================================================================
# M5 — hit rate
# =========================================================================


def hit_rate(bundle: Bundle) -> float:
    """M5. |chunks that became evidence in any page| / |chunks read by any
    model during the run|.

    Reads `bundle.run_meta['chunks_read']` (a list of chunk ids). Returns
    NaN if the run did not record reads.
    """
    chunks_read = bundle.run_meta.get("chunks_read")
    if not chunks_read:
        return float("nan")
    used = {ev.chunk_id for p in bundle.pages for ev in p.evidence}
    read = set(chunks_read)
    if not read:
        return float("nan")
    return len(used & read) / len(read)


# =========================================================================
# M6 — grounding gate
# =========================================================================

# A factual sentence is any non-empty line of body prose that is not a
# heading, list bullet, code fence, or evidence footnote line. Crude on
# purpose; the gate threshold tolerates the noise.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MARKER_RE = re.compile(r"\[\^([^\]]+)\]")


def _factual_sentences(body: str) -> list[str]:
    out: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("#", "-", "*", ">", "```", "|")):
            continue
        if line.startswith("[^"):
            continue
        for sent in _SENT_SPLIT.split(line):
            s = sent.strip()
            if len(s) > 20:  # ignore tiny fragments
                out.append(s)
    return out


@dataclass
class GroundingResult:
    g1_anchoring: float  # fraction of factual sentences with a marker
    g2_evidence_ok: float  # fraction of markers that resolve correctly
    n_sentences: int
    n_markers: int
    passes: bool  # G1 >= 0.9 AND G2 >= 0.99


def grounding(
    bundle: Bundle,
    chunk_text: Callable[[str], str | None],
    g1_threshold: float = 0.9,
    g2_threshold: float = 0.99,
) -> GroundingResult:
    """M6. Compute the grounding gate.

    `chunk_text(chunk_id)` returns the corpus chunk text or None if the
    id does not exist. The harness wires this to the corpus chunk store.
    """
    n_sent = 0
    n_anchored = 0
    n_markers = 0
    n_markers_ok = 0

    for page in bundle.pages:
        ev_by_marker = {ev.marker: ev for ev in page.evidence}
        for sent in _factual_sentences(page.body_clean):
            n_sent += 1
            markers = _MARKER_RE.findall(sent)
            if markers:
                n_anchored += 1
            for marker in markers:
                n_markers += 1
                ev = ev_by_marker.get(marker)
                if ev is None:
                    continue
                text = chunk_text(ev.chunk_id)
                if text is None:
                    continue
                if _normalize(ev.quote) in _normalize(text):
                    n_markers_ok += 1

    g1 = n_anchored / n_sent if n_sent else 1.0
    g2 = n_markers_ok / n_markers if n_markers else 1.0
    return GroundingResult(
        g1_anchoring=g1,
        g2_evidence_ok=g2,
        n_sentences=n_sent,
        n_markers=n_markers,
        passes=(g1 >= g1_threshold and g2 >= g2_threshold),
    )


# =========================================================================
# GT-P — person recall against bibliography metadata
# =========================================================================

_NAME_PUNCT = re.compile(r"[^\w\s]")


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = _NAME_PUNCT.sub(" ", s).lower()
    return " ".join(s.split())


def normalize_person(name: str) -> str:
    """Deterministic name fold: 'Akira Fujishima' -> 'fujishima_a'.

    Handles:
      - 'Last, First M.'      -> 'last_f'
      - 'First M. Last'       -> 'last_f'
      - 'F. Last'             -> 'last_f'
      - single token (rare)   -> 'lasttoken'
    """
    s = _normalize(name)
    if not s:
        return ""
    if "," in name:
        last, _, rest = name.partition(",")
        last = _normalize(last)
        first = _normalize(rest)
    else:
        parts = s.split()
        last = parts[-1]
        first = parts[0] if len(parts) > 1 else ""
    initial = first[:1] if first else ""
    return f"{last}_{initial}" if initial else last


def person_recall(bundle: Bundle, author_names: Iterable[str]) -> float:
    """GT-P. |canonical authors with a matching person page| / |canonical authors|.

    `author_names` is the union of bibliography author strings across the
    corpus. Canonicalisation is done here.
    """
    canonical = {normalize_person(n) for n in author_names if n}
    canonical.discard("")
    if not canonical:
        return float("nan")
    page_keys: set[str] = set()
    for p in bundle.people:
        page_keys.add(normalize_person(p.title))
        for a in p.aliases:
            page_keys.add(normalize_person(a))
    return len(canonical & page_keys) / len(canonical)


# =========================================================================
# GT-C — concept recall against the cleaned ingest topic vocabulary
# =========================================================================


def concept_recall(
    bundle: Bundle,
    topic_phrases: Sequence[str],
    topic_embeddings: np.ndarray,  # (n_topics, d), unit-norm
    embed: Embedder,
    cosine_threshold: float = 0.78,
) -> float:
    """GT-C. |topics with a matching concept page| / |topics|.

    Match if the topic equals (post-normalisation) a page title or alias,
    OR if cos(embed(topic), embed(page.body_clean)) >= cosine_threshold for
    some concept page.

    `topic_phrases` and `topic_embeddings` are produced once per corpus by
    pulling the cleaned vocabulary from the ingest DB and embedding it.
    """
    if len(topic_phrases) == 0:
        return float("nan")
    concepts = bundle.concepts
    if not concepts:
        return 0.0

    # exact-ish match by normalised title/aliases
    title_keys: set[str] = set()
    for p in concepts:
        title_keys.add(_normalize(p.title))
        for a in p.aliases:
            title_keys.add(_normalize(a))

    matched = np.zeros(len(topic_phrases), dtype=bool)
    norm_topics = [_normalize(t) for t in topic_phrases]
    for i, t in enumerate(norm_topics):
        if t in title_keys:
            matched[i] = True

    # embedding match for the rest
    remaining = np.where(~matched)[0]
    if remaining.size > 0:
        page_embeds = embed([p.body_clean for p in concepts])
        sims = topic_embeddings[remaining] @ page_embeds.T  # (r, n_pages)
        best = sims.max(axis=1)
        matched[remaining] = best >= cosine_threshold

    return float(matched.mean())
