# ScholarForge — PDF Parser Evaluation

Comparison of document parsing options for the ingestion pipeline.

## Requirements

- Parse 200 academic PDFs into clean markdown
- Handle multi-column layouts, tables, figures, equations
- Run locally (no cloud APIs)
- Windows 11 compatible
- Fast enough for batch processing

## Parser Comparison

### pymupdf4llm (current)

- **Approach**: Rule-based, built on pymupdf/fitz
- **Output**: Structured markdown (headings, paragraphs, lists)
- **Strengths**: Fast, local, zero setup, good figure extraction via fitz, Python-native
- **Weaknesses**: Struggles with multi-column layouts (text interleaving), complex tables extracted as garbled text, equations may not survive cleanly
- **Dependencies**: pymupdf only
- **Windows**: Full support
- **Install**: `uv add pymupdf pymupdf4llm`
- **Verdict**: Good 80% solution. Currently implemented in `ingest/pdf.py`.

### LiteParse (run-llama/liteparse) — EVALUATING

- **Approach**: Spatial layout preservation — projects text onto a grid, preserving column/table positioning without trying to detect structure
- **Output**: Spatial text with bounding boxes (JSON or text). NOT traditional markdown — relies on LLMs understanding spatial formatting natively
- **Philosophy**: "LLMs already know how to read a table" — no complex table-detection pipeline
- **Strengths**:
  - Layout-aware: handles multi-column, tables via spatial positioning
  - Fast: optimized for real-time agent pipelines
  - Broad format support: PDF, DOCX, XLSX, PPTX, images (via LibreOffice/ImageMagick conversion)
  - Built-in OCR (Tesseract.js) for scanned documents
  - Screenshot generation for multimodal LLM fallback
  - Open-source core of LlamaParse
  - Apache 2.0 license
- **Weaknesses**:
  - TypeScript-native (Node.js) — Python wrapper exists (`pip install liteparse`) but adds Node.js dependency
  - Output is spatial text, not structured markdown — downstream chunking needs adaptation
  - OCR quality limited by Tesseract (PaddleOCR/EasyOCR available as HTTP servers)
  - Very new (first release March 19, 2026)
- **Dependencies**: Node.js runtime, optional LibreOffice (Office docs), optional ImageMagick (images)
- **Windows**: Supported
- **Install**: `pip install liteparse` or `npm i -g @llamaindex/liteparse`
- **Python API**:
  ```python
  import liteparse
  result = liteparse.parse("paper.pdf")
  print(result.text)
  ```
- **CLI**:
  ```bash
  lit parse paper.pdf
  lit parse paper.pdf --format json -o output.json
  lit screenshot paper.pdf -o ./screenshots  # for multimodal LLM fallback
  ```
- **Verdict**: Promising for multi-column academic papers. The spatial approach avoids table-detection failures. Worth testing against pymupdf4llm on sample papers. The screenshot fallback is useful for complex layouts. Concern: Node.js dependency in a Python project.

### Marker (VikParuchuri/marker)

- **Approach**: ML-based layout detection + OCR
- **Output**: Clean markdown with tables and equations
- **Strengths**: Excellent multi-column + table support, good equation handling, local
- **Weaknesses**: GPU recommended for speed, heavier dependencies (PyTorch), slower on CPU
- **Windows**: Supported
- **Verdict**: Best accuracy for complex academic layouts, but heavy. Good fallback for papers pymupdf4llm can't handle.

### Docling (IBM)

- **Approach**: ML-based document understanding
- **Output**: Structured markdown/JSON
- **Strengths**: Good table/figure detection, handles diverse layouts
- **Weaknesses**: Heavier install, newer/less battle-tested, IBM ecosystem
- **Windows**: Supported
- **Verdict**: Strong alternative to Marker with similar tradeoffs.

### GROBID

- **Approach**: ML-based, specialized for academic papers
- **Output**: TEI XML (structured academic format)
- **Strengths**: Best-in-class for academic paper structure (headers, refs, sections, metadata)
- **Weaknesses**: Requires Java server, slower, overkill for simple PDFs, complex setup
- **Windows**: Requires Docker or Java
- **Verdict**: Gold standard for academic parsing accuracy, but heavy infrastructure. Consider for metadata enrichment only.

### Unstructured.io

- **Approach**: Hybrid (rule-based + ML), broad format support
- **Output**: Structured elements (JSON)
- **Strengths**: Handles many formats (PDF, DOCX, PPTX, HTML, email), partitioning strategies
- **Weaknesses**: Large dependency tree, some features need API, slower
- **Windows**: Partial support
- **Verdict**: Best for format diversity, overkill for PDF-focused pipeline.

## Recommendation

**Hybrid approach** — use multiple parsers based on document complexity:

1. **Default**: pymupdf4llm (fast, handles 80% of papers well)
2. **Multi-column/tables**: LiteParse spatial output (let LLM interpret layout)
3. **Complex layouts**: Marker (ML-based, highest accuracy, slower)
4. **Metadata enrichment**: CrossRef/Semantic Scholar API for clean title/authors/DOI

The parser could be selectable per-document or auto-detected based on layout complexity
(e.g., detect multi-column via page width analysis).

## Testing Plan

To evaluate, run all parsers on the same 10 diverse papers:
- Single-column journal paper
- Two-column IEEE/ACM paper
- Paper with complex tables
- Paper with many equations
- Scanned/OCR-needed paper
- Paper with multi-panel figures

Compare: text completeness, structure preservation, table accuracy, processing time.

## Sources

- [LiteParse GitHub](https://github.com/run-llama/liteparse)
- [LiteParse blog post](https://www.llamaindex.ai/blog/liteparse-local-document-parsing-for-ai-agents)
- [LiteParse on PyPI](https://libraries.io/pypi/liteparse) — v1.2.1, released 2026-03-28
- [MarkTechPost coverage](https://www.marktechpost.com/2026/03/19/llamaindex-releases-liteparse-a-cli-and-typescript-native-library-for-spatial-pdf-parsing-in-ai-agent-workflows/)
