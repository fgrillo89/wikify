# Topic Extraction (Linking)

## Philosophy
No hardcoded topic dictionaries. Topics emerge from what authors declare.

## Two-pass algorithm

**Pass 1 — Build corpus vocabulary:**
- Scan every paper for "Keywords:", "Index Terms:", "Key Words:" sections
- Extract declared keywords (comma/semicolon/period-separated)
- Normalize plurals: "memristors" -> "memristor", "vacancies" -> "vacancy"
- Keep longest form as canonical: "neuromorphic" absorbed by "neuromorphic computing"

**Pass 2 — Assign topics per paper:**
- If paper has declared keywords: use those directly
- If not: match corpus vocabulary against the paper's text using word-boundary regex
  - Prefer specific (longer) terms: sorted by length descending
  - Cap at 8 matches per paper

**Pass 3 — Global normalization:**
- Merge plural variants across papers
- Rename to canonical display form

## Deduplication rules
1. Plural normalization ("memristors" = "memristor")
2. Substring absorption ("neuromorphic" absorbed by "neuromorphic computing")
3. Stem absorption for single-word topics only ("synapse" stem matches "synaptic device")

## Why this approach?
- No manual taxonomy maintenance
- Scales to any domain — topics come from the papers themselves
- Incremental: single-file ingestion uses cached vocabulary (O(1) file read)

## Where the code lives
- `vault/linker.py` — extraction, normalization, deduplication
- `ingest/registry.py` — caches corpus vocabulary to JSON
