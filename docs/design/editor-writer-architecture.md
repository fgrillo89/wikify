# Editor-Writer Architecture

## Core Insight

The editor is not an optional step. It is the central decision-maker in
both scripted and autonomous modes. The difference between E/M/X and O
is who controls sampling:

- **Scripted (E/M/X)**: Sampler picks chunks by rule. Editor decides
  write-readiness and produces briefs.
- **Autonomous (O)**: Editor also controls sampling, guided by dossier
  gaps and corpus profile.

## Three Roles

### Extractor (cheap, high-volume)

Reads one chunk, produces a rich dossier entry per concept:
- title, aliases, kind, category
- definition (one sentence)
- summary (2-3 sentences: what this chunk says about the concept)
- parameters (quantitative values with units and conditions)
- mechanisms (how it works, short phrases)
- relationships (target concept, relation type, evidence)
- quote (verbatim evidence anchor)

Uses extract_v2.yaml prompt. Default for all strategies.

### Editor (medium model)

Reads the accumulated dossier for each concept plus the wiki index.
Decides:

1. **Write-readiness**: Does this dossier have enough substance for a
   page? (has_substance heuristic + editorial judgment)
2. **Brief**: Section plan, tone, register, comparisons, figures,
   zone labels (established/contested/frontier)

The editor sees:
- Compacted dossier (for_editor() projection)
- Existing wiki page titles (for cross-referencing)
- Corpus profile summary (communities, importance scores)

### Writer (medium/large model)

Receives the brief and writes the article. Focuses on prose craft.
The writer sees:
- Editor's brief (section-by-section instructions)
- Full chunk texts (evidence_v2) for synthesis context
- Style guide, field guide, artifact template
- Neighbor page summaries (lead paragraphs)

## Dossier Management

### Structure

```
Dossier (per concept, persists to <bundle>/_dossiers/<id>.json):
  page_id, title, aliases, kind, category
  entries: list[DossierEntry]     # raw evidence from extraction
  canonical_definition: str       # consolidated (after compaction)
  canonical_summary: str          # consolidated
  merged_parameters: list[dict]   # deduplicated
  merged_mechanisms: list[str]    # deduplicated
  merged_relationships: list[dict]
  n_source_docs: int
  n_compactions: int
```

### Lifecycle

1. **Accumulation**: Each extract call adds DossierEntry objects.
   Entries carry chunk_id, doc_id, quote, definition, summary,
   parameters, mechanisms, relationships, section_type, figure_ids.

2. **Compaction**: When entries exceed threshold (default 10), a
   cheap model call consolidates: dedup definitions, merge params,
   rank evidence by information value. The top 5-8 entries survive.

3. **Persistence**: Dossiers save to disk after each accumulation.
   Incremental runs (--feed) load existing dossiers and add to them.

4. **Readiness check**: has_substance heuristic (2+ entries, 1+ source
   docs, has definition or summary). Editor can override.

5. **Editor projection**: for_editor() returns a compact dict with
   the best definition, merged parameters, and ranked evidence.

## Strategy Axes

| | E (breadth) | M (balanced) | X (depth) | O (autonomous) |
|---|---|---|---|---|
| Sampling | pagerank, broad | Levy + gap | similarity walk | editor-directed |
| Extractor | haiku | haiku | sonnet | editor chooses |
| Compactor | haiku | haiku | sonnet | haiku |
| Editor | rule-based | sonnet | sonnet | IS the orchestrator |
| Writer | haiku | sonnet | opus | editor chooses |
| Dossier depth | shallow | moderate | deep | adaptive |

In E mode, the editor can be a simple rule: "if has_substance, write."
In O mode, the editor IS the orchestrator agent with full corpus profile.

## Pipeline Flow

```
1. LOAD corpus (docs, chunks, vectors, graph, images)
2. SAMPLE chunks (sampler picks by strategy)
3. EXTRACT per chunk → DossierEntry → DossierStore.save()
   - Periodic compaction when entries > threshold
4. CANONICALIZE candidates → WikiPage skeletons
5. EDITOR reads dossiers + wiki index
   - Decides which concepts are ready for writing
   - Produces EditorBrief per greenlit concept
6. WRITE per page (follows brief)
7. CROSSLINK + write pages to disk
8. BUILD wiki index
```

## Corpus Profile (orchestrator input)

The corpus profile gives the editor/orchestrator a bird's-eye view:
- Document importance (PageRank on unified doc graph)
- Node roles (core/bridge/peripheral)
- Communities (Louvain on doc similarity + citation graph)
- Hub chunks (most central by similarity degree)
- Topic vocabulary

All computed from the materialized graph -- no model calls needed.
Works for any document type (papers, blogs, manuals) because
importance comes from the unified graph, not citations alone.
