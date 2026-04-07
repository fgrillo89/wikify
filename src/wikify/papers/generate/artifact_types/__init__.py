"""Artifact type definitions for generated documents.

Each artifact type defines:
- Required structure (sections/headings)
- Type-specific writing instructions
- How the type combines with the base writing style guide
"""

from __future__ import annotations

from wikify.papers.generate.artifact_types.registry import (
    ARTIFACT_TYPES,
    ArtifactType,
    get_artifact_type,
    list_artifact_types,
)

__all__ = ["ARTIFACT_TYPES", "ArtifactType", "get_artifact_type", "list_artifact_types"]
