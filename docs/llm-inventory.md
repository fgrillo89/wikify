# ScholarForge — LLM Dependency Inventory

Every step in the pipeline assessed for whether it needs an LLM or can be deterministic.

## Summary

Out of ~25 pipeline steps, only **3 genuinely require an LLM**. Everything else is either
already deterministic or can be handled with embedding models / heuristics.

## Genuinely Irreplaceable LLM Steps

### 1. TOC/Structure Planning (`generate/planner.py`)
- **What it does**: Reads paper summaries, decides document structure, assigns papers to sections
- **Why LLM**: Requires understanding cross-paper themes and constructing a logical academic argument flow
- **Optimization**: Use Louvain clustering to reduce input from 200 papers to ~10-15 cluster descriptions

### 2. Section Prose Writing (`generate/writer.py`)
- **What it does**: Synthesizes information from multiple papers into coherent academic prose
- **Why LLM**: Novel connected paragraphs from source chunks is the core LLM use case
- **Token budget**: 40% source, 20% figures, 10% structure, 30% output

### 3. Supports/Contradicts Link Detection (`vault/linker.py`)
- **What it does**: Determines if Paper A's findings support or contradict Paper B's
- **Why LLM**: Requires reading and comparing specific claims from both papers
- **Priority**: Low — optional for MVP, most valuable link types (cites, hasTopic) are deterministic

## Already Deterministic — No Changes Needed

| Step | Module | Approach |
|---|---|---|
| PDF → Markdown | `ingest/pdf.py` | pymupdf4llm |
| Title extraction | `extract/metadata.py` | PDF metadata → first heading → filename |
| Author extraction | `extract/metadata.py` | PDF metadata split on `,` `;` `and` |
| Abstract extraction | `extract/metadata.py` | Regex on `## Abstract` heading |
| DOI extraction | `extract/metadata.py` | Regex `10.\d{4,}/...` |
| Year extraction | `extract/metadata.py` | PDF metadata date regex |
| Chunking | `extract/chunker.py` | Section-aware paragraph splitting + tiktoken |
| Figure extraction | `extract/figures.py` | fitz `get_images()` + SHA256 dedup |
| Citation detection | `extract/chunker.py` | Regex `[Author YYYY]` / `[N]` |
| Vault frontmatter | `vault/writer.py` (planned) | Jinja2 template fill from SQLite |
| Citation formatting | `export/bibliography.py` (planned) | bibtexparser + CSL-JSON |
| Citation existence check | `export/bibliography.py` (planned) | SQL lookup against Paper/Citation tables |
| .docx/.tex rendering | `export/` (planned) | python-docx + Jinja2 templates |
| Context assembly | `generate/context.py` (planned) | Embedding-ranked chunk selection + token packing |
| Figure selection | `generate/figures_gen.py` (planned) | section_path + topic embedding match |

## Should Upgrade to Embedding Models (Not LLMs)

These currently use weak heuristics or are not yet implemented. An embedding model
(sentence-transformers, already in dependencies) is the right tool — not an LLM API call.

| Step | Current | Upgrade To |
|---|---|---|
| Topic/concept extraction | Not implemented | KeyBERT (uses sentence-transformers) |
| Related paper linking | Not implemented | ChromaDB cosine search |
| Caption-to-figure association | Text prefix match | fitz bounding box spatial proximity |
| Method/dataset detection | Not implemented | NER (spaCy `en_core_sci_lg`) + fuzzy match |

## LLM-Deferrable with Good-Enough Substitutes

These are planned as LLM steps but can use deterministic substitutes for the MVP:

| Step | LLM Approach | Deterministic Substitute |
|---|---|---|
| Vault note Summary | LLM-generated 3-sentence synthesis | Use the abstract (already extracted) |
| Key Contributions section | LLM summarization | Extract sentences with "we propose/introduce" markers from intro |
| Findings section | LLM summarization | Extract sentences with result markers from Results/Conclusion |
| Methodology section | LLM synthesis | Copy content from extracted "Methods" section directly |

## One Genuine Multimodal LLM Task

**Chart/figure data extraction** (`Figure.extracted_data` field):
Converting a chart image into numeric data requires a vision model (Claude with image input,
or specialized model like DePlot). This is optional but valuable for comparing quantitative
results across papers in a literature review.

## Cost Implications

For 200 papers with only the 3 required LLM steps:

- **Ingestion (0 LLM calls)**: All 200 papers processed deterministically
- **Vault creation (0 LLM calls for MVP)**: Abstracts as summaries, heuristic extraction for contributions/findings
- **Linking (0 LLM calls for core links)**: Citations, authors, topics all deterministic/embedding-based
- **Generation (LLM calls here only)**:
  - 1 planner call (~4K tokens input for 200 papers via cluster summaries)
  - N writer calls (one per section, ~10-20 for a literature review)
  - Total: ~15-25 LLM calls for the entire pipeline

This is dramatically fewer LLM calls than architectures that use LLM for extraction, summarization, and linking.
