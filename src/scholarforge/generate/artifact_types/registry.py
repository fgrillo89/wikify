"""Artifact type registry — defines structure and rules per document type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ArtifactType:
    """Defines the structure and writing rules for a document type."""

    id: str
    name: str
    sections: list[str]  # required section headings in order
    instructions: str  # type-specific writing rules (compact)

    def full_instructions(self, base_style_guide: str) -> str:
        """Combine base style guide with type-specific instructions."""
        return f"{base_style_guide}\n\n---\n\n{self.instructions}"


# ── Literature Review ────────────────────────────────────────────────────────
# Based on PMC3715443 "Ten Simple Rules for Writing a Literature Review"

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
    instructions="""\
## Literature Review Rules

**Structure**: NOT standard IMRaD. Use: Introduction (context + gap), \
thematic body sections (organized by concepts, not by paper), Conclusion \
(synthesis + future directions).

**Synthesize, don't summarize.** Never describe papers one-by-one. Group \
findings by theme, compare across studies, identify agreements and \
contradictions. A review that merely lists "Study A found X, Study B \
found Y" is a bibliography, not a review.

**Introduction**: Funnel from broad field to specific gap. State why this \
review is needed now. End with scope statement.

**Body sections**: Organize by concepts/themes, not chronologically. Each \
section addresses a research question or debate. Within each section:
- State the theme's current understanding
- Present supporting evidence from multiple papers
- Note contradictions or methodological differences
- Identify what remains unresolved

**Critical assessment**: For each major finding, evaluate: How robust is \
the evidence? Do methods differ across studies? Are results reproducible? \
What are the limitations?

**Conclusion**: Do NOT restate the abstract. Synthesize: what does the \
body of evidence collectively tell us? State 2-3 concrete open questions \
or recommended next experiments.

**Citations**: High density (1-3 per claim). Weave into narrative. Use \
author names for key findings, numbers for background consensus.

**Scope**: Stay focused. Discuss wider implications briefly but don't \
attempt to cover adjacent fields in depth.\
""",
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
    instructions="""\
## Research Article Rules

**Structure**: Standard IMRaD (Introduction, Methods, Results, Discussion).

**Abstract**: 4 moves — context, gap, approach+results, significance. \
One paragraph, no citations.

**Introduction**: Funnel to gap. End with "Here we show/report/demonstrate..."

**Methods**: Past tense, enough detail for reproduction. Specific parameters.

**Results**: Present findings in logical order. Lead with main finding per \
subsection. "Figure N shows..." followed by interpretation.

**Discussion**: Compare with prior work. Propose mechanisms. Acknowledge \
limitations honestly. State implications.

**Conclusion**: 1-2 paragraphs. Primary contribution + specific future work.\
""",
)

# ── Grant Proposal ───────────────────────────────────────────────────────────

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
    instructions="""\
## Grant Proposal Rules

**Structure**: Specific Aims (1 page), Significance, Innovation, Approach.

**Specific Aims**: Hook (1-2 sentences), gap, long-term goal, objective, \
central hypothesis, 2-3 specific aims with brief rationale.

**Significance**: Why does this matter? What gap does it fill?

**Innovation**: What's new about the approach?

**Approach**: Detailed methods per aim. Include preliminary data, expected \
outcomes, potential pitfalls and alternatives.

**Tone**: Confident but not arrogant. "We will" not "We hope to".\
""",
)

# ── Registry ─────────────────────────────────────────────────────────────────

ARTIFACT_TYPES: dict[str, ArtifactType] = {
    "lit_review": _LIT_REVIEW,
    "research_article": _RESEARCH_ARTICLE,
    "grant_proposal": _GRANT_PROPOSAL,
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
