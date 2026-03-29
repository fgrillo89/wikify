"""Artifact type registry — defines structure and rules per document type."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

_GUIDES_DIR = Path(__file__).resolve().parents[2] / "prompts" / "artifact_types"


@dataclass
class ArtifactType:
    """Defines the structure and writing rules for a document type."""

    id: str
    name: str
    sections: list[str]  # required section headings in order
    instructions_file: str  # filename inside docs/logic/artifact_types/

    @cached_property
    def instructions(self) -> str:
        """Load instructions from the corresponding .md file."""
        path = _GUIDES_DIR / self.instructions_file
        return path.read_text(encoding="utf-8")

    def full_instructions(self, base_style_guide: str) -> str:
        """Combine base style guide with type-specific instructions."""
        return f"{base_style_guide}\n\n---\n\n{self.instructions}"


# Tell dataclasses not to include cached_property in __eq__/__hash__
ArtifactType.__hash__ = None  # type: ignore[assignment]


# ── Literature Review ────────────────────────────────────────────────────────

_LIT_REVIEW = ArtifactType(
    id="lit_review",
    name="Literature Review",
    sections=[
        "Abstract",
        "Introduction",
        # Main body sections are determined by the planner based on
        # the corpus structure — these are thematic, not prescribed
        "Conclusion",
    ],
    instructions_file="lit_review.md",
)

# ── Research Article ─────────────────────────────────────────────────────────

_RESEARCH_ARTICLE = ArtifactType(
    id="research_article",
    name="Research Article",
    sections=[
        "Abstract",
        "Introduction",
        "Methods",
        "Results",
        "Discussion",
        "Conclusion",
    ],
    instructions_file="research_article.md",
)

# ── Grant Proposal ──────────────────────────────────────────────────────────

_GRANT_PROPOSAL = ArtifactType(
    id="grant_proposal",
    name="Grant Proposal",
    sections=[
        "Abstract",
        "Specific Aims",
        "Significance",
        "Innovation",
        "Approach",
    ],
    instructions_file="grant_proposal.md",
)

# ── Technical Report ────────────────────────────────────────────────────────

_TECHNICAL_REPORT = ArtifactType(
    id="technical_report",
    name="Technical Report",
    sections=[
        "Abstract",
        "Introduction",
        "Background",
        "Methods",
        "Results",
        "Analysis",
        "Conclusions",
        "Recommendations",
    ],
    instructions_file="technical_report.md",
)

# ── Master Thesis ───────────────────────────────────────────────────────────

_MASTER_THESIS = ArtifactType(
    id="master_thesis",
    name="Master Thesis",
    sections=[
        "Abstract",
        "Introduction",
        "Literature Review",
        "Methodology",
        "Results",
        "Discussion",
        "Conclusions",
    ],
    instructions_file="master_thesis.md",
)

# ── PhD Thesis ──────────────────────────────────────────────────────────────

_PHD_THESIS = ArtifactType(
    id="phd_thesis",
    name="PhD Thesis",
    sections=[
        "Abstract",
        "Introduction",
        "Literature Review",
        "Theoretical Framework",
        "Methodology",
        "Results",
        "Discussion",
        "Conclusions",
        "Future Work",
    ],
    instructions_file="phd_thesis.md",
)

# ── Undergraduate Research Paper ────────────────────────────────────────────

_RESEARCH_PAPER_UNDERGRAD = ArtifactType(
    id="research_paper_undergrad",
    name="Undergraduate Research Paper",
    sections=[
        "Abstract",
        "Introduction",
        "Methods",
        "Results",
        "Discussion",
        "Conclusion",
    ],
    instructions_file="research_paper_undergrad.md",
)

# ── Registry ────────────────────────────────────────────────────────────────

ARTIFACT_TYPES: dict[str, ArtifactType] = {
    "lit_review": _LIT_REVIEW,
    "research_article": _RESEARCH_ARTICLE,
    "grant_proposal": _GRANT_PROPOSAL,
    "technical_report": _TECHNICAL_REPORT,
    "master_thesis": _MASTER_THESIS,
    "phd_thesis": _PHD_THESIS,
    "research_paper_undergrad": _RESEARCH_PAPER_UNDERGRAD,
}


def get_artifact_type(type_id: str) -> ArtifactType:
    """Look up an artifact type by ID."""
    if type_id not in ARTIFACT_TYPES:
        available = ", ".join(ARTIFACT_TYPES)
        msg = f"Unknown artifact type '{type_id}'. Available: {available}"
        raise ValueError(msg)
    return ARTIFACT_TYPES[type_id]


def list_artifact_types() -> list[dict[str, str]]:
    """Return metadata for all artifact types."""
    return [
        {"id": t.id, "name": t.name, "sections": ", ".join(t.sections)}
        for t in ARTIFACT_TYPES.values()
    ]
