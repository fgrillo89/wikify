"""Composite maturity score for a work-folder concept.

Pure function ``compute_maturity(bundle, slug, *, kind_stencil) ->
MaturityReport``. Reads only the slug folder, the events ledger, and
the wiki.db link neighbourhood — no model calls, no embeddings, no I/O
beyond those three sources.

Score formula and gates are described in detail in
``.claude/skills/wikify/references/exploration/maturity.md``. The
``wikify-investigate`` editor reads this and promotes when
``band == "ready"``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from ...api import Bundle
from ..run.events import iter_events
from .card import load_card
from .evidence import EvidenceRecord, read_evidence
from .notebook import read_notebook

# --- detection patterns over chunk quote text -----------------------

_DEFINITION_RE = re.compile(
    r"\b(?:is|are)\s+(?:a|an|the)\b|"
    r"\b(?:refers? to|defined as|denotes|describes|"
    r"is the term|is known as|is also called|are called)\b",
    re.IGNORECASE,
)
_MECHANISM_RE = re.compile(
    r"\b(?:mechanism|process(?:es)?|involves|proceeds via|reaction|"
    r"step|catalyz|reduces|oxidizes|deposits?|cycle|"
    r"half-reaction|pulse|purge)\b",
    re.IGNORECASE,
)
_APPLICATION_RE = re.compile(
    r"\b(?:used (?:for|in|to)|applied (?:to|in|for)|application|"
    r"deployed|enables|demonstrated in|adopted|integrated into)\b",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"\b(?:limitation|drawback|challenge|cannot|fails? to|limited by|"
    r"problem|degrad\w*|trade-?off|bottleneck|constraint)\b",
    re.IGNORECASE,
)
_VARIANT_RE = re.compile(
    r"\b(?:variant|variation|type of|kind of|family|class of|"
    r"subtype|categor\w+|species of|flavour|approach|method)\b",
    re.IGNORECASE,
)
_PERSON_CONTRIBUTION_RE = re.compile(
    r"\b(?:proposed|introduced|developed|invented|discovered|"
    r"demonstrated|reported|formulated|showed|established)\b",
    re.IGNORECASE,
)
_PERSON_TEMPORAL_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PERSON_COLLAB_RE = re.compile(
    r"\b(?:with|co[-]?authored|colleagues|collaborat\w+|team|"
    r"jointly|together with)\b",
    re.IGNORECASE,
)

# --- kind stencils --------------------------------------------------

STENCILS: dict[str, set[str]] = {
    "article-method": {"definition", "mechanism", "application"},
    "article-theory": {"definition", "mechanism", "limitation"},
    "article-survey": {"definition", "variant", "application"},
    "article-history": {"definition", "variant", "limitation"},
    "person": set(),  # person rule does not use the kind set
}


# --- data containers ------------------------------------------------


@dataclass
class MaturityReport:
    slug: str
    kind: str = "article"
    kind_stencil: str = "article-method"
    score: float = 0.0
    band: str = "new"
    gates_passed: bool = False
    growth_stalled: bool = False
    last_computed_round: int = 0
    components: dict[str, float] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)
    n_chunks: int = 0
    n_docs: int = 0
    kinds_present: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "kind": self.kind,
            "kind_stencil": self.kind_stencil,
            "score": round(self.score, 4),
            "band": self.band,
            "gates_passed": self.gates_passed,
            "growth_stalled": self.growth_stalled,
            "last_computed_round": self.last_computed_round,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "gates": self.gates,
            "n_chunks": self.n_chunks,
            "n_docs": self.n_docs,
            "kinds_present": list(self.kinds_present),
        }


# --- helpers --------------------------------------------------------


def _detect_kinds(records: Iterable[EvidenceRecord]) -> set[str]:
    """Detect which content kinds the evidence covers, by regex over quotes."""
    found: set[str] = set()
    for r in records:
        q = r.quote or ""
        if not q:
            continue
        if "definition" not in found and _DEFINITION_RE.search(q):
            found.add("definition")
        if "mechanism" not in found and _MECHANISM_RE.search(q):
            found.add("mechanism")
        if "application" not in found and _APPLICATION_RE.search(q):
            found.add("application")
        if "limitation" not in found and _LIMITATION_RE.search(q):
            found.add("limitation")
        if "variant" not in found and _VARIANT_RE.search(q):
            found.add("variant")
    return found


def _diversity_bonus(records: list[EvidenceRecord]) -> float:
    """``1 - HHI(per-doc share)``. 0 when all chunks come from one doc,
    approaches 1 when spread evenly across many docs.
    """
    if not records:
        return 0.0
    counts: dict[str, int] = {}
    for r in records:
        counts[r.doc_id] = counts.get(r.doc_id, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return 0.0
    hhi = sum((c / total) ** 2 for c in counts.values())
    return max(0.0, 1.0 - hhi)


def _growth_stalled(
    bundle: Bundle, slug: str, *, current_round: int, window: int = 2
) -> bool:
    """True iff no ``evidence_added`` event for this slug in the last ``window`` rounds.

    Falls back to True when the bundle has no ``round_started`` events
    (baseline-style bundle, no investigate loop yet).
    """
    rounds_seen: list[int] = []
    last_evidence_round: int | None = None
    for ev in iter_events(bundle):
        if ev.type == "round_started":
            r = int(ev.data.get("round", 0))
            if r not in rounds_seen:
                rounds_seen.append(r)
        elif ev.type == "evidence_added" and ev.concept_id == slug:
            # Tie evidence-added events to the most recent round_started.
            if rounds_seen:
                last_evidence_round = rounds_seen[-1]
    if not rounds_seen:
        return True  # no investigate loop -> treat as stalled
    if last_evidence_round is None:
        return True
    return (current_round - last_evidence_round) >= window


def _wiki_page_id_for_slug(bundle: Bundle, slug: str) -> str | None:
    p = bundle.sqlite_path
    if not p.exists():
        return None
    con = sqlite3.connect(str(p))
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT page_id FROM wiki_pages WHERE slug = ?", (slug,)
        ).fetchone()
        return row["page_id"] if row else None
    finally:
        con.close()


def _link_neighbours_chunk_sets(
    bundle: Bundle, page_id: str
) -> list[set[str]]:
    """For each depth-1 link neighbour, the set of cited chunk_ids."""
    p = bundle.sqlite_path
    if not p.exists():
        return []
    con = sqlite3.connect(str(p))
    try:
        con.row_factory = sqlite3.Row
        out_neighbours = {
            r["dst_id"]
            for r in con.execute(
                "SELECT dst_id FROM wiki_edges "
                "WHERE src_id = ? AND kind = 'links_to' AND dst_type = 'page'",
                (page_id,),
            )
        }
        in_neighbours = {
            r["src_id"]
            for r in con.execute(
                "SELECT src_id FROM wiki_edges "
                "WHERE dst_id = ? AND kind = 'links_to' AND dst_type = 'page'",
                (page_id,),
            )
        }
        neighbour_ids = (out_neighbours | in_neighbours) - {page_id}
        if not neighbour_ids:
            return []
        out: list[set[str]] = []
        for nid in neighbour_ids:
            rows = con.execute(
                "SELECT DISTINCT chunk_id FROM wiki_evidence "
                "WHERE page_id = ? AND chunk_id IS NOT NULL",
                (nid,),
            ).fetchall()
            out.append({r["chunk_id"] for r in rows if r["chunk_id"]})
        return out
    finally:
        con.close()


def _max_chunk_jaccard(mine: set[str], neighbours: list[set[str]]) -> float:
    if not mine or not neighbours:
        return 0.0
    best = 0.0
    for other in neighbours:
        if not other:
            continue
        union = mine | other
        if not union:
            continue
        score = len(mine & other) / len(union)
        if score > best:
            best = score
    return best


def _person_components(
    bundle: Bundle, slug: str, records: list[EvidenceRecord]
) -> tuple[float, dict[str, float], dict[str, bool], list[str]]:
    n_contribution = sum(
        1 for r in records if _PERSON_CONTRIBUTION_RE.search(r.quote or "")
    )
    n_docs = len({r.doc_id for r in records})
    card = load_card(bundle, slug)
    author_alias = any(
        str(a).lower().startswith("author:") for a in card.aliases
    )
    has_collab = any(_PERSON_COLLAB_RE.search(r.quote or "") for r in records)
    has_temporal = any(_PERSON_TEMPORAL_RE.search(r.quote or "") for r in records)

    gates = {
        "n_quoted_contribution_chunks_ge_3": n_contribution >= 3,
        "n_distinct_docs_ge_2": n_docs >= 2,
        "author_metadata_present": author_alias,
    }
    components = {
        "n_quoted_contribution_chunks": 0.45 * min(n_contribution / 4.0, 1.0),
        "n_distinct_docs": 0.25 * min(n_docs / 3.0, 1.0),
        "has_collaboration_evidence": 0.15 * (1.0 if has_collab else 0.0),
        "has_temporal_anchor": 0.15 * (1.0 if has_temporal else 0.0),
    }
    score = sum(components.values())
    kinds = []
    if n_contribution > 0:
        kinds.append("contribution")
    if has_collab:
        kinds.append("collaboration")
    if has_temporal:
        kinds.append("temporal")
    return score, components, gates, kinds


# --- public entry point ---------------------------------------------


def compute_maturity(
    bundle: Bundle,
    slug: str,
    *,
    kind_stencil: str | None = None,
    current_round: int = 0,
    threshold: float = 0.70,
) -> MaturityReport:
    """Score a concept's readiness to write.

    See module docstring for the formula. Returns a ``MaturityReport``
    even when the slug is empty / missing — fields default to zeros.
    """
    card = load_card(bundle, slug)
    kind = card.kind or "article"
    records = [r for r in read_evidence(bundle, slug) if r.status == "active"]

    if kind == "person":
        stencil = "person"
        score, components, gates, kinds_present = _person_components(
            bundle, slug, records
        )
        all_gates = all(gates.values())
        band = _band(score, all_gates, threshold)
        return MaturityReport(
            slug=slug,
            kind=kind,
            kind_stencil=stencil,
            score=score if all_gates else 0.0,
            band=band,
            gates_passed=all_gates,
            growth_stalled=_growth_stalled(
                bundle, slug, current_round=current_round
            ),
            last_computed_round=current_round,
            components=components,
            gates=gates,
            n_chunks=len(records),
            n_docs=len({r.doc_id for r in records}),
            kinds_present=kinds_present,
        )

    # Article path
    stencil = kind_stencil
    if stencil is None:
        nb = read_notebook(bundle, slug)
        stencil = nb.front.maturity.kind_stencil or "article-method"
    if stencil not in STENCILS:
        stencil = "article-method"
    required_kinds = STENCILS[stencil]

    kinds_detected = _detect_kinds(records)
    has_definition = "definition" in kinds_detected
    n_chunks = len(records)
    n_docs = len({r.doc_id for r in records})
    growth_stalled = _growth_stalled(
        bundle, slug, current_round=current_round
    )

    gates = {
        "has_definition_evidence": has_definition,
        "n_chunks_ge_8": n_chunks >= 8,
        "n_docs_ge_4": n_docs >= 4,
        "growth_stalled": growth_stalled,
    }
    all_gates = all(gates.values())

    page_id = _wiki_page_id_for_slug(bundle, slug)
    mine = {r.chunk_id for r in records if r.chunk_id}
    neighbours = (
        _link_neighbours_chunk_sets(bundle, page_id) if page_id else []
    )
    jaccard_max = _max_chunk_jaccard(mine, neighbours)

    kinds_required_present = (
        len(kinds_detected & required_kinds) / max(1, len(required_kinds))
    )

    components = {
        "n_chunks": 0.25 * min(n_chunks / 12.0, 1.0),
        "n_docs": 0.15 * min(n_docs / 6.0, 1.0),
        "kinds_coverage": 0.30 * kinds_required_present,
        "redundancy_inverse": 0.20 * (1.0 - jaccard_max),
        "diversity_bonus": 0.10 * _diversity_bonus(records),
    }
    score = sum(components.values()) if all_gates else 0.0
    band = _band(score, all_gates, threshold)

    return MaturityReport(
        slug=slug,
        kind=kind,
        kind_stencil=stencil,
        score=score,
        band=band,
        gates_passed=all_gates,
        growth_stalled=growth_stalled,
        last_computed_round=current_round,
        components=components,
        gates=gates,
        n_chunks=n_chunks,
        n_docs=n_docs,
        kinds_present=sorted(kinds_detected),
    )


def _band(score: float, gates_passed: bool, threshold: float) -> str:
    if not gates_passed:
        return "growing" if score == 0.0 else "growing"
    if score >= threshold:
        return "ready"
    if score >= 0.50:
        return "growing"
    return "new"
