# Write Constraints

Committed pages are Wikipedia-style encyclopedia pages.

Rules:

- Natural title, not a prefixed concept id.
- Full prose article or person page, not a stub unless a workflow
  explicitly defines a provisional mode.
- No visible `[[wikilinks]]`; links live in the page `links` field.
- No corpus meta-commentary.
- Final `## References` section with grounded evidence definitions.
- Person pages degrade gracefully when `author_context` is missing.

The validator enforces structural and quote-grounding constraints.
