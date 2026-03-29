"""Versioned prompt templates for LLM interactions.

Each template is a frozen dataclass with typed input slots and a
``render()`` method that validates all slots are filled. Prompts
live in one place, are version-tagged, and can be unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptTemplate:
    """A versioned, testable prompt template."""

    name: str
    version: str
    system: str
    user: str
    input_fields: dict[str, type] = field(default_factory=dict)

    def render(self, **kwargs: Any) -> list[dict[str, str]]:
        """Render to a messages list, validating all slots are filled."""
        missing = set(self.input_fields) - set(kwargs)
        if missing:
            raise ValueError(f"Missing template fields: {missing}")
        extra = set(kwargs) - set(self.input_fields)
        if extra:
            raise ValueError(f"Unexpected template fields: {extra}")
        return [
            {"role": "system", "content": self.system.format(**kwargs)},
            {"role": "user", "content": self.user.format(**kwargs)},
        ]


# ── Concrete templates ───────────────────────────────────────────────────────

PLAN_PAPER = PromptTemplate(
    name="plan_paper",
    version="2.0",
    system=(
        "{persona}\n\n"
        "Given a writing prompt and source papers, create a detailed outline "
        "for a {artifact_name}.\n\n"
        "{schema_instructions}\n\n"
        "{section_guidance}\n"
        "{type_hint}\n"
        "Distribute the target word count across sections proportionally."
    ),
    user=("Prompt: {user_prompt}\n\nAvailable papers:\n{paper_list}\n\n{graph_section}"),
    input_fields={
        "persona": str,
        "artifact_name": str,
        "schema_instructions": str,
        "section_guidance": str,
        "type_hint": str,
        "user_prompt": str,
        "paper_list": str,
        "graph_section": str,
    },
)

WRITE_SECTION = PromptTemplate(
    name="write_section",
    version="2.0",
    system=(
        "{persona}\n\n"
        "Write the following section of a review paper based on the "
        "literature provided. Cite sources using [REF:display_name] markers.\n"
        "Be precise, technical, and thorough.\n"
        "Do NOT include the section heading.\n"
        "Target approximately {target_tokens} words.\n"
        "{figure_instruction}\n"
        "After drafting, self-revise: check for banned words, "
        "nominalizations, passive voice overuse, vague quantifiers."
    ),
    user=(
        "Paper title: {paper_title}\n"
        "Section: {section_heading}\n"
        "Section description: {section_description}\n"
        "{source_hint}\n\n"
        "--- Previously written sections ---\n{prior_sections}\n\n"
        "--- Literature context ---\n{lit_context}"
    ),
    input_fields={
        "persona": str,
        "target_tokens": str,
        "figure_instruction": str,
        "paper_title": str,
        "section_heading": str,
        "section_description": str,
        "source_hint": str,
        "prior_sections": str,
        "lit_context": str,
    },
)

HUB_SYNTHESIS = PromptTemplate(
    name="hub_synthesis",
    version="1.0",
    system=("You are a research subagent exploring a hub paper and its neighborhood."),
    user=(
        "Hub paper: {hub_title} ({hub_authors}, {hub_year})\n\n"
        "Produce a DENSE synthesis (200-300 words max):\n"
        "1. Hypothesis -> Test -> Result\n"
        "2. State of the art\n"
        "3. Pitfalls and limitations\n"
        "4. Conclusions and open questions\n"
        "5. Reading recommendations: READ IN FULL / SKIM / SKIP\n\n"
        "{focus_instruction}\n\n"
        "{schema_instructions}\n\n"
        "--- Excerpts ---\n{excerpts}"
    ),
    input_fields={
        "hub_title": str,
        "hub_authors": str,
        "hub_year": str,
        "focus_instruction": str,
        "schema_instructions": str,
        "excerpts": str,
    },
)
