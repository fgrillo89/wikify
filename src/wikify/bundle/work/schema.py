"""Extract-side Pydantic models — re-exports from the legacy ``wikify.schema``.

Phase C will absorb these in-place when ``src/wikify/schema.py`` is
deleted; until then this module is the structural home (so new code
imports ``from wikify.bundle.work.schema import ExtractedConcept``).
"""

from __future__ import annotations

from ...schema import (
    Equation,
    EquationRef,
    ExtractedConcept,
    ExtractRequest,
    ExtractResponse,
    FigureCaption,
    ImageRef,
    Parameter,
    Relationship,
)

__all__ = [
    "Equation",
    "EquationRef",
    "ExtractRequest",
    "ExtractResponse",
    "ExtractedConcept",
    "FigureCaption",
    "ImageRef",
    "Parameter",
    "Relationship",
]
