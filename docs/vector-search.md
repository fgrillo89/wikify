# Vector search

Wikify lets you find passages and pages by *meaning*, not just by exact
words. This document explains how that works: what an **embedding** is,
the three ways Wikify searches (semantic, BM25, and hybrid), and the two
separate **vector indexes** it keeps — one over the corpus, one over the
finished wiki.

If you have not yet read `docs/overview.md`, read it first. The terms
**corpus**, **chunk**, **bundle**, **wiki page**, and **data artifact**
are defined there and reused here unchanged.

## Embeddings

An **embedding** is a list of numbers (a vector) that stands in for a
piece of text. Two passages that talk about the same idea get vectors
that point in nearly the same direction, even if they share no words.
That is what makes meaning-based search possible: to find text "about X"
you embed X, then look for the stored vectors closest to it.

Wikify computes one embedding per chunk when it builds a corpus, and one
embedding per prose page when it commits a wiki. The shared embedder
lives in `src/wikify/embedding.py` and everything else calls into it, so
the corpus and the wiki are always measured the same way.

### The embedder

There are two backends, chosen by the `WIKIFY_EMBEDDER` environment
variable:

- **`fastembed`** (the default): a small sentence-transformer model
  served through ONNX. The default model is
  `jinaai/jina-embeddings-v2-small-en` — 512-dimensional vectors with a
  long token window, which is what lets the chunker keep whole sections
  together. The model is configurable with `WIKIFY_EMBED_MODEL`; the
  registry in `embedding.py` knows the dimension, token window, and
  per-model batch size for each supported model.
- **`hash`**: a deterministic, offline bag-of-words projection
  (128-dimensional). It needs no model download and is meant for CI and
  smoke tests, where speed matters more than quality.

Two details make the vectors directly comparable:

- **Unit normalization.** Every vector is scaled to length 1 before it
  is stored (`encode_vector` in `corpus/store/vectors.py`). Once all
  vectors are unit length, the cosine between two of them is just their
  dot product, so search reduces to one matrix multiply.
- **Passage vs. query prefixes.** Some models expect a short task hint
  prepended to the text — one phrasing for documents being indexed,
  another for a search query. `embed_passages` and `embed_queries`
  apply the right prefix automatically from the model's registry entry,
  so indexing and querying always agree.

The embedder runs on GPU (CUDA or DirectML) when one is available and
falls back to CPU otherwise. Because a silent CPU fallback can turn a
minutes-long job into an hours-long one, the loader runs a one-token
warm-up and aborts with a clear message if that warm-up is suspiciously
slow.

### Where embeddings are stored

When a corpus is built, the ingest pipeline embeds every chunk
(`embed_passages` in `ingest/pipeline.py`) and writes the vectors into
the corpus database, `wikify.db`. Each batch of vectors belongs to an
**embedding space** — a row in the `embedding_spaces` table recording
the backend, model, and dimension that produced it. Vectors live in the
`embeddings` table, keyed by space, node type (`chunk`), and node id.

Recording the backend and model alongside the vectors matters: later
tools (evaluation, query, sampling) must embed their queries with the
*exact* same embedder, or the numbers would not be comparable.
`corpus/vectors_meta.py` reads the active space back so those tools can
reconstruct the matching embedder instead of guessing.

## The three search modes

Wikify offers three ways to rank chunks against a query. They answer
slightly different questions, and the hybrid mode combines them.

### Semantic search

Semantic search ranks chunks by **cosine similarity** to the query
vector — the meaning match described above. The query string is embedded
once, and that vector is compared against every stored chunk vector.

The comparison is brute force and lives in `VectorIndex`
(`corpus/store/vectors.py`). On the first query the index decodes all
vectors for the active space into a single in-memory `(n, d)` matrix;
each search is then one matrix multiply (`matrix @ query_vec`) followed
by a top-k selection. This trades a little startup cost for very simple,
fast per-query math, and keeps the door open to swapping in an
approximate-nearest-neighbor index later without changing any caller.

Scores are cosine values, roughly 0 to 1, where higher means closer.

### BM25 (full-text) search

BM25 is classic keyword search: it ranks chunks by how well their
*actual words* match the query, rewarding rarer terms and discounting
very long passages. Wikify uses SQLite's built-in FTS5 engine over two
shadow indexes — one for chunk text, one for document titles and
abstracts (`corpus/store/fts.py`). Document matches weight the title far
above the abstract.

BM25 finds exact terms that meaning-based search can miss: an acronym, a
chemical formula, a specific spelling. SQLite's `bm25()` returns
lower-is-better scores, so Wikify negates them when combining rankings.
A malformed FTS query (for example a stray hyphen, which FTS5 reads as
"NOT") is caught and treated as "no hits" so a search never crashes.

### Hybrid search

Hybrid search runs BM25 and semantic search together and merges their
rankings with **Reciprocal Rank Fusion** (RRF). RRF ignores the raw,
incomparable scores and looks only at *rank position*: a chunk's fused
score is the sum over rankings of `1 / (k + rank)`, with `k = 60`
(`rrf_fuse` in `corpus/store/fts.py`). A chunk that ranks high in
several channels rises to the top; one that only one channel found still
appears, lower down.

The chunk-level hybrid fuses three rankings: chunk BM25, document BM25
(each matching document expands to its chunks, carrying the document's
rank), and the semantic vector ranking. This is the most robust default
because it captures both keyword hits and meaning matches at once.

### Choosing a mode from the CLI

`wikify corpus find <query> --rank <mode>` exposes all of this. `--rank`
accepts `semantic` (the default), `bm25`, `hybrid`, or `all`, plus the
graph metrics `citation_count` and `pagerank` for ranking by importance
rather than relevance.

The `all` mode runs semantic, BM25, and a literal-substring grep
together, fuses them with RRF, removes duplicates, and tags each hit
with a small badge showing which channels matched (`s` semantic, `b`
BM25, `t` text). All modes accept an `exclude_kinds` filter so callers
can keep boilerplate — references, acknowledgments — out of content
retrieval.

## The two vector indexes

Wikify keeps embeddings in two distinct places, for two distinct
purposes. Keeping them separate is deliberate; they are searched through
different surfaces.

### The corpus chunk index

This is the index described above: one vector per chunk, in the corpus's
`wikify.db`. It is the substrate for reading and exploring the source
documents. It powers:

- **Chunk and paper search** (`corpus find`), in any of the three modes.
- **The similarity walk** (`corpus similarity-walk`), a recursive,
  depth-bounded crawl that starts from a query or a chunk and hops to
  nearby chunks above a cosine threshold (`similarity_walk` in
  `corpus/queries.py`). This is the semantic-boundary expansion the
  agent's explorers use to follow an idea across documents.
- **Diverse document sampling** and **author search**, which both lean
  on the same chunk vectors.

### The wiki page index

When pages are committed to a bundle, Wikify embeds them too, into a
*separate* set of tables in the bundle's database
(`wiki_embedding_spaces` and `wiki_embeddings`, served by
`WikiVectorIndex` in `bundle/wiki/vectors.py`). This index is over the
finished encyclopedia, not the raw sources, and it powers searching and
cross-linking the wiki itself:

- **`wiki find --mode semantic | hybrid`** ranks committed pages by
  meaning, or by the BM25+semantic RRF blend (`bundle/wiki/queries.py`).
  The wiki's own BM25 runs over an FTS index of page titles and bodies.
- **`wiki traverse <page> --similar`** finds the pages closest to a
  given page in embedding space — the "see also" relation.

Two points about this index:

- **Only prose pages are embedded.** Articles and person pages go into
  the vector space; **data artifacts do not**. Data tables are
  property-by-subject pivots, not prose, so meaning-based search over
  them is not meaningful. They remain fully searchable by text and BM25,
  and they still appear in `show`, `traverse`, and navigation.
- **Wiki search picks the most complete space, not the newest.** An
  incremental commit can create a small, fresh embedding space; choosing
  the space with the most embedded pages (then the newest as a
  tiebreak) prevents a one-page partial space from hijacking search and
  hiding every other page.

A third, lighter-weight store exists only for evaluation: a cached
`.npz` matrix of page-body vectors under the bundle's `derived/`
directory (`bundle/wiki/embeddings.py`). It is a rebuildable projection,
kept out of `wiki/` because evaluation is a read-only consumer of the
committed wiki, and it is invalidated automatically when any committed
page changes.
