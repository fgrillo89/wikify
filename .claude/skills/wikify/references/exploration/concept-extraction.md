# Concept Extraction

Concept extraction turns observed corpus text into candidate wiki pages.
It is prompt/reference material, not an exploration strategy.

## Candidate Fields

- `title`: natural Wikipedia-style title.
- `aliases`: abbreviations and alternative names.
- `kind`: `article` or `person`.
- `category`: phenomenon, method, material, device, theory, metric,
  organization, or other.
- `quote`: verbatim evidence span from the observed text.
- `definition`: one-sentence meaning.
- `summary`: what the observed text says about the concept.
- `parameters`: quantitative values with units and conditions.
- `mechanisms`: short mechanism phrases.
- `relationships`: target concept, relation, evidence.
- `equations`: formulas or equations present in supplied context.
- `evidence_figures`: figure ids directly discussed.
- `cited_refs`: citation ordinals directly relevant to the concept.
- `confidence`: extracted, inferred, or ambiguous.
- `score`: 0.0-1.0.

## Rules

- Reuse a canonical title if the workflow supplies one.
- The quote must be a verbatim substring of the observed text.
- Person candidates use `kind="person"` and omit technical parameters.
- Prefer fewer high-value concepts over noisy phrase extraction.
- Flag merge/split ambiguity for the workflow rather than inventing a
  final ontology decision.
