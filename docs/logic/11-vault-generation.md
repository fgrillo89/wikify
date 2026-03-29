# Vault (Obsidian) Generation

## What it produces
An Obsidian vault under `data/vault/` with interconnected markdown notes.

## Note types

**Paper notes** (`papers/*.md`):
- YAML frontmatter: title, authors (wikilinks to author notes), year, tags, hasTopic, cites, similar_to, cites_same, file_hash, source_path
- Clickable link to open original PDF (`file:///` URI)
- Abstract (citation brackets stripped to avoid phantom wikilinks)
- Cites section (direct citation wikilinks)
- Figure/Table References section
- Similar Papers section (k-NN wikilinks)
- Bibliographic Coupling section
- Statistics (chunk + figure counts)
- Full Text (collapsed callout `[!quote]- Full Text` — searchable in Obsidian but hidden by default)

**Author notes** (`authors/*.md`):
- Frontmatter: name, tags
- Papers section with wikilinks
- Merged on update (existing papers preserved)

**Topic notes** (`topics/*.md`):
- Frontmatter: name, tags
- Related Papers section with wikilinks

## The Ghost Graph
All relationships are encoded as YAML frontmatter + wikilinks. Obsidian renders this as an interactive knowledge graph automatically. No graph database needed.

## Four edge types in the graph
| Signal | Representation | Direction |
|--------|---------------|-----------|
| Topics | Paper `hasTopic: [[topics/X]]` | Undirected |
| Similarity | Paper `similar_to: [[papers/X]]` | Undirected |
| Citations | Paper `cites: [[papers/X]]` | Directed |
| Coupling | Paper `cites_same: [[papers/X]]` | Undirected |

## Citation bracket stripping
`[[4,5]]` and `[10-12]` look like wikilinks to Obsidian. We convert `[N]` to `(N)` and remove `[[N]]` entirely.

## Full text design
Full paper text is stored as a collapsed Obsidian callout. It's searchable via Obsidian's search but invisible by default. The LLM retrieval pipeline reads from SQLite chunks, not vault notes — so the full text doesn't bloat LLM context.

## Where the code lives
- `vault/writer.py` — note generation + file I/O
- `vault/templates.py` — note content templates
- `vault/linker.py` — topic hub notes
