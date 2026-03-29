# Technical Report — Writing Guide

## Exemplary Models

IEEE standards documents, NIST Technical Notes (TN series), NASA Technical Reports,
and DOE/national laboratory technical reports. These share a focus on practical
outcomes, data-heavy presentation, and actionable recommendations.

## Structure

1. **Abstract** — Brief summary of purpose, methods, key findings, and
   recommendations. May also be called "Executive Summary" in longer reports.
2. **Introduction** — Problem statement, scope, objectives, and audience. Unlike a
   research article, explicitly state who the intended readers are.
3. **Background** — Relevant prior work and context. Less exhaustive than an academic
   literature review; focus on what the reader needs to understand the current work.
4. **Methods** — Procedures, equipment, software, standards followed. Emphasize
   reproducibility and traceability.
5. **Results** — Data-heavy. Use tables, figures, and appendices liberally. Present
   results clearly without extensive interpretation.
6. **Analysis** — Interpret results. Compare against specifications, standards, or
   expected performance. Statistical analysis where appropriate.
7. **Conclusions** — Summarize findings. Must be concrete and tied directly to the
   objectives stated in the Introduction.
8. **Recommendations** — Actionable next steps. What should the reader/organization
   do based on these findings? This section is required and must not be vague.

## Key Conventions

- **Audience awareness**: Technical reports often serve mixed audiences (engineers,
  managers, policy makers). Define acronyms, provide context, and use clear tables
  that can stand alone.
- **Executive summary focus**: Many readers will only read the abstract/executive
  summary and recommendations. These sections must be self-contained.
- **Data presentation**: Prefer tables over prose for numerical results. Every table
  and figure must have a descriptive caption.
- **Traceability**: Methods must reference applicable standards (ISO, ASTM, IEEE, etc.)
  and specify instrument models, software versions, and calibration dates.
- **Recommendations must be actionable**: "Further study is needed" is insufficient.
  Specify what study, why, and what resources are required.

## Actionable Instructions for the LLM

- Always include a Recommendations section with numbered, actionable items.
- Results section should lead with tables and figures, not prose.
- Conclusions must map one-to-one back to the objectives in the Introduction.
- Background section should be concise (only what the reader needs), not a full
  literature review.
- Use numbered sections and subsections for easy cross-referencing.
- Define all acronyms on first use; consider including an acronym list for longer
  reports.
