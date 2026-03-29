"""Pydantic output models for every LLM interaction.

Each model defines the contract the LLM must satisfy. Field validators
encode domain-specific constraints; on failure the validate-and-retry
loop feeds the error back to the LLM for correction.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Paper planning ───────────────────────────────────────────────────────────


class PlannedSection(BaseModel):
    """A single section in a paper plan."""

    heading: str = Field(..., min_length=3)
    level: Literal[1, 2, 3] = 1
    description: str = Field(..., min_length=10)
    target_tokens: int = Field(..., gt=50, lt=5000)
    source_papers: list[str] = Field(default_factory=list)
    subsections: list[PlannedSection] = Field(default_factory=list)

    @field_validator("heading")
    @classmethod
    def no_numbered_prefix(cls, v: str) -> str:
        """Strip LLM-added numbered prefixes like '1.' or '2)'."""
        return re.sub(r"^\d+[\.\)]\s*", "", v)


class PaperPlanOutput(BaseModel):
    """Validated paper plan returned by the LLM."""

    title: str = Field(..., min_length=5, max_length=200)
    paper_type: Literal["lit_review", "research", "research_article", "grant_proposal", "abstract"]
    target_length: int = Field(..., gt=500)
    sections: list[PlannedSection] = Field(..., min_length=3)

    @field_validator("sections")
    @classmethod
    def must_have_intro_and_conclusion(
        cls,
        v: list[PlannedSection],
    ) -> list[PlannedSection]:
        headings_lower = [s.heading.lower() for s in v]
        if not any("intro" in h for h in headings_lower):
            raise ValueError("Plan must include an Introduction section")
        if not any("conclu" in h for h in headings_lower):
            raise ValueError("Plan must include a Conclusion section")
        return v


# ── Section writing ──────────────────────────────────────────────────────────

# Phrases that betray LLM-generated text.
_LLM_TELLS: list[tuple[str, str]] = [
    (r"\bdelve\b", "delve"),
    (r"\beverchanging\b", "everchanging"),
    (r"\bIt is worth noting\b", "It is worth noting"),
    (r"\bcrucial\b", "crucial"),
    (r"\bpivotal\b", "pivotal"),
    (r"\bin the realm of\b", "in the realm of"),
    (r"\bpaving the way\b", "paving the way"),
    (r"\bparadigm shift\b", "paradigm shift"),
]


class SectionOutput(BaseModel):
    """Validated output from the section writer.

    The ``content`` field holds the prose text. Validators enforce
    minimum length, citation presence, and style quality.
    ``citations_found`` is auto-populated by the model validator.
    """

    content: str = Field(..., min_length=100)
    citations_found: list[str] = Field(default_factory=list)
    # When True, citation check is skipped (e.g. for Abstract sections).
    _skip_citation_check: bool = False

    @field_validator("content")
    @classmethod
    def minimum_word_count(cls, v: str) -> str:
        words = len(v.split())
        if words < 50:
            raise ValueError(
                f"Section has only {words} words; minimum is 50. "
                "Expand the section with more detail."
            )
        return v

    @field_validator("content")
    @classmethod
    def no_llm_tells(cls, v: str) -> str:
        found = [name for pattern, name in _LLM_TELLS if re.search(pattern, v, re.IGNORECASE)]
        if found:
            raise ValueError(
                f"Remove LLM stylistic tells: {', '.join(found)}. Rephrase these passages."
            )
        return v

    @model_validator(mode="after")
    def extract_citations(self) -> SectionOutput:
        """Populate citations_found from [REF:...] markers in content."""
        self.citations_found = re.findall(r"\[REF:([^\]]+)\]", self.content)
        return self

    def check_citations(self) -> None:
        """Raise ValueError if no citations found (call after construction).

        Separated from field_validator so callers can skip for Abstract.
        """
        refs = re.findall(r"\[REF:[^\]]+\]", self.content)
        if len(refs) < 1:
            raise ValueError(
                "Section must contain at least one [REF:...] citation marker. "
                "Cite the source papers."
            )


# ── Hub-spoke synthesis ──────────────────────────────────────────────────────


class HubSynthesisOutput(BaseModel):
    """Structured output for hub-spoke synthesis."""

    summary: str = Field(..., min_length=100, max_length=2000)
    read_in_full: list[str] = Field(default_factory=list)
    skim: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("read_in_full")
    @classmethod
    def at_least_one_deep_read(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("Must recommend at least one paper for deep reading.")
        return v
