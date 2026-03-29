# Section Classification

## The problem

Section headings vary wildly across papers:
- "III. RESULTS AND DISCUSSION", "3. Results and discussion", "RESULTS AND DISCUSSION"
- "II. EXPERIMENTAL DETAILS", "Methods", "2. Experimental details"
- "■[CONCLUSIONS]", "V. CONCLUSION", "VI.CONCLUDING REMARKS"

This makes cross-paper queries impossible without normalization.

## Solution: keyword-based classifier

A regex classifier maps raw headings to canonical types. No transformer needed —
academic section names follow predictable patterns.

### Canonical types

| Type | Matches | Count (20 papers) |
|---|---|---|
| `introduction` | introduction | 24 chunks |
| `methods` | method(s), experimental, fabrication, procedure, characterization, deposition | 27 chunks |
| `results` | result(s), results and discussion | 45 chunks |
| `discussion` | standalone discussion | 3 chunks |
| `conclusion` | conclusion, concluding, summary | 17 chunks |
| `abstract` | abstract | 4 chunks |
| `references` | references, bibliography | 69 chunks |
| `acknowledgments` | acknowledg* | 13 chunks |
| `appendix` | appendix, supplementary, supporting information | 6 chunks |
| `background` | background, literature review, related work | 0 chunks |
| `body` | everything else (topic-specific sections) | 247 chunks |

### Classification steps

1. **Clean heading**: strip `**bold**`, `■[]` artifacts, Roman numerals (`III.`), numbers (`3.2.`)
2. **Match against patterns**: first regex match wins (ordered by specificity)
3. **Section path**: for dotted paths like `3.Results.3.1.IV curves`, classify each component, keep deepest non-body match

### "body" is not a failure

247 out of 455 chunks are "body" — that's correct. These are the topic-specific sections
like "Memristor Device Physics", "ALD Process Parameters", "Neural Network Architecture"
that don't fit IMRaD categories. They're the actual research content.

## How it's used

### MCP tool: `get_sections(section_type, paper_pattern?)`

Enables queries like:
- `get_sections("conclusion")` → all conclusions across the corpus
- `get_sections("methods", "Kim 2021")` → methods section of a specific paper
- `get_sections("introduction")` → all introductions for a literature overview

### Chunk model

`Chunk.section_type` field (string) stores the canonical type. Set during chunking
by calling `classify_section_path(section_path)`.

### Backfill

Existing chunks get their `section_type` via ALTER TABLE + backfill script.
New chunks get it automatically during ingestion.

## Why not a transformer?

The keyword classifier gets 14/14 on our test cases. Academic section names are
highly predictable — the vocabulary is small and stable across fields. A transformer
would add model loading time (~2s), inference time per heading, and a dependency,
all for marginal accuracy gain on an already-solved problem.

If domain-specific sections need classification (e.g., "Device Fabrication" → methods),
add keywords to the pattern list. It's a 1-line change.

## Where the code lives

- `extract/section_classifier.py` — classifier + SectionType enum
- `extract/chunker.py` — calls classifier during chunk creation
- `store/models.py` — `Chunk.section_type` field
- `mcp_server.py` — `get_sections` tool
