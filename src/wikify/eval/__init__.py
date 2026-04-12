"""Evaluation harness for wikify wiki bundles.

Modules:
  - metrics.py: one pure function per metric (M1, M2, M3, M5, M6, GT-P, GT-C)

Every metric takes a Bundle (and, where needed, a Corpus or a callable) and
returns a scalar or a small dataclass. No metric mutates anything. No metric
calls an LLM.

Bundle/Page/Evidence types and loading live in ``store.wiki_bundle``.

See ../metrics.md for the definitions.
"""

from ..store.wiki_bundle import Bundle, Evidence, Page, load_bundle
from .metrics import (
    GroundingResult,
    HeapsFit,
    concept_recall,  # GT-C
    coverage_residual,  # M1
    grounding,  # M6
    heaps_exponent,  # M2
    hit_rate,  # M5
    person_recall,  # GT-P
    spectral_gap_modularity,  # M3
)

__all__ = [
    "Bundle",
    "Evidence",
    "GroundingResult",
    "HeapsFit",
    "Page",
    "concept_recall",
    "coverage_residual",
    "grounding",
    "heaps_exponent",
    "hit_rate",
    "load_bundle",
    "person_recall",
    "spectral_gap_modularity",
]
