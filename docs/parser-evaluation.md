# ScholarForge — Document Parser & Ingestion Evaluation

Comparison of parsing options across all supported file types.

## Requirements

- Parse 200+ academic PDFs into clean markdown
- Handle DOCX, PPTX, Excel, CSV, TXT, Parquet alongside PDFs
- Handle multi-column layouts, tables, figures, equations
- Run locally (no cloud APIs)
- Windows 11 compatible
- Fast enough for batch processing

## Source Categories

ScholarForge handles four distinct source categories, each with different parsing
needs, vault note types, and graph roles.

### 1. Literature (external, read-only, citable)

| Format | Parser | Notes |
|---|---|---|
| PDF | pymupdf4llm / LiteParse / Marker | Primary academic format |
| DOCX | python-docx / LiteParse | Preprints, reports, theses |
| PPTX | python-pptx / LiteParse | Conference talks, lecture slides |

- **Vault path**: `vault/papers/`
- **Graph role**: Source nodes — referenced by user docs, linked to concepts/methods/authors
- **Ingestion**: Full pipeline (parse → extract metadata → chunk → embed → create vault note)

### 2. User Documents (user-authored, evolving)

| Format | Parser | Notes |
|---|---|---|
| DOCX | python-docx | Drafts, reports, grant proposals |
| PPTX | python-pptx | User's own presentations |
| TXT | direct read | Notes, memos, raw text |

- **Vault path**: `vault/docs/`
- **Graph role**: Output/working nodes — link TO literature and data, evolve over time
- **Ingestion**: Parse → chunk → embed (no metadata extraction, user provides context)
- **Key difference**: These are mutable — re-ingestion on file change, not dedup by hash

### 3. Data Files (structured, evidence)

| Format | Parser | Notes |
|---|---|---|
| CSV | polars | Tabular data |
| Excel (.xlsx, .xls) | polars (with xlsx2csv or openpyxl backend) | Spreadsheets (multi-sheet) |
| Parquet | polars/pyarrow | Columnar analytics data |
| TSV | pandas | Tab-separated |

- **Vault path**: `vault/data/`
- **Graph role**: Evidence nodes — supports findings, linked to papers and user docs
- **Ingestion**: NOT parsed to markdown. Generate a **dataset card** note with:
  - Schema (columns, types, ranges)
  - Summary statistics (row count, null rates, distributions)
  - Preview (first N rows as markdown table)
  - User-provided description (optional)
- **No chunking**: Data files are queryable, not readable prose
- **No embedding of content**: Embed the dataset card description instead

### 4. Presentations as Literature vs User Docs

PPTX files are ambiguous — they could be conference talks (literature) or the user's own slides.

- **Detection heuristic**: If ingested from a `papers/` or `references/` input directory → literature
- **CLI flag**: `scholarforge ingest slides.pptx --source-type literature|user`
- **Default**: user document (safer assumption)

## Format-Specific Parsing

### DOCX Parsing

**python-docx** (already in dependencies):
- Extracts paragraphs with style info (Heading 1, Normal, etc.)
- Tables extracted as structured data
- Images extracted via `document.inline_shapes` and relationship parts
- No layout detection needed (DOCX is already structured)

**LiteParse alternative**:
- Converts DOCX → PDF via LibreOffice, then parses as PDF
- Loses DOCX structure (headings, styles) in the conversion
- Only useful if python-docx output is poor

**Recommendation**: python-docx for DOCX (preserves structure natively). LiteParse only as fallback.

### PPTX Parsing

**python-pptx** (already in dependencies):
- Extracts slide-by-slide: title, body text, notes, tables, images
- Each slide → a section in the output markdown
- Speaker notes are valuable context — include as blockquotes

**Output structure**:
```markdown
## Slide 1: Introduction
Content from slide body...

> **Speaker notes**: Additional context from presenter notes...

## Slide 2: Methods
...
```

**LiteParse alternative**:
- Converts PPTX → PDF → spatial text
- Loses slide structure and speaker notes
- Not recommended for PPTX

**Recommendation**: python-pptx for PPTX (preserves slide structure + speaker notes).

### Data File Parsing (CSV, Excel, Parquet)

**No text parsing needed** — these are structured data. Generate a dataset card:

```python
import polars as pl

def create_dataset_card(file_path: Path) -> dict:
    """Generate dataset card metadata — no LLM needed."""
    df = pl.read_csv(file_path)  # or read_excel, read_parquet
    return {
        "format": file_path.suffix,
        "rows": df.height,
        "columns": df.columns,
        "dtypes": {col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)},
        "null_counts": {col: df[col].null_count() for col in df.columns},
        "numeric_stats": df.describe().to_dicts(),
        "preview": df.head(5),
        "memory_mb": df.estimated_size("mb"),
    }
```

**Excel-specific**: Multi-sheet workbooks → one dataset card per sheet, or one card with sheet index.
`pl.read_excel(path, sheet_name="Sheet1")` or iterate sheets.

**Parquet-specific**: Schema available without loading full file (`pl.read_parquet_schema()`).

### TXT Parsing

- Direct read, no library needed
- Chunk by paragraphs (double-newline split) or fixed-size
- Source type determined by CLI flag or input directory

## Registry Expansion

The current `ingest/registry.py` maps extensions to parsers. Expanded:

```python
PARSERS = {
    # Literature / text documents
    ".pdf": parse_pdf,       # pymupdf4llm (default) or LiteParse
    ".docx": parse_docx,     # python-docx
    ".pptx": parse_pptx,     # python-pptx
    ".txt": parse_txt,       # direct read

    # Data files → dataset cards (not text parsing)
    ".csv": create_dataset_card,
    ".tsv": create_dataset_card,
    ".xlsx": create_dataset_card,
    ".xls": create_dataset_card,
    ".parquet": create_dataset_card,
}
```

## Dependencies (additions)

```
polars                         # Data file parsing (CSV, Excel, Parquet) — preferred over pandas
pyarrow                        # Parquet support + efficient schema reading
```

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
