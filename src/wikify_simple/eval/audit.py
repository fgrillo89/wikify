"""Per-bundle ``_audit.md`` generator (graphify item 6e).

Reads a loaded ``Bundle`` plus the metrics dict produced by the eval CLI
and emits a human-readable audit report listing the most useful operator
hooks: hub pages by g_evidence degree, top communities, low-confidence
claims, and overall bundle/grounding stats. The audit is purely
descriptive — no thresholds, no pass/fail.

The audit reads ``WikiPage`` confidence info from the per-page sidecar
``.provenance.json`` files written by ``store/wiki_files.write_page``.
Pages without a sidecar are treated as fully confident (score 1.0,
label "extracted").
"""

from pathlib import Path
from statistics import mean, median

import numpy as np

from .bundle import Bundle, Page
from .community import louvain_communities
from .metrics import _build_g_evidence

_AMBIGUOUS_LABEL = "ambiguous"
_LOW_SCORE_THRESHOLD = 0.5
_TOP_K = 10


def write_audit(bundle: Bundle, metrics: dict, *, out_path: Path | None = None) -> Path:
    """Render and write ``<bundle>/_audit.md``. Returns the written path.

    ``metrics`` is the same dict the eval CLI builds (M1/M3/M5/M6 etc.).
    Only the M3 g_evidence and g_links modularity numbers are read; the
    rest are passed through into the overall stats section.
    """
    target = out_path or (bundle.root / "_audit.md")
    sections: list[str] = []

    sections.append(f"# Audit — {bundle.name}\n")
    sections.append(_section_overall(bundle, metrics))
    sections.append(_section_hubs(bundle))
    sections.append(_section_communities(bundle))
    sections.append(_section_low_confidence(bundle))

    target.write_text("\n".join(sections), encoding="utf-8")
    return target


# --- sections ------------------------------------------------------------


def _section_overall(bundle: Bundle, metrics: dict) -> str:
    pages = bundle.pages
    n_pages = len(pages)
    written = sum(1 for p in pages if p.body_clean.strip())
    n_evidence = sum(len(p.evidence) for p in pages)
    confidences: list[float] = []
    for p in pages:
        for s in (p.provenance or {}).get("confidence_scores") or []:
            if isinstance(s, dict) and isinstance(s.get("score"), (int, float)):
                confidences.append(float(s["score"]))
    mean_c = mean(confidences) if confidences else float("nan")
    median_c = median(confidences) if confidences else float("nan")
    g_evid_q = float(metrics.get("M3_g_evidence", {}).get("modularity", 0.0))
    g_links_q = float(metrics.get("M3_g_links", {}).get("modularity", 0.0))
    lines = [
        "## Overall",
        "",
        f"- pages: {n_pages}",
        f"- pages with prose: {written}  ({(written / n_pages * 100) if n_pages else 0:.1f}%)",
        f"- evidence entries: {n_evidence}",
        f"- mean confidence: {mean_c:.3g}",
        f"- median confidence: {median_c:.3g}",
        f"- g_evidence Q: {g_evid_q:.4g}",
        f"- g_links Q:    {g_links_q:.4g}",
        f"- Q gap (g_evidence - g_links): {g_evid_q - g_links_q:+.4g}",
        "",
    ]
    return "\n".join(lines)


def _section_hubs(bundle: Bundle) -> str:
    pages, W = _build_g_evidence(bundle)
    lines = ["## Top hub pages (g_evidence degree)", ""]
    n = W.shape[0]
    if n == 0 or W.sum() == 0:
        lines.append("_no hubs (empty g_evidence)_\n")
        return "\n".join(lines)
    deg = (W > 0).sum(axis=1)
    order = np.argsort(-deg)[:_TOP_K]
    for rank, idx in enumerate(order, 1):
        if deg[idx] == 0:
            break
        p = pages[idx]
        lines.append(f"{rank}. **{p.title}** (id=`{p.id}`) — degree {int(deg[idx])}")
    lines.append("")
    return "\n".join(lines)


def _section_communities(bundle: Bundle) -> str:
    pages, W = _build_g_evidence(bundle)
    lines = ["## Top communities (g_evidence Louvain)", ""]
    n = W.shape[0]
    if n == 0 or W.sum() == 0:
        lines.append("_no communities (empty g_evidence)_\n")
        return "\n".join(lines)
    comms = louvain_communities(W)
    sized = sorted(comms, key=len, reverse=True)[:_TOP_K]
    for rank, comm in enumerate(sized, 1):
        if not comm:
            continue
        # representative = highest-degree member
        deg = (W > 0).sum(axis=1)
        rep_idx = max(comm, key=lambda i: deg[i])
        rep = pages[rep_idx]
        lines.append(f"{rank}. size={len(comm)} — rep: **{rep.title}** (`{rep.id}`)")
    lines.append("")
    return "\n".join(lines)


def _section_low_confidence(bundle: Bundle) -> str:
    lines = ["## Low-confidence claims", ""]
    flagged: list[tuple[Page, int, str, float]] = []
    for p in bundle.pages:
        scores = (p.provenance or {}).get("confidence_scores") or []
        for i, s in enumerate(scores):
            if not isinstance(s, dict):
                continue
            label = str(s.get("label", "extracted"))
            score = float(s.get("score", 1.0))
            if label == _AMBIGUOUS_LABEL or score < _LOW_SCORE_THRESHOLD:
                flagged.append((p, i, label, score))
    if not flagged:
        lines.append("_no low-confidence evidence in this bundle_\n")
        return "\n".join(lines)
    for p, i, label, score in flagged:
        ev = p.evidence[i] if i < len(p.evidence) else None
        chunk_id = ev.chunk_id if ev else "?"
        lines.append(
            f"- **{p.title}** (`{p.id}`) ev[{i}] {label} score={score:.2f} chunk=`{chunk_id}`"
        )
    lines.append("")
    return "\n".join(lines)
