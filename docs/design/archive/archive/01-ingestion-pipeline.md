# Ingestion Pipeline

## What it does
Takes a PDF/DOCX/PPTX and turns it into structured data: metadata, chunks, figures, citations, vault notes.

## Two modes

**Single file** (`wikify ingest paper.pdf`):
1. Parse file -> markdown text (~2s for PDF)
2. Extract metadata (title, authors, abstract, DOI, year)
3. Chunk text into ~600-token sections
4. Extract figures, citations, figure/table references
5. Persist to SQLite
6. Run fast incremental steps for this paper only (topics, embedding, k-NN)
7. Spawn background thread to refresh all cross-paper signals

**Batch** (`wikify ingest ./papers/ --parallel`):
1. Parse all PDFs in parallel (ProcessPoolExecutor)
2. Persist sequentially (SQLite isn't concurrent)
3. Run ONE batch refresh at the end (not N individual refreshes)

## Key decisions

- **Skip unchanged files**: SHA-256 hash of file bytes = paper ID. If hash exists in DB, skip.
- **OCR fallback chain**: pymupdf4llm -> fitz raw text -> RapidOCR. Auto-detected by checking if >30% of markdown output is image placeholders.
- **Background refresh**: Single-file ingestion does O(1) sync work then spawns a daemon thread for corpus-wide refresh. User sees instant feedback.

## Where the code lives
- `ingest/registry.py` — orchestration, mode selection, batch steps
- `ingest/pdf.py` — PDF parsing + persistence
- `ingest/docx.py`, `ingest/pptx.py` — Office format parsing
