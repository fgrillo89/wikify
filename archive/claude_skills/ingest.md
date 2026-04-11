# /ingest — Build or expand the ScholarForge corpus

You are a corpus builder. Your job is to ingest academic papers (PDFs, DOCX, PPTX) into the ScholarForge knowledge base and verify the result.

## Ingestion

### Single file
```python
from pathlib import Path

from scholarforge.ingest import ingest_file

print(ingest_file(Path(r"C:\path\to\paper.pdf")))
```

### Directory (all supported files)
```bash
cd C:/dev/scholarforge && uv run scholarforge ingest "path/to/papers/" --parallel
```

### Supported formats
- `.pdf` (with OCR fallback for scanned papers)
- `.docx`
- `.pptx`

## What happens during ingestion

1. **Parse** — extract text, metadata (title, authors, year, DOI), section structure
2. **Chunk** — split into ~600-token semantic chunks
3. **Extract** — figure/table references, bibliography entries
4. **Embed** — summary embeddings into ChromaDB for semantic search
5. **Graph** — citation matching, k-NN similarity, bibliographic coupling
6. **Vault** — generate Obsidian notes (papers, authors, topics)
7. **BibTeX** — update `data/library.bib`

## After ingestion

Verify the corpus:
```python
from scholarforge.agent.tools import get_corpus_summary
print(get_corpus_summary())
```

```python
from scholarforge.agent.tools import list_papers
print(list_papers())
```

Check graph connectivity:
```python
from scholarforge.agent.tools import get_graph_metrics
print(get_graph_metrics())
```

## Refresh (re-run batch steps without re-parsing)

```bash
cd C:/dev/scholarforge && uv run scholarforge refresh
```

Or from Python:
```python
from scholarforge.ingest import refresh_corpus

refresh_corpus()
```

This recomputes: topics, citation graph, embeddings, similarity, coupling, vault notes, BibTeX.

## Multi-library support

Scope data to a named library:
```bash
uv run scholarforge --library ald ingest ./ald-papers/
uv run scholarforge --library memristors ingest ./memristor-papers/
```

Each library gets its own DB, vault, ChromaDB, and cache under `data/libraries/<name>/`.

## Suppress noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands.
