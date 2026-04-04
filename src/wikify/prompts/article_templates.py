"""Adaptive article templates by concept type.

Each concept type gets a tailored article structure. Templates are
domain-agnostic -- they describe what sections to include based on
the *kind* of thing (material, technique, phenomenon, etc.), not
the *domain* (materials science, biology, etc.).

The LLM writing agent uses these templates to structure its output.
If a concept's type doesn't match any template, it falls back to
the generic template.
"""

from __future__ import annotations

# ── Template definitions ────────────────────────────────────────────────────

TEMPLATES: dict[str, str] = {
    "material": """\
Write a wiki article about the material "{name}".

Structure:
1. **Lead** (2-3 sentences): What it is, chemical formula if applicable, \
why it matters in this field.
2. **## Properties**: Key physical, chemical, or electronic properties. \
Use a table if multiple properties are known.
3. **## Synthesis & Processing**: How it is made or deposited. Methods, \
conditions, variants.
4. **## Applications**: What it is used for. Link to application concepts \
with [[wikilinks]].
5. **## Comparison with Alternatives**: How it compares to competing \
materials for the same applications.
6. **## Open Questions**: What remains unresolved.
{parameters_instruction}
{evidence_instruction}
""",
    "technique": """\
Write a wiki article about the technique "{name}".

Structure:
1. **Lead** (2-3 sentences): What the technique does, its core principle.
2. **## Mechanism**: How it works step by step.
3. **## Process Parameters**: Key controllable variables and their effects. \
Use a table if parameters are known.
4. **## Advantages & Limitations**: What it does well and where it falls short.
5. **## Applications**: Where it is used. Link with [[wikilinks]].
6. **## Variants**: Related or competing techniques.
7. **## Open Questions**: What remains unresolved.
{parameters_instruction}
{evidence_instruction}
""",
    "phenomenon": """\
Write a wiki article about the phenomenon "{name}".

Structure:
1. **Lead** (2-3 sentences): What the phenomenon is, where it is observed.
2. **## Physical Mechanism**: The underlying physics or chemistry. \
Cause and effect chain.
3. **## Experimental Signatures**: How it is detected or measured. \
Key observables.
4. **## Models & Theories**: Theoretical frameworks that explain it. \
Note where models disagree.
5. **## Influencing Factors**: What controls or modifies the phenomenon.
6. **## Open Questions**: What remains unexplained.
{parameters_instruction}
{evidence_instruction}
""",
    "method": """\
Write a wiki article about the method "{name}".

Structure:
1. **Lead** (2-3 sentences): What the method accomplishes, its context.
2. **## Procedure**: Step-by-step description of how it works.
3. **## Inputs & Outputs**: What goes in, what comes out.
4. **## Assumptions & Constraints**: When it applies and when it doesn't.
5. **## Alternatives**: Other methods that achieve similar goals. \
Compare trade-offs.
6. **## Open Questions**: Limitations or gaps.
{parameters_instruction}
{evidence_instruction}
""",
    "theory": """\
Write a wiki article about the theory or model "{name}".

Structure:
1. **Lead** (2-3 sentences): What the theory explains, who proposed it.
2. **## Core Principles**: The fundamental ideas or equations.
3. **## Predictions**: What the theory predicts that can be tested.
4. **## Experimental Support**: Evidence for and against.
5. **## Limitations**: Where the theory breaks down or is incomplete.
6. **## Competing Theories**: Alternative explanations.
7. **## Open Questions**: What remains unresolved.
{parameters_instruction}
{evidence_instruction}
""",
    "dataset": """\
Write a wiki article about the dataset or benchmark "{name}".

Structure:
1. **Lead** (2-3 sentences): What it contains, who created it, why it \
matters.
2. **## Contents**: Size, format, what is measured or labeled.
3. **## Usage**: How it is used in the field. Common tasks.
4. **## Limitations**: Known biases, missing categories, scale issues.
5. **## Alternatives**: Competing datasets or benchmarks.
{parameters_instruction}
{evidence_instruction}
""",
    "synthesis": """\
Write a synthesis article about "{name}".

This is a **cross-cutting concept** that does not exist in any single \
source but emerges from combining evidence across multiple papers.

Structure:
1. **Lead** (2-3 sentences): What this synthesis addresses, why it \
matters.
2. **## Contributing Evidence**: Which concepts and papers feed into \
this synthesis. Cite each with [REF:paper_display].
3. **## Analysis**: The cross-cutting insight that emerges. Connect \
the dots between sources.
4. **## Implications**: What this means for the field. Practical \
consequences.
5. **## Remaining Gaps**: What evidence is still missing to \
strengthen or challenge this synthesis.
{parameters_instruction}
{evidence_instruction}
""",
}

# Fallback for unknown or empty concept types
GENERIC_TEMPLATE = """\
Write a wiki article about "{name}".

Structure:
1. **Lead** (2-3 sentences): What it is, why it matters.
2. **## What Is Known**: Established facts. Cite evidence with \
[REF:paper_display].
3. **## Where the Field Disagrees**: Contested points or open debates.
4. **## Open Questions**: What remains unresolved.
{parameters_instruction}
{evidence_instruction}
"""

# ── Common instruction fragments ────────────────────────────────────────────

_PARAMETERS_WITH_DATA = """\
Include a **## Parameters** table:
| Parameter | Value | Unit | Conditions |
|-----------|-------|------|------------|
{param_rows}
"""

_PARAMETERS_EMPTY = ""

_EVIDENCE_WITH_DATA = """\
Use these evidence quotes as sources. Cite with [REF:paper_display]:
{evidence_lines}
"""

_EVIDENCE_EMPTY = ""


# ── Public API ──────────────────────────────────────────────────────────────


def get_article_template(
    concept_type: str,
    name: str,
    parameters: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> str:
    """Get the appropriate article template for a concept type.

    Args:
        concept_type: One of technique, material, phenomenon, method,
            theory, dataset, or empty string for generic.
        name: Concept display name.
        parameters: List of param dicts with name, value, unit, conditions.
        evidence: List of evidence dicts with paper_display, quote.

    Returns:
        Formatted template string ready for the writing agent.
    """
    template = TEMPLATES.get(concept_type, GENERIC_TEMPLATE)

    # Build parameters instruction
    if parameters:
        rows = "\n".join(
            f"| {p.get('name', '')} | {p.get('value', '')} "
            f"| {p.get('unit', '')} | {p.get('conditions', '')} |"
            for p in parameters
        )
        params_instruction = _PARAMETERS_WITH_DATA.format(param_rows=rows)
    else:
        params_instruction = _PARAMETERS_EMPTY

    # Build evidence instruction
    if evidence:
        lines = "\n".join(
            f'- [REF:{e.get("paper_display", "")}]: "{e.get("quote", "")}"' for e in evidence
        )
        evidence_instruction = _EVIDENCE_WITH_DATA.format(evidence_lines=lines)
    else:
        evidence_instruction = _EVIDENCE_EMPTY

    return template.format(
        name=name,
        parameters_instruction=params_instruction,
        evidence_instruction=evidence_instruction,
    )


# ── Common writing rules (appended to all templates) ────────────────────────

WRITING_RULES = """\

Rules:
- One concept per sentence
- No em-dashes as separators
- Use [[wikilinks]] for related concepts
- Cite evidence with [REF:paper_display] markers
- No meta-commentary ("this article covers...")
- 300-500 words for central concepts, 200-300 for peripheral ones
"""
