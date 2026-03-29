# Section-Aware Chunking

## What it does
Splits paper markdown into ~600-token chunks that respect section boundaries.

## Rules
1. **Never split across sections**. Each chunk stays within one heading's scope.
2. **Target size**: 600 tokens (configurable). Max: 800 tokens.
3. **Split on paragraph boundaries** (double newlines). Never mid-sentence.
4. **Overlap**: Last paragraph of a chunk carries into the next (50 tokens max) for context continuity.
5. **Section path tracking**: Each chunk records its heading path (e.g., `Introduction.Background.Related Work`) for structured retrieval.

## Metadata flags per chunk
- `has_citations`: regex detects `[Author 2020]` or `[1]` patterns
- `has_equations`: regex detects `$$...$$` or `\[...\]` patterns
- `token_count`: counted via tiktoken (cl100k_base encoding)

## Why section-aware?
Generic chunkers lose the hierarchical structure of papers. Knowing a chunk is from "Methods.Data Collection" vs "Results.Table 1" lets the retrieval pipeline filter by section type.

## Where the code lives
- `extract/chunker.py`
