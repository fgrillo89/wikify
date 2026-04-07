# Discovery Engine Alignment

Gap analysis and implementation plan to align Wikify's extraction pipeline with
the Discovery Engine framework (arxiv 2505.17500).

---

## Current State vs Framework

### Element 1: Structured Extraction Template

**Framework**: A Markdown document with modular sections (system scope, objectives,
implementation), containing probes ranging from qualitative descriptions to
quantitative parameter fields with units. The template IS the schema — it defines
what knowledge looks like in this domain.

**What we have**: A flat prompt string hardcoded in `_extract_from_chunk()`:
```python
user_msg = (
    "Extract named concepts from the following text excerpt.\n\n"
    "Return a JSON array where each element has:\n"
    '  "name": ...\n  "type": ...\n  "aliases": ...\n  "definition": ...\n'
)
```

**Gap**: Our "template" is 4 fields. The framework describes a rich schema with
sections for parameters (with units), mechanisms, relationships, comparative
scores, and justifications. We extract the thinnest possible slice of knowledge.

**What to build**: A `distillation_template.md` file that evolves per epoch. The
template is loaded at runtime and injected into the extraction prompt. It defines
what to extract from each publication, organized by aspect:

```markdown
# Extraction Template v{epoch}

## Concepts
For each named concept: canonical name, type, aliases, 25-word definition.

## Parameters
For each quantitative parameter: name, value, unit, conditions, source sentence.
Example: "ALD growth rate: 1.0 A/cycle at 250C substrate temperature"

## Mechanisms
For each causal or process mechanism: description, enabling conditions, evidence.

## Relationships
For each pair of concepts that interact: relation type, direction, evidence.

## Scores
Rate the publication on: methodological rigor (1-5), novelty (1-5), 
reproducibility (1-5). Justify each score with a source sentence.

## Gaps
What knowledge in this text does the template above FAIL to capture?
Describe any concepts, relationships, or findings that don't fit the schema.
```

### Element 2: Source Evidence Linkage

**Framework**: Every extracted component must have "direct textual justifications
from the source document." This is "paramount for building a trustworthy knowledge
base and mitigating potential LLM hallucination."

**What we have**: Concepts are extracted with a definition, but no source quote.
We have no way to verify if a concept was actually mentioned in the text or if
the LLM inferred/hallucinated it. The `SourceCoverage.extraction` field stores
the haiku map output, but that's for article writing, not concept extraction.

**Gap**: No provenance chain from concept -> source sentence -> chunk -> paper.

**What to build**: Add `evidence` field to concept extraction output:

```json
{
  "name": "Atomic Layer Deposition",
  "type": "technique",
  "aliases": ["ALD"],
  "definition": "Sequential self-limiting deposition with atomic thickness control",
  "evidence": "atomic layer deposition (ALD) is likely the most technologically relevant",
  "evidence_location": "paragraph 3"
}
```

Store evidence in `ConceptRecord` or a linked `ConceptEvidence` table. This
enables:
- Hallucination detection (grep source text for the evidence quote)
- Concept quality scoring (concepts with strong evidence rank higher)
- Human auditability (click a concept, see exactly where it came from)

### Element 3: Meta-Probes and Self-Reflection

**Framework**: The template contains meta-probes instructing the LLM to "reflect on
its own capacity to capture the full scope of information" and identify "knowledge
components or relational nuances that the current template fails to represent."

**What we have**: Nothing. Our extraction prompt asks "what do you see?" but never
asks "what couldn't you capture?"

**Gap**: Without meta-probes, the system never learns about its own blind spots.
The AKE plan (Phase 1.4) proposes a gap-reporting follow-up question, but that's
a separate LLM call. The framework integrates it directly into the template.

**What to build**: Add a `## Gaps` section to the extraction template (as shown
above). This is not a separate call — it's part of the same extraction. The LLM
fills in concepts AND reports what it couldn't classify, in one response.

The key insight: the gaps section should be *inside* the template, not bolted on
after. This makes gap reporting part of the standard extraction contract, not an
optional extra.

### Element 4: Self-Consistent Template Refinement Loop

**Framework**: "The template incorporates meta-probes... This feedback,
systematically aggregated across numerous documents, reveals statistical patterns
of misfit or recurring representational gaps. Periodically, this aggregated
feedback drives AI-assisted revisions to the template structure."

The loop: extract -> collect gap feedback -> aggregate -> revise template -> repeat.
Convergence = the template provides "a stable, comprehensive, and minimally
ambiguous schema" for the corpus.

**What we have**: The AKE plan's Phase 1.3 proposes appending corpus statistics to
the prompt ("under-represented types: theory, dataset"). This is a weak form of
adaptation — it adjusts the prompt but doesn't revise the schema structure.

**Gap**: We don't revise what we extract, only how aggressively we look for each
type. The template structure (4 fields: name, type, aliases, definition) never
changes. In the framework, the template might add entirely new probe sections
(e.g., "process parameters", "device architectures") based on gap feedback.

**What to build**: A template revision pipeline that runs between epochs:

```
End of epoch N:
  1. Aggregate all gap reports from extraction
  2. Cluster gap descriptions by embedding similarity
  3. For each cluster with 5+ instances:
     a. Generate a proposed template addition (new probe section)
     b. Test it on 10 sample chunks to verify it captures real knowledge
     c. If yield > threshold: add to template for epoch N+1
  4. Write updated template to distillation_template.md
  5. Log template version in EpochLog
```

Track template convergence separately from wiki convergence:
```
template_delta = |probes_added + probes_removed| / total_probes
```
When `template_delta < 0.01` for 3 consecutive epochs, the template has converged.

### Element 5: Per-Publication Systematic Interrogation

**Framework**: The LLM processes each publication as a whole unit against the full
template. Every publication gets the same structured interrogation.

**What we have**: We process individual chunks (600-token fragments), not whole
publications. Cross-chunk context threading helps, but the LLM never sees the
full publication against the full template.

**Gap**: Chunk-level extraction misses cross-section relationships (e.g., a method
introduced in Methods, applied in Results, discussed in Discussion — three chunks,
one concept). The threading helps within a source but can't capture the full
publication structure.

**What to build**: A two-pass extraction strategy:

**Pass 1a (publication-level)**: For each paper, send the abstract + section
summaries (not full text) to the LLM with the full extraction template. This
gives a broad view of what the publication covers. Output: publication-level
concept list + parameter list + relationship list.

**Pass 1b (chunk-level deepening)**: For concepts identified in 1a that need more
detail, drill into specific chunks. Use the concept-aware pre-filter to select
only the relevant chunks. This is the existing chunk-level extraction, but now
guided by the publication-level extraction.

This mirrors how a human reads: first skim the abstract and structure, then dive
into specific sections for detail.

### Element 6: Quantitative Parameter Extraction

**Framework**: The template includes "specific fields for quantitative parameters
(requiring values and units)" — not just concept names but actual data.

**What we have**: We extract concept names and definitions. We don't extract
parameters like "growth rate: 1.0 A/cycle at 250C" or "ON/OFF ratio: 10^3" or
"endurance: 10^6 cycles". These are buried in the article body text but never
structured.

**Gap**: The most machine-actionable knowledge in a scientific corpus is
quantitative. Our wiki articles mention numbers in prose but don't structure them.

**What to build**: A `ParameterExtraction` model:

```python
class ParameterExtraction(SQLModel, table=True):
    id: int | None
    concept_id: str           # FK -> ConceptRecord
    paper_id: str             # FK -> Paper
    parameter_name: str       # e.g. "growth rate"
    value: str                # e.g. "1.0"
    unit: str                 # e.g. "A/cycle"
    conditions: str           # e.g. "substrate temperature 250C"
    evidence: str             # source sentence
    epoch_extracted: int
```

This table is the most directly machine-useful output of the pipeline. It enables:
- Quantitative comparison across papers ("what growth rates have been reported?")
- Data tables in wiki articles auto-generated from structured parameters
- Anomaly detection (a reported value 10x different from consensus)

### Element 7: VSA/HDC Vector Representation

**Framework**: Uses Vector Symbolic Architectures / Hyperdimensional Computing to
compress structured extractions into a hierarchical vector database. Operations
like binding, unbinding, and superposition enable structured reasoning in vector
space.

**What we have**: Standard sentence-transformer embeddings in ChromaDB. These
capture semantic similarity but not structure. "ALD at 250C" and "ALD at 100C"
have nearly identical embeddings despite being meaningfully different conditions.

**Gap**: Our embeddings lose structure. VSA/HDC preserves it: a concept vector
bound with a parameter vector creates a composite that can be unbound to recover
either component.

**What to build**: This is the most ambitious component. A practical first step:

1. **Structured concept vectors**: Instead of embedding `"Atomic Layer Deposition"`,
   embed a structured string: `"ALD | type:technique | enables:RRAM,HfO2 |
   params:growth_rate=1.0_A/cycle@250C"`. This captures structure in the embedding
   without requiring a full VSA implementation.

2. **Compositional embeddings**: For the Conceptual Nexus Model (AKE Phase 6),
   experiment with simple binding operations: `concept_vector XOR parameter_vector`
   creates a composite. This is a lightweight VSA approach that works with existing
   embeddings.

3. **Full VSA** (future): If compositional embeddings prove valuable, implement
   proper holographic reduced representations (HRR) or MAP vectors. This would
   require a custom embedding layer, not sentence-transformers.

The practical priority is step 1 — structured concept vectors are cheap and
immediately useful for better similarity search and dedup.

---

## Implementation Plan

### Phase 0: Extraction Template Infrastructure

Create the template system that all subsequent phases build on.

**Deliverables:**
- `src/wikify/wiki/template.py` — template loader, versioning, prompt builder
- `data/wiki/_template.md` — the extraction template (versioned per epoch)
- Template v1: concepts + parameters + mechanisms + relationships + gaps
- `_extract_from_chunk()` refactored to use template instead of hardcoded prompt

**Estimated effort:** ~200 LOC, modifies concepts.py

### Phase 1: Source Evidence Linkage

Add provenance to every extraction.

**Deliverables:**
- `ConceptEvidence` model (concept_id, paper_id, chunk_id, evidence_quote)
- Extraction prompt updated to require evidence quotes
- Evidence verification: grep source chunk for the quoted text
- Hallucination flag if evidence quote not found in source

**Estimated effort:** ~150 LOC, new model + concepts.py changes

### Phase 2: Meta-Probes and Gap Reporting

Integrated into the template, not a separate call.

**Deliverables:**
- `## Gaps` section in extraction template
- `ExtractionGap` model (from AKE Phase 5)
- Gap aggregation and clustering per epoch
- Dashboard view of recurring gaps

**Estimated effort:** ~150 LOC, concepts.py + dashboard.py

### Phase 3: Self-Consistent Template Refinement

The feedback loop that makes the template evolve.

**Deliverables:**
- Template revision pipeline (aggregate gaps -> propose additions -> test -> apply)
- Template version tracking in EpochLog
- Template convergence metric (`template_delta`)
- `wikify wiki audit --template` to show template evolution history

**Estimated effort:** ~250 LOC, new template.py functions + epoch.py integration

### Phase 4: Two-Pass Extraction (Publication + Chunk Level)

Replace single-pass chunk extraction with publication-level overview + targeted
chunk deepening.

**Deliverables:**
- `extract_from_publication()` — abstract + section summaries against full template
- Modified `discover_concepts()` to run 1a then 1b
- Publication-level concept list guides chunk-level pre-filtering

**Estimated effort:** ~200 LOC, concepts.py refactor

### Phase 5: Quantitative Parameter Extraction

Extract structured parameters, not just concept names.

**Deliverables:**
- `ParameterExtraction` model
- Template section for parameters (name, value, unit, conditions, evidence)
- Parameter table auto-generated in wiki articles
- `wikify wiki query "growth rate of ALD TiO2"` searches parameter table

**Estimated effort:** ~200 LOC, new model + concepts.py + builder.py

### Phase 6: Structured Concept Vectors

Richer embeddings that capture structure, not just semantics.

**Deliverables:**
- Structured embedding strings for concepts (type + relations + params)
- Improved similarity search and dedup using structured embeddings
- Experimental compositional embeddings (concept XOR parameter binding)

**Estimated effort:** ~150 LOC, embeddings.py + concepts.py

---

## Sequencing

```
Phase 0 (template infrastructure)
    |
    +---> Phase 1 (evidence linkage)
    |         |
    |         v
    +---> Phase 2 (meta-probes + gaps)
    |         |
    |         v
    |     Phase 3 (self-consistent refinement loop)
    |
    +---> Phase 4 (two-pass extraction)
    |
    +---> Phase 5 (parameter extraction)
              |
              v
          Phase 6 (structured vectors)
```

Phase 0 must come first. Phases 1, 2, 4 can start in parallel after Phase 0.
Phase 3 depends on Phase 2. Phase 6 depends on Phase 5.

### Integration with Adaptive Knowledge Engine Plan

This document extends the AKE plan (`adaptive-knowledge-engine.md`). The mapping:

| AKE Phase | This Document |
|-----------|--------------|
| Phase 1.3 (adaptive prompt) | Superseded by Phase 3 (template refinement) |
| Phase 1.4 (gap reporting) | Implemented by Phase 2 (meta-probes) |
| Phase 5 (schema evolution) | Implemented by Phase 3 (template refinement) |
| Phase 6 (Conceptual Nexus Model) | Extended by Phase 5+6 (parameters + vectors) |

The AKE phases 2 (UCB scoring), 3 (contradiction exploration), and 4 (hierarchy)
remain as planned — they operate on the output of the extraction pipeline, not
on the extraction itself.

---

## References

- [The Discovery Engine (2025)](https://arxiv.org/html/2505.17500v1) — self-consistent
  refinement, meta-probes, Conceptual Nexus Tensor
- [VSA/HDC for Structured Knowledge (Kanerva 2009)](https://doi.org/10.1007/s10339-009-0258-6) —
  holographic reduced representations, binding operations
- [AlphaEvolve (2025)](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) —
  evolutionary template refinement analogy
