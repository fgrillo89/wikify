"""Consolidate claims into a materialized data-artifact table.

A table is a pivot of subjects (rows) by properties (columns), derived from
the claim store on demand. Each non-empty cell carries the markers of the
claims that back it; cells where papers disagree are flagged as conflicts and
show every reported value. The consolidator never mutates stored values — it
projects them — so the same spec re-run after new claims arrive yields an
updated table (the "evolving artifact" property).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ArtifactSpec, normalize_key
from .store import DataStore

# Claims at or above the requested verification bar are eligible. "conflict"
# is treated as verified-but-contested (it passed the quote gate).
_TIERS = {
    "verified": {"verified", "conflict"},
    "any": {"verified", "conflict", "unverified", "figure_digitized"},
}


class SubjectFilterUnmatchedError(ValueError):
    """A non-empty ``ArtifactSpec.subjects`` filter matched no stored claim.

    The filter compares ``normalize_key(spec subject)`` against the stored
    ``subject_norm`` column, so a spec spelling that matches nothing — or a
    stale ``subject_norm`` written by an older ``normalize_key`` — produces a
    silent 0-row table rather than an error. Consolidation refuses instead.
    """

    def __init__(self, requested: list[str], available: list[str]) -> None:
        # Both sides are shown as NORMALIZED keys, because that is what the
        # filter actually compares. Printing the spec's raw spelling next to
        # normalized stored keys made a match look like a mismatch and sent
        # the reader hunting for a difference that normalization had already
        # erased.
        requested_norm = sorted({normalize_key(s) for s in requested})
        super().__init__(
            f"no stored claim has any of these subject keys: {requested_norm} "
            f"(from spec subjects {requested}). Stored subject keys include: "
            f"{available[:10]}. Fix the spec spelling, or run "
            "`wikify data reindex` if these claims were written by an older "
            "normalize_key."
        )
        self.requested = requested
        self.requested_norm = requested_norm
        self.available = available


@dataclass
class Cell:
    text: str = ""
    markers: list[str] = field(default_factory=list)
    conflict: bool = False


@dataclass
class ConsolidatedTable:
    artifact_id: str
    title: str
    description: str
    columns: list[str]  # display names for property columns
    property_keys: list[str]  # normalized keys aligned to columns
    rows: list[dict]  # {"subject": str, "cells": {col: Cell}}
    evidence: list[dict]  # ordered {marker, claim_id, doc_id, chunk_id, locator, quote}
    claim_ids: list[str]
    n_conflicts: int = 0
    empty_columns: list[str] = field(default_factory=list)  # spec props with no claims
    # Spec subjects that match no stored claim at all. The table still renders
    # from the subjects that DO resolve, so a typo in one of many no longer
    # blanks the artifact - but it must not vanish either, which is how a
    # silently-narrowed table reads as a complete one.
    missing_subjects: list[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.rows)


def _cell_value(claim: dict) -> str:
    """Human-readable value for a single claim, with unit + uncertainty."""
    parts = [claim.get("value_text") or claim.get("value_original") or ""]
    unc = (claim.get("uncertainty") or "").strip()
    if unc and unc not in parts[0]:
        parts.append(f"± {unc}".replace("± ±", "±"))
    unit = (claim.get("unit") or "").strip()
    text = " ".join(p for p in parts if p).strip()
    if unit and unit.lower() not in text.lower():
        text = f"{text} {unit}".strip()
    return text or "—"


def _canonical_value_key(claim: dict) -> str:
    """Identity used to decide whether two claims agree on a value."""
    num = claim.get("value_num")
    unit = normalize_key(claim.get("unit") or "")
    if num is not None:
        return f"{round(float(num), 6)}|{unit}"
    return f"{normalize_key(claim.get('value_text') or '')}|{unit}"


def consolidate(
    store: DataStore,
    spec: ArtifactSpec,
    *,
    restrict_claim_ids: set[str] | None = None,
) -> ConsolidatedTable:
    """Build the table described by *spec* from the claim store.

    ``restrict_claim_ids`` limits the table to a fixed set of claims — used to
    reproduce a *committed* artifact snapshot (the sidecar's recorded claim
    ids) so a projection never advances past the committed page. ``None``
    consolidates from the full current store (the evolving-artifact path).
    """
    allowed = _TIERS["verified"] if spec.min_verification == "verified" else _TIERS["any"]
    prop_keys = [normalize_key(p) for p in spec.properties]
    # Display names: prefer the spec's spelling.
    columns = list(spec.properties)

    subject_filter = {normalize_key(s) for s in spec.subjects} if spec.subjects else None

    # Marker assignment is stable across a single build, in first-seen order.
    # A marker identifies a CITATION (source doc/chunk + locator + grounding
    # quote), not a claim: two claims that cite the identical source with the
    # identical quote share one [^dN] marker, so a table cell and the
    # References list never repeat an identical citation. ``claim_ids`` still
    # records every backing claim (order preserved) for the sidecar snapshot.
    marker_for: dict[tuple[str, str, str, str], str] = {}
    claim_ids_seen: dict[str, None] = {}
    evidence: list[dict] = []

    def marker(claim: dict) -> str:
        claim_ids_seen.setdefault(claim["claim_id"], None)
        key = (
            claim.get("doc_id", "") or "",
            claim.get("chunk_id", "") or "",
            claim.get("locator", "") or "",
            (claim.get("grounding_quote", "") or "").strip(),
        )
        if key not in marker_for:
            m = f"d{len(marker_for) + 1}"
            marker_for[key] = m
            evidence.append({
                "marker": m,
                "claim_id": claim["claim_id"],
                "doc_id": claim.get("doc_id", ""),
                "chunk_id": claim.get("chunk_id", ""),
                "locator": claim.get("locator", ""),
                "quote": claim.get("grounding_quote", ""),
            })
        return marker_for[key]

    # Gather eligible claims per (subject_norm, property_norm).
    grouped: dict[str, dict[str, list[dict]]] = {}
    subject_display: dict[str, str] = {}
    for pk, prop in zip(prop_keys, spec.properties):
        for claim in store.list_points(property=prop):
            if claim["verification_status"] not in allowed:
                continue
            if restrict_claim_ids is not None and claim["claim_id"] not in restrict_claim_ids:
                continue
            sn = claim["subject_norm"]
            if subject_filter is not None and sn not in subject_filter:
                continue
            subject_display.setdefault(sn, claim["subject"])
            grouped.setdefault(sn, {}).setdefault(pk, []).append(claim)

    # A subjects filter naming a subject the store has never heard of yields a
    # 0-row table with no error, which reads downstream as "no data for these
    # subjects" when the real cause is a spec spelling or a stale
    # ``subject_norm``.
    #
    # The test is deliberately against the WHOLE store, not against ``grouped``:
    # an empty ``grouped`` also means "this subject exists but its property has
    # not been harvested yet" or "its claims are below min_verification", both
    # of which are legitimate not-yet states a DRAFT build must still render.
    # Only a subject that resolves to no stored claim AT ALL is an authoring
    # error here. (Publishing is stricter: ``_publish_blockers`` additionally
    # refuses a named subject that contributes no row, which is the case this
    # store-wide test cannot see.) The committed-snapshot path
    # (``restrict_claim_ids``) is exempt: it may legitimately reproduce fewer
    # claims than the spec names.
    missing_subjects: list[str] = []
    if subject_filter is not None and restrict_claim_ids is None:
        stored_subject_norms = {s["subject_norm"] for s in store.subjects()}
        missing_subjects = [
            subj for subj in spec.subjects
            if normalize_key(subj) not in stored_subject_norms
        ]
        # An empty store is "no data harvested yet", not an authoring error;
        # gating it would turn a first `data consolidate` into a hard failure.
        if stored_subject_norms and not (subject_filter & stored_subject_norms):
            raise SubjectFilterUnmatchedError(
                list(spec.subjects), sorted(stored_subject_norms)
            )

    rows: list[dict] = []
    n_conflicts = 0
    # Row order: follow the spec's subject list when given, else by display
    # name. Subjects named in the spec but absent from the data sort last.
    spec_order = (
        {normalize_key(s): i for i, s in enumerate(spec.subjects)}
        if subject_filter is not None
        else {}
    )

    def _row_key(sn: str) -> tuple:
        return (spec_order.get(sn, len(spec_order)), subject_display.get(sn, sn).lower())

    for sn in sorted(grouped, key=_row_key):
        cells: dict[str, Cell] = {}
        for pk, col in zip(prop_keys, columns):
            claims = grouped[sn].get(pk, [])
            if not claims:
                cells[col] = Cell()
                continue
            distinct = {_canonical_value_key(c): c for c in claims}
            if len(distinct) == 1:
                claim = next(iter(distinct.values()))
                # Merge markers from all claims that agree; claims sharing an
                # identical citation collapse to one marker (preserve order).
                markers = list(dict.fromkeys(marker(c) for c in claims))
                cells[col] = Cell(text=_cell_value(claim), markers=markers)
            else:
                # conflict: show each distinct value with its marker
                n_conflicts += 1
                pieces = []
                markers: list[str] = []
                for c in distinct.values():
                    m = marker(c)
                    markers.append(m)
                    pieces.append(f"{_cell_value(c)} [^{m}]")
                cells[col] = Cell(
                    text="; ".join(pieces), markers=markers, conflict=True
                )
        # Drop rows that ended up entirely empty.
        if any(cell.text for cell in cells.values()):
            rows.append({"subject": subject_display[sn], "cells": cells})

    # A spec property that produced no non-empty cell anywhere is a silent
    # empty column — usually a spelling that does not match any stored
    # property_norm. Surface it so the caller can warn instead of shipping a
    # blank column (F22).
    nonempty_cols = {
        col for row in rows for col, cell in row["cells"].items() if cell.text
    }
    empty_columns = [col for col in columns if col not in nonempty_cols]

    return ConsolidatedTable(
        artifact_id=spec.artifact_id,
        title=spec.title,
        description=spec.description,
        columns=columns,
        property_keys=prop_keys,
        rows=rows,
        evidence=evidence,
        claim_ids=list(claim_ids_seen.keys()),
        n_conflicts=n_conflicts,
        empty_columns=empty_columns,
        missing_subjects=missing_subjects,
    )
