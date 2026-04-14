# Citation Resolution Demo

Tested on mvp50 corpus (50 ALD/memristor papers, 1950 citations).

## Resolution Chain

```
Chunk text with [1-7] markers
    |
    v
Parse markers: [1-7] -> ordinals {1,2,3,4,5,6,7}
    |
    v
Look up ord in Document.citations -> citation dict with raw_text, doi
    |
    v
Heuristic parse (citestore/parse.py) -> title, authors, venue
    |
    v
Check DOI against corpus papers -> in_corpus: bool, corpus_doc_id
    |
    v
If in corpus: similarity search for concept chunks -> retrievable evidence
```

## What Works

- **Marker survival**: 332/3344 chunks (10%) retain `[N]` markers through
  PDF parsing + chunking
- **Ordinal mapping**: `Document.citations[N].ord` matches bracket numbers
- **Nature/IEEE/APA styles**: 73% title extraction, 0.27% false positive rate
- **Cross-paper fusion**: boosts title coverage from 73% to 98%
- **Corpus-internal detection**: DOI matching identifies cited works that are
  also corpus papers

## What Needs Improvement

### Elsevier/comma-delimited style (30% of mvp50)

Format: `- Authors, Title, Journal Vol (Year) Pages, DOI-URL.`

```
- Q. Xia, J.J. Yang, Memristive crossbar arrays for brain-inspired
  computing, Nat. Mater. 18 (2019) 309-323, https://doi.org/10.1038/
  s41563-019-0291-x.
```

Current parser classifies this as "perioded" but fails to extract the title
because there's no period between title and journal -- they're comma-separated.
The title is "Memristive crossbar arrays for brain-inspired computing" but
the parser grabs page numbers or DOI URLs instead.

**Fix needed**: Detect Elsevier numbered style (starts with `- `, no quotes,
no `vol.`/`pp.`, has URL at end) and use comma-delimited title extraction.

### Broken DOI extraction from URLs with spaces

Many PDF-extracted citations have spaces in DOI URLs:
```
https://doi.org/10.1002/ aelm.201900287
```

The DOI regex misses these because it stops at whitespace.

**Fix needed**: DOI extraction should handle spaces within `doi.org/` URLs.

### Corpus-internal detection by title

When a cited work lacks DOI but its title matches a corpus paper, we can
detect it. Currently only DOI matching is used. Title fuzzy matching would
increase corpus-internal detection.

## Quality Summary (current state)

| Style | % of corpus | Title yield | Notes |
|-------|-------------|-------------|-------|
| Perioded (Nature/Vancouver) | 69% | 75%+ | Works well |
| Quoted (IEEE/Chicago) | 16% | 85%+ | Works well |
| ACS (semicolons) | 10% | 60% | Decent |
| APA | 4% | 70%+ | Works well |
| Elsevier (comma-delimited) | ~30% | <30% | **Needs fix** |

The Elsevier style is the biggest gap. Fixing it would push overall title
extraction from 73% to ~85-90%.
