"""Typed shapes for the factual-data subsystem.

A :class:`DataPoint` is one extracted fact: a small assertion
(subject / property / value / unit) plus open conditions and the provenance
needed to verify it. An :class:`ArtifactSpec` is the durable recipe for a
consolidated table; the table itself is always re-derived from the claim
store, never stored as truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

# Canonical vocabularies. Kept small and explicit; the property/condition
# space is intentionally open (schema-on-read) — these only constrain the
# few fields that gate correctness.
VALUE_TYPES = (
    "scalar",
    "range",
    "upper_bound",
    "lower_bound",
    "list",
    "categorical",
)
SOURCE_KINDS = ("table", "text", "caption", "figure_caption", "figure")
EXTRACTION_TIERS = ("T1", "T2", "T3")
VERIFICATION_STATES = (
    "verified",
    "unverified",
    "conflict",
    "figure_digitized",
    "rejected",
)

_WS_RE = re.compile(r"\s+")
# A signed number with optional decimal, scientific notation, and unicode minus.
_NUMBER_RE = re.compile(
    r"[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
)
# A space-separated thousands grouping: 1-3 digit lead then all-3-digit groups
# (``10 000``, ``1 234 567``), the final group optionally carrying a decimal.
# Bounded by non-digits so it never straddles two unrelated numbers.
_SPACED_THOUSANDS_RE = re.compile(
    r"(?<!\d)([-+−]?\d{1,3}(?:[   ]\d{3})+(?:\.\d+)?)(?!\d)"
)
_THOUSANDS_SPACE_RE = re.compile(r"[   ]")


def collapse_spaced_thousands(s: str) -> str:
    """Join space-separated thousands groups into a single token so the number
    parser reads the correct magnitude: ``10 000 cycles`` -> ``10000 cycles``.
    Only a 1-3 digit lead followed by 3-digit groups is collapsed; an
    OCR-mangled run like ``1 10 5`` is left untouched."""
    return _SPACED_THOUSANDS_RE.sub(
        lambda m: _THOUSANDS_SPACE_RE.sub("", m.group(1)), s or ""
    )


def normalize_key(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation noise for matching.

    Used to derive ``subject_norm`` / ``property_norm`` so that
    "Growth per cycle" and "growth-per-cycle " collapse to one key.
    """
    s = (text or "").strip().lower()
    s = s.replace("-", " ").replace("_", " ").replace("/", " ")
    s = _WS_RE.sub(" ", s)
    return s.strip()


def parse_leading_number(value: str) -> float | None:
    """Best-effort canonical numeric from a value string.

    Returns the first number found (handling unicode minus, thousands
    separators, scientific notation). ``None`` when no number is present
    (ranges and categoricals keep their text form in ``value_text``).
    """
    if not value:
        return None
    m = _NUMBER_RE.search(collapse_spaced_thousands(value))
    if not m:
        return None
    token = (
        m.group(0)
        .replace("−", "-")
        .replace(" ", "")
        .replace(",", "")
        .replace(" ", "")
    )
    try:
        return float(token)
    except ValueError:
        return None


@dataclass
class DataPoint:
    """One extracted factual figure with verifiable provenance."""

    # assertion
    subject: str
    property: str
    value_text: str
    doc_id: str
    grounding_quote: str
    # optional assertion detail
    unit: str = ""
    value_original: str = ""
    unit_original: str = ""
    uncertainty: str = ""
    value_type: str = "scalar"
    # conditions (open key-space) + method
    conditions: dict = field(default_factory=dict)
    method: str = ""
    # provenance
    chunk_id: str = ""
    locator: str = ""
    source_kind: str = "text"
    extraction_tier: str = "T1"
    # assurance (set/overwritten by the verify gate + consolidation)
    verification_status: str = "unverified"
    quote_verified: bool = False
    confidence: float | None = None
    extractor: str = ""
    round: int | None = None
    created_at: str = ""

    # derived (filled in by ``finalize``)
    subject_norm: str = ""
    property_norm: str = ""
    value_num: float | None = None
    claim_id: str = ""

    def finalize(self) -> DataPoint:
        """Populate derived keys + content-hash id. Idempotent."""
        self.subject_norm = normalize_key(self.subject)
        self.property_norm = normalize_key(self.property)
        if self.value_num is None:
            self.value_num = parse_leading_number(self.value_original or self.value_text)
        if not self.value_original:
            self.value_original = self.value_text
        self.claim_id = self._content_hash()
        return self

    def _content_hash(self) -> str:
        # Prefer the normalized numeric value so "1.1" and "1.10" collapse to
        # one claim; fall back to raw text for non-numeric values. Include
        # uncertainty + value_type so two facts that differ only there are
        # not merged into one.
        value_key = (
            repr(round(self.value_num, 6))
            if self.value_num is not None
            else (self.value_text or "").strip().lower()
        )
        payload = json.dumps(
            [
                self.doc_id,
                self.chunk_id,
                self.subject_norm,
                self.property_norm,
                value_key,
                (self.unit or "").strip().lower(),
                (self.uncertainty or "").strip().lower(),
                (self.value_type or "").strip().lower(),
                json.dumps(self.conditions, sort_keys=True, default=str),
            ],
            default=str,
        )
        return "dp_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("conditions", None)
        d["conditions_json"] = json.dumps(self.conditions, sort_keys=True, default=str)
        d["quote_verified"] = 1 if self.quote_verified else 0
        return d

    @classmethod
    def from_dict(cls, d: dict) -> DataPoint:
        """Build from a loose dict (staged JSONL record). Tolerant of aliases."""
        conditions = d.get("conditions") or {}
        if isinstance(conditions, str):
            try:
                conditions = json.loads(conditions)
            except json.JSONDecodeError:
                conditions = {}
        return cls(
            subject=str(d.get("subject", "")).strip(),
            property=str(d.get("property", "")).strip(),
            value_text=str(d.get("value_text", d.get("value", ""))).strip(),
            doc_id=str(d.get("doc_id", "")).strip(),
            grounding_quote=str(d.get("grounding_quote", d.get("quote", ""))).strip(),
            unit=str(d.get("unit", "")).strip(),
            value_original=str(d.get("value_original", "")).strip(),
            unit_original=str(d.get("unit_original", "")).strip(),
            uncertainty=str(d.get("uncertainty", "")).strip(),
            value_type=str(d.get("value_type", "scalar")).strip() or "scalar",
            conditions=dict(conditions) if isinstance(conditions, dict) else {},
            method=str(d.get("method", "")).strip(),
            chunk_id=str(d.get("chunk_id", "")).strip(),
            locator=str(d.get("locator", "")).strip(),
            source_kind=str(d.get("source_kind", "text")).strip() or "text",
            extraction_tier=str(d.get("extraction_tier", "T1")).strip() or "T1",
            confidence=_as_float(d.get("confidence")),
            extractor=str(d.get("extractor", "")).strip(),
            round=_as_int(d.get("round")),
        )


@dataclass
class ArtifactSpec:
    """Durable recipe for a consolidated data-artifact table.

    The table is a pivot of ``subject`` (rows) by ``properties`` (columns),
    optionally restricted to a subject set and filtered by conditions. The
    consolidator re-derives the table from the claim store on every rebuild,
    so the spec — not the rendered table — is the thing that persists.
    """

    artifact_id: str
    title: str
    properties: list[str]
    description: str = ""
    subjects: list[str] = field(default_factory=list)  # empty = all subjects with data
    condition_columns: list[str] = field(default_factory=list)
    min_verification: str = "verified"  # only cells from claims at/above this

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> ArtifactSpec:
        d = json.loads(s)
        return cls(
            artifact_id=str(d["artifact_id"]),
            title=str(d.get("title", d["artifact_id"])),
            properties=[str(p) for p in d.get("properties", [])],
            description=str(d.get("description", "")),
            subjects=[str(x) for x in d.get("subjects", [])],
            condition_columns=[str(x) for x in d.get("condition_columns", [])],
            min_verification=str(d.get("min_verification", "verified")),
        )


def _as_float(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(v: object) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
