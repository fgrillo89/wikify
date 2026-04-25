"""Write-side Pydantic — re-exports from the legacy ``wikify.schema`` until Phase C."""

from __future__ import annotations

from ...schema import (
    QuoteNotInChunkError,
    WriteEvidenceRef,
    WriteEvidenceRefV2,
    WriteRequest,
    WriteResponse,
    _check_figure_mentions,
    _check_wikipedia_structure,
    _has_section,
    _split_sections,
)

__all__ = [
    "QuoteNotInChunkError",
    "WriteEvidenceRef",
    "WriteEvidenceRefV2",
    "WriteRequest",
    "WriteResponse",
    "_check_figure_mentions",
    "_check_wikipedia_structure",
    "_has_section",
    "_split_sections",
]
