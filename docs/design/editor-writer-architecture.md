# Editor-Writer Architecture

## Problem

The current pipeline produces structurally correct but shallow wiki articles
because:

1. **Thin extraction**: Each chunk yields only concept names + one quote.
   The writer has no definitions, parameters, mechanisms, or relationships
   to work with.
2. **No editorial judgment**: The pipeline mechanically maps chunks to
   concepts and hands them to the writer with flat evidence lists. No one
   decides what the article should emphasize, what structure fits this
   specific topic, or how this article relates to others in the wiki.
3. **Writer in isolation**: Each article is written without awareness of
   other articles, leading to repeated explanations and no comparative depth.

## Design: Three Roles

### 1. Extractor (cheap, high-volume)

Produces a **rich dossier** per chunk, not just concept names.

```
ExtractedDossier:
  concepts:
    - title, aliases, kind, category
    - definition: str          # one-line definition (NEW)
    - quote: str               # verbatim evidence quote
    - summary: str             # 2-3 sentence summary of what the chunk says about this concept (NEW)
    - parameters: list         # quantitative values with units/conditions (NEW)
    - mechanisms: list[str]    # how it works / how it's applied (NEW)
    - relationships: list      # (target_concept, relation_type, evidence) (NEW)
  figures_referenced: list     # which figures this chunk discusses
  section_type: str            # abstract/methods/results/conclusion (from chunk metadata)
```

The dossier gives the editor material to work with. A concept like
"Atomic Layer Deposition" might accumulate across 10 chunks:
- 3 definitions from different papers
- 5 parameters (growth rate, temperature, precursor, thickness, cycle count)
- 4 mechanisms (self-limiting reaction, nucleation, purge, plasma enhancement)
- 8 relationships (ENABLES conformal coating, USED-IN HfO2 deposition, etc.)

### 2. Editor (medium model, runs once per page)

The editor is the orchestrator. It reads ALL dossiers for a concept,
decides the article structure, and writes a **brief** for the writer.

```
EditorBrief:
  page_id: str
  title: str
  register: str              # "academic" | "applied" | "tutorial"
  tone: str                  # specific tone guidance
  sections: list[BriefSection]
  comparative_notes: str     # how this differs from related concepts
  figures_to_embed: list     # which figures to include and where
  max_length: int            # target article length
```

Each BriefSection contains:
```
BriefSection:
  heading: str               # "## Mechanism" or "## Device Performance"
  instruction: str           # "Explain the conductive filament model. Compare HfO2 and TaOx."
  evidence: list             # the specific dossier entries to cite
  zone: str                  # "established" | "contested" | "frontier"
  parameters_to_include: list
```

The editor decides:
- What sections this specific article needs (not a template)
- Which evidence goes in which section
- What the tone and register should be
- What comparisons to draw
- Which figures to embed
- What's established consensus vs. open questions

### 3. Writer (medium/large model, runs once per page)

Receives the brief and writes the article. The writer's job is prose
craft, not editorial judgment. It follows the brief's section plan,
cites the specified evidence, embeds the specified figures, and matches
the specified register.

The writer sees:
- The editor's brief with section-by-section instructions
- Full chunk texts for each piece of evidence (not just quotes)
- The style guide, field guide, and artifact template
- Lead paragraphs of neighbor articles (for cross-referencing)

## Fitting the 3 Strategy Axes

The three strategies control **sampling** and **budget allocation**. The
editor-writer roles fit cleanly into each:

### E (Explore) — breadth, cheap

- **Extractor**: haiku, high volume, pagerank sampling
- **Editor**: haiku, lightweight briefs (just section headings + evidence assignment)
- **Writer**: haiku, follows brief mechanically
- **Result**: many pages, thin but structurally correct

### M (Mixed) — balanced

- **Extractor**: haiku, Levy walk + coverage gap
- **Editor**: sonnet, real editorial judgment on structure/tone/comparisons
- **Writer**: sonnet, follows brief with domain knowledge
- **Result**: moderate page count, good quality

### X (Exploit) — depth, quality ceiling

- **Extractor**: sonnet, similarity walk, deeper dossiers
- **Editor**: sonnet/opus, full editorial pass with comparative analysis
- **Writer**: sonnet/opus, follows brief with synthesis across sources
- **Result**: fewer pages, high quality

The strategy axes map naturally:

| Axis          | E (Explore) | M (Mixed)   | X (Exploit) |
|---------------|-------------|-------------|-------------|
| Extractor     | haiku       | haiku       | sonnet      |
| Editor        | haiku       | sonnet      | sonnet/opus |
| Writer        | haiku       | sonnet      | sonnet/opus |
| Dossier depth | names+quotes| +definitions+params | +mechanisms+relationships |
| Brief depth   | template    | per-page judgment | comparative analysis |

### O (Orchestrator) — agent-driven

A fourth mode where the orchestrator agent makes ALL decisions:

- **No fixed sampling**: the orchestrator reads the corpus profile,
  decides which papers to read first, which chunks to prioritize
- **No fixed extract/write split**: the orchestrator decides when it has
  enough material for a page and triggers the write
- **Iterative refinement**: the orchestrator can read a written page,
  decide it needs more evidence, go back to extract, then rewrite
- **Cross-page coordination**: the orchestrator maintains a mental model
  of the whole wiki and steers toward coverage and coherence

The orchestrator script:

```
1. Profile the corpus (topics, size, field)
2. Decide: what are the 20-30 most important concepts?
3. For each priority concept:
   a. Identify the 5-10 most relevant chunks
   b. Extract rich dossiers from those chunks
   c. Assess: do I have enough material? If not, find more chunks.
   d. Write an editor brief for this concept
   e. Send to writer
   f. Review the result: does it meet quality bar?
   g. If not, add more evidence or rewrite
4. After core concepts: fill gaps, add cross-references
5. Final pass: consistency check across all pages
```

## Pipeline Changes

### ExtractRequest / ExtractResponse

Add to ExtractedConcept:
- `definition: str = ""`
- `summary: str = ""`
- `parameters: list[dict] = []`  (each: name, value, unit, conditions)
- `mechanisms: list[str] = []`
- `relationships: list[dict] = []`  (each: target, relation_type, evidence)

The extractor prompt changes from "list concepts and pick a quote" to
"for each concept, provide a definition, summarize what this chunk says
about it, extract any quantitative parameters, note how it works, and
identify relationships to other concepts."

### New: EditorBrief model + editor step in pipeline

Between canonicalize and write, the pipeline runs an editor pass:
1. Group all dossier entries by concept
2. For each concept, call the editor with all accumulated material
3. Editor returns a brief
4. Writer receives the brief instead of flat evidence

### WriteRequest changes

- Add `brief: EditorBrief` (the editor's structured instructions)
- Add `chunk_texts: dict[str, str]` (full chunk text for each evidence item)
- Add `neighbor_summaries: list[dict]` (lead paragraph + title of related pages)

### Prompt changes

- `extract_v2.yaml`: richer extraction prompt requesting definitions, params, mechanisms
- `edit_v1.yaml`: new editor prompt that reads dossiers and writes briefs
- `write_v2.yaml`: writer prompt that follows the editor's brief

## Migration Path

1. Add new fields to ExtractedConcept with defaults (backwards compatible)
2. Write extract_v2.yaml prompt
3. Add EditorBrief model and edit_v1.yaml prompt
4. Add editor step to pipeline between canonicalize and write
5. Update write_v2.yaml to consume the brief
6. Update drain skills to handle the richer schemas
7. Run on mvp20 corpus and compare quality
