# Citation Graph (Bibliography Matching)

## What it does
Matches raw bibliography entries against papers in the corpus to build a directed citation graph (A cites B).

## How bibliography entries are extracted
1. Find "References" / "Bibliography" / "Works Cited" heading
2. Split on numbered markers (`[1]`, `1.`) or blank lines
3. Strip markdown formatting
4. Discard entries < 20 characters (noise)

## Fuzzy matching algorithm
For each citation string, score against every corpus paper:

1. **Year must match** (hard filter). Extract 4-digit year from citation text.
2. **Author last name**: +3 points if any author's last name (>= 3 chars) appears in the citation text
3. **Title word overlap**: +1 per matching word (words >= 4 chars, lowercased)
4. **Threshold**: score >= 3 required (author match OR 2+ title words)
5. Self-citations are filtered out

Best-scoring match wins. Ties broken by score.

## Why fuzzy?
Bibliography formatting varies wildly across publishers. Exact string matching fails. The year+author+title combo is robust enough for corpus-internal matching.

## Where the code lives
- `extract/citations.py` — bibliography section extraction
- `extract/cite_match.py` — fuzzy matching against corpus
