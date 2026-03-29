# Metadata Extraction

## What it extracts
Title, authors, abstract, year, DOI.

## Priority chain (for each field)

**Title**: PDF metadata field -> first markdown heading -> filename pattern -> filename stem

**Authors**: "Authors:" section in text -> PDF metadata -> filename pattern (e.g., `Kim_2021_title.pdf`)

**Abstract**: Regex match for "Abstract" section header, then grab text until next section. If <50 words, extend by concatenating subsequent paragraphs.

**Year**: Filename pattern -> regex in text (1950-2030 range) -> PDF metadata

**DOI**: Regex `10.\d{4,}/\S+` in text -> PDF metadata

## Key rules

- Abstract must be >= 50 words. If the initial regex capture is shorter, keep appending paragraphs from the text until threshold is met or text runs out.
- Garbled titles are detected (patterns like `acs_nn...` or too many dots/underscores) and fall back to filename.
- Author parsing handles formats: "Last, First; Last, First", "First Last and First Last", comma-separated. Initials like "J." get reassembled with the next token.

## Where the code lives
- `extract/metadata.py`
