# Ingestion and parsing

This document expands the "files -> corpus" branch of
[overview.md](overview.md). It covers how a folder of documents becomes a
**corpus**: the read-only, parsed, chunked, embedded, graphed input that
the wikify agent loop reads from. Read the overview first for the core
vocabulary (corpus, chunk, document); this page explains how that input
is built and what choices you have along the way.

A corpus is built once by a single command and then never changed during
a run. Everything below is what that one command does.

## The one command

```
wikify corpus build <source-dir> --out <corpus-dir>
```

`<source-dir>` is a folder of files on disk. `<corpus-dir>` is where the
parsed corpus is written. That is the whole contract: **a folder in, a
corpus out.** You can fill the source folder yourself, or have the
`arxiv` skill harvest papers into it first; either way, ingestion only
ever sees a directory of files.

The build walks the folder, parses every supported file, splits each one
into chunks, computes search embeddings, and extracts a citation and
topic graph. The full set of flags:

```
wikify corpus build <source-dir> --out <corpus-dir>
    [--parser default|lite|marker|docling]
    [--mode additive|sync]
    [--workers N]
    [--no-refresh]
    [--openalex/--no-openalex]
    [--allow-partial]
```

The rest of this document explains the stages those flags control.

## What counts as a source file

Ingestion recognises eight file extensions: `.pdf`, `.docx`, `.pptx`,
`.html`, `.htm`, `.md`, `.markdown`, and `.txt`. The source folder is
walked recursively, so you can organise files into subfolders (by year,
by topic) however you like. Anything with an unrecognised extension is
ignored.

**Document.** Each parsed source file becomes one *document* in the
corpus: its cleaned text, its title and metadata, its images, and the
chunks it was split into. A document gets a stable id derived from its
filename plus a short hash of its bytes (`mytitle_a1b2c3d4e5f6`), so the
same file always maps to the same document.

Two kinds of duplicate are filtered out automatically:

- **Same paper, two formats.** If `paper.pdf` and `paper.docx` sit in the
  same folder with the same name, they are treated as one paper in two
  formats and only the preferred one is parsed. PDF wins, because the
  layout-aware PDF parsers produce cleaner text than ad-hoc Office
  conversions. A `[skip-format]` line is logged for the dropped file.
- **Identical bytes.** If the exact same file appears twice (in this run
  or in a previous run of the same corpus), the duplicate is detected by
  its content hash and aliased to the document already on disk rather
  than parsed again.

## Parser backends

Parsing is the step that turns a binary file (a PDF, a Word document)
into clean Markdown text plus extracted images, equations, and section
structure. Wikify supports four **parser backends**, chosen with
`--parser`:

| Backend | PDF / DOCX / PPTX / HTML parser | When to use |
| --- | --- | --- |
| `default` | Docling | The default. Best quality. |
| `docling` | Docling (everywhere) | Same as default; one parser for all formats. |
| `marker` | Marker for PDFs | Fast PDF path when you do not need equations. |
| `lite` | pymupdf4llm + python-docx + python-pptx + trafilatura | CI, tests, low-resource machines. No models. |

`.md`, `.markdown`, and `.txt` files always go through a built-in
Markdown reader regardless of backend; backends only change how the
heavier formats are parsed.

### Docling (the default)

[Docling](https://github.com/docling-project/docling) is IBM's
document-conversion toolkit. One interface (`DocumentConverter`) handles
PDF, DOCX, PPTX, and HTML and returns a structured document that Wikify
lowers into Markdown plus images, sections, and metadata.

For PDFs, Docling runs a full enrichment pipeline: layout detection, a
table-structure model, and an optional formula head. That formula head is
the **Granite-Docling-258M** model, which reads equations off the page
and emits clean LaTeX. The first build downloads it once (about 258 MB)
along with the layout and table models; after the model cache is warm a
typical PDF parses in roughly ten seconds on a GPU. GPU acceleration is
used automatically when CUDA is available.

Docling is the default because, on real papers, its wall-clock time is
within about 13% of Marker while its structural formula extraction
produces materially cleaner equation LaTeX.

### Marker (PDF fallback)

[Marker](https://github.com/datalab-to/marker) is a surya-based PDF
pipeline: layout detection, OCR, equation extraction, and table
recognition, GPU-accelerated when available. It emits equations as
`$...$` and `$$...$$` LaTeX and keeps inline citation markers as
superscripts. Pass `--parser marker` for the absolute-fastest PDF path
when you do not need Docling's higher-quality equation extraction. Marker
only overrides the PDF parser; DOCX, PPTX, and HTML still use the
built-in readers.

### Lite (no models)

`--parser lite` uses only lightweight libraries: pymupdf4llm for PDFs,
python-docx for Word, python-pptx for PowerPoint, and trafilatura for
HTML. It downloads no machine-learning models and starts instantly, so it
is the right choice for continuous integration, automated tests, and
machines without a capable GPU. It does not extract equations from page
images. Library callers (tests and scripts) get `lite` by default; the
`wikify corpus build` command opts into `default` so interactive users
get the best parsers.

The selected backend is validated before any file is parsed, so a missing
dependency (for example, Marker not installed) fails fast with a clear
message instead of failing once per file midway through a long run.

## Chunking

A **chunk** is one small, addressable passage of a document, a few
paragraphs long. Chunks are the unit Wikify reads, searches, and cites:
every quote in a finished wiki page points back to a specific chunk.

Every backend's Markdown output goes through the same chunker, Docling's
`HybridChunker`. This matters: no matter which parser produced the text,
chunking is identical and is a pure function of the saved Markdown, which
is what makes a later `corpus rechunk` reproducible from disk alone.

The chunker is **tokenizer-aware**. It sizes chunks in tokens using the
tokenizer of the active embedding model, not in raw characters, so a
chunk fills the embedder's context window without overflowing it. The
default embedder is `jinaai/jina-embeddings-v2-small-en`, and the chunker
targets chunks up to about 2000 tokens. It also merges undersized
adjacent passages that share the same heading, which avoids tiny
fragments and keeps a coherent section together as one chunk.

After splitting, each chunk is labelled and filtered:

- **Empty or noise chunks are dropped.** A chunk with fewer than 30
  alphanumeric characters (for example, a stray `##`) carries no
  information and is discarded.
- **Boilerplate is flagged.** Publisher license blocks and similar
  filler are marked so they are excluded from retrieval.
- **A section type is assigned.** Every chunk gets a `section_type` drawn
  from a fixed list: `abstract`, `introduction`, `methods`, `results`,
  `discussion`, `conclusion`, `references`, `acknowledgments`,
  `appendix`, `figure`, `table`, `caption`, `boilerplate`, or plain
  `body`. This label is what lets the wiki loop separate real content
  from references, captions, and page furniture.

This last point is exactly why the overview warns that you cannot "cover
100% of the corpus": a parsed paper is roughly half non-content chunks
(references, captions, acknowledgments, boilerplate), and Wikify never
cites those as evidence.

Alongside the text chunks, the parser's extracted images become short
**caption chunks**, and equations and figure references are pulled out
and bound to the chunks they appear in. When Docling's formula head ran,
its structural LaTeX is merged with a lighter Markdown-level equation
scan so both sources are captured without double-counting.

## The corpus build (refresh)

Parsing and chunking happen per file. Once every file is parsed and
persisted, a second phase rebuilds the **corpus-wide derived artifacts**,
the things that can only be computed once the whole corpus is present.
This phase is called *refresh*, and `corpus build` runs it automatically
(pass `--no-refresh` to skip it). Its main stages:

- **Embeddings.** Every chunk is embedded into a vector so the corpus can
  be searched by meaning. Embedding is incremental: vectors for unchanged
  chunks are reused from the previous build, and only new or changed
  chunks are re-embedded. If you switch embedding models, every vector is
  recomputed so incompatible vectors never mix.
- **Document similarity.** Documents are linked to their most similar
  neighbours using the cosine similarity of their chunk vectors.
- **Topics.** A topic vocabulary is extracted across the corpus.
- **Citations and bibliography.** Each document's reference list is
  parsed, in-corpus citations are resolved to other documents (building
  the citation graph the agent walks), and a corpus bibliography is
  assembled. When `--openalex` is on (the default), this stage calls the
  OpenAlex API to canonicalise author and venue metadata and surface more
  citation matches; pass `--no-openalex` to stay fully offline. Set
  `OPENALEX_EMAIL` to use OpenAlex's faster polite pool.
- **Query store.** Everything is written into the corpus's SQLite store
  (`wikify.db`), the read surface the CLI and agent query.

## Incremental builds and resilience

A corpus remembers what it has already ingested in a **manifest**, so a
second `corpus build` against the same `--out` directory does the minimum
work. `--mode additive` (the default) adds new files and leaves existing
ones; `--mode sync` also removes documents whose source files have
disappeared from the folder. Unchanged files are skipped entirely.

Two properties make long builds safe on real hardware:

- **VRAM stability.** The GPU parsers restart their worker process every
  20 papers to hand all GPU memory back to the operating system. Without
  this, a long run on an 8 GB card can run out of memory partway through.
  The lite backend, which holds no GPU models, instead runs a long-lived
  pool of parallel CPU workers (tune with `--workers`).
- **Crash recovery.** Parsed documents are committed to disk in batches,
  and the manifest is the recovery boundary. If a build is killed midway,
  the documents already on disk are detected and skipped on the next run,
  so only the unfinished files are re-parsed.

When some files fail to parse, the build's default is **quality over
completeness**: it aborts before advertising the corpus as queryable
(exit code 5) and writes one line per failure to
`<corpus-dir>/failed_files.log`. The successful documents stay on disk and
are recovered on the next run, so re-running only retries the failures.
Pass `--allow-partial` when you deliberately want to inspect whatever
landed despite the failures.

## Where to go next

The companion document `corpus.md` covers the read side: how the finished
corpus is searched (semantic search, full-text search) and traversed (the
citation and topic graph). For how the agent then turns this corpus into
a wiki, return to [overview.md](overview.md) and follow the agent-loop
branch.
