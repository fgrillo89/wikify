"""Rule-based scanner for bib-quality issues in corpus_papers.bib /
cited_works.bib.

Philosophy: each rule tests a *structural invariant* of a real BibTeX
field, not a hard-coded string. If a rule finds itself growing a list of
specific publishers, cities, or journal abbreviations, that rule is
degenerate — either generalise its pattern or delete it.

Rule categories (in decreasing order of generality):

  * Structural (field missing, empty, short): valid entries have a
    title, a year, and ≥1 author. Flag absences.

  * Author-shape: a real author is a sequence of name-tokens separated
    by comma/"and". Any single token must itself be a name shape
    (capitalised word, optional initials, optional particles). Flag
    anything that breaks this shape — lowercase-non-particle words,
    file extensions, colons, institution keywords, trailing affiliations.

  * Title-shape: a real title is prose. Citation-fragment residue,
    vol/page numbers, journal abbreviations at the boundary, web-PDF
    anchor IDs, and leftover citation markers all break prose shape.

  * Cross-field: journal name should not contain a publisher-city
    parenthetical, ISSN, or masthead boilerplate.

Each rule carries a "root cause" hint so the fix loop knows where to
look upstream.

Usage:
    uv run python scripts/scan_bib_quality.py
    uv run python scripts/scan_bib_quality.py --only author_shape,title_shape
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Bib parsing (structure only; we trust the writer's output format)
# ---------------------------------------------------------------------------

def _iter_entries(bib: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for match in re.finditer(
        r"@(\w+)\{([^,]+),([^@]*?)\n\}", bib, re.DOTALL,
    ):
        key = match.group(2).strip()
        body = match.group(3)
        entry: dict[str, str] = {"_key": key, "_raw": match.group(0)}
        for fm in re.finditer(
            r"\s*(\w+)\s*=\s*\{(.*?)\},?\s*(?=\n\s*(?:\w+\s*=|\})|$)",
            body,
            re.DOTALL,
        ):
            entry[fm.group(1).lower()] = fm.group(2).strip()
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

# Lowercase words that are legitimate parts of author names.
_NAME_PARTICLES = frozenset({
    "van", "von", "der", "de", "da", "di", "la", "le", "du",
    "del", "den", "dos", "el", "al", "bin", "ibn",
    "and",  # separator, not a particle but shows up between names
})


def _author_tokens(author: str) -> list[str]:
    """Split an author string into individual-name tokens."""
    return [t.strip() for t in re.split(r"\s+and\s+", author) if t.strip()]


def _title_needs_structural_check(title: str) -> bool:
    return bool(title and len(title.split()) >= 2)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
#
# Each rule: (id, invariant-tested, root-cause-hint, predicate).
# `invariant-tested` is the *positive* thing the entry should look like.
# `root-cause-hint` tells the fix loop where in the pipeline to look.

Rule = tuple[str, str, str, Callable[[dict[str, str]], bool]]


# ---- Structural (field presence) ------------------------------------------

def _r_missing_field(field: str) -> Callable[[dict[str, str]], bool]:
    return lambda e: not e.get(field, "").strip()


# ---- Author-shape ---------------------------------------------------------
#
# A real author is one of:
#   * `First Last` (two+ title-cased words)
#   * `F. Last` (initials + surname)
#   * `Last, F.` (CrossRef form)
#   * Chinese/Korean single ideograph tokens (rare in this corpus)
#
# Anything that violates those shapes is evidence of parser leakage.

def _author_has_lowercase_content_word(entry: dict[str, str]) -> bool:
    """A name token contains a lowercase word that isn't a particle.

    Example failure: author = `G. A low-cost` — "low-cost" is prose
    that bled from the title into the author slot.
    """
    for tok in _author_tokens(entry.get("author", "")):
        for word in tok.split():
            w = word.strip(".,;:-").lower()
            if not w or w.isdigit():
                continue
            if word != word.lower():
                continue  # has uppercase — legitimate
            if w in _NAME_PARTICLES:
                continue
            return True
    return False


def _author_has_prose_separator(entry: dict[str, str]) -> bool:
    """Name tokens containing `:` — real names never do. Catches running-
    header residues like `CHUA: MEMRISTOR-MISSING CIRCUIT ELEMENT`.
    """
    return ":" in entry.get("author", "")


def _author_has_institution(entry: dict[str, str]) -> bool:
    """Name tokens contain an institution/corporation keyword.

    General pattern: real author names don't contain these words. When
    they do, the parser concatenated a byline with its affiliation tail.
    """
    return bool(re.search(
        r"\b(?:University|Institute|Laboratory|Department|School|"
        r"College|Corporation|Foundation|Research\s+(?:Center|Centre|Lab))\b",
        entry.get("author", ""),
        re.IGNORECASE,
    ))


def _author_has_file_extension(entry: dict[str, str]) -> bool:
    """`.pdf` / `.docx` / `.html` in author → source-path leaked."""
    return bool(re.search(
        r"\.(?:pdf|docx|html?|xml)\b",
        entry.get("author", ""),
        re.IGNORECASE,
    ))


def _author_has_digits(entry: dict[str, str]) -> bool:
    """Digit clusters outside initial-marker positions. Real author names
    never have bare numbers.
    """
    # Allow `\d` ONLY if preceded by `.` (part of an initial) or at start
    # with a year marker — neither applies in practice.
    author = entry.get("author", "")
    return bool(re.search(r"\d{2,}", author))


# ---- Title-shape ----------------------------------------------------------
#
# A real title is prose: a sentence-like sequence of words with very few
# punctuation clusters. Citation-fragment residue breaks prose shape in
# specific ways we can test structurally.

_ANCHOR_ID_RE = re.compile(
    # `/sbref0021`, `#sbref0021`, `90110-9/sbref0021)`, `/fn5`, `/anchor3`.
    # Requires a known anchor-name prefix (sbref|ref|fn|anchor|note|sec|bib)
    # so chemical formulas with slashes (`Pt/HfO2`, `TiO2/Pt`) don't match.
    r"(?:\d+-\d+/)?[/#](?:sbref|fn|anchor|note|sec|bib)\d+\)?",
    re.IGNORECASE,
)


def _title_has_anchor_id(entry: dict[str, str]) -> bool:
    """Title contains an HTML/PDF anchor id — a known anchor-prefix token
    (sbref, fn, anchor, note, sec, bib) preceded by ``/`` or ``#`` and
    followed by digits. Real titles never contain these; they're web-PDF
    tag residue.
    """
    return bool(_ANCHOR_ID_RE.search(entry.get("title", "")))


def _title_has_citation_marker(entry: dict[str, str]) -> bool:
    """Title starts with `[N]`, `>[N]`, `(N)`, or `N.` where N is a
    reference-list ordinal. These only exist because the upstream parser
    didn't strip the leading marker from a reference string.
    """
    title = entry.get("title", "").lstrip()
    return bool(re.match(r"^[>]?\s*[\[\(]\d+[\]\)]|^\d+\.\s+[A-Z]", title))


def _title_has_year(entry: dict[str, str]) -> bool:
    """Title contains a 4-digit year — prose rarely does; citation
    fragments (journal + year + vol) always do.

    Exception: titles that legitimately mention a year ("The 2007
    financial crisis…") are rare in scientific bibliographies; accept
    this rule as high-precision for the citation domain.
    """
    title = entry.get("title", "")
    if not title:
        return False
    return bool(re.search(r"\b(?:19|20)\d{2}\b", title))


def _title_has_volume_pages(entry: dict[str, str]) -> bool:
    """Title contains a `vol, pages` or `vol(year) pages` tuple."""
    return bool(re.search(
        r"\b\d{1,4}\s*,\s*\d{3,}|\b\d{1,4}\s*\(\s*\d{4}\s*\)\s*\d+",
        entry.get("title", ""),
    ))


def _title_ends_with_journal_abbrev(entry: dict[str, str]) -> bool:
    """Title ends with `. <Short>` where <Short> is 1–3 title-cased
    words each ≤12 chars. Prose sentences end with common English
    words; this pattern specifically catches a trailing abbreviated
    journal name (`. Results Phys`, `. Nevac Blad`, `. Appl`) that the
    reference-string parser forgot to split off.
    """
    return bool(re.search(
        r"\.\s+(?:[A-Z][A-Za-z]{0,11})(?:\s+[A-Z][A-Za-z]{0,11}){0,2}\.?\s*$",
        entry.get("title", ""),
    ))


def _title_has_stranded_initial(entry: dict[str, str]) -> bool:
    """Title begins with `<Letter>. ` followed by a title-cased word ≥4
    chars. Letter + period is initials-shape; a legitimate title almost
    never begins this way. Guarded on the next word being ≥4 chars +
    title-case so genus abbreviations (`A. vulgaris`) are spared.
    """
    return bool(re.match(
        r"^[A-Z]\.\s+[A-Z][a-z]{3,}", entry.get("title", ""),
    ))


def _title_starts_with_conjunction(entry: dict[str, str]) -> bool:
    """Title starts with a conjunction that can't open a real title
    (`and`, `or`, `but`, `nor`, `so`). Signals truncation at the front.
    """
    title = entry.get("title", "").strip().lower()
    return any(
        title.startswith(w + " ") for w in ("and", "or", "but", "nor")
    )


def _title_contains_markdown_link(entry: dict[str, str]) -> bool:
    """Title contains `](` — markdown-link syntax that only appears when
    a masthead or TOC line leaked into the title slot.
    """
    return "](" in entry.get("title", "")


def _title_has_html_entity(entry: dict[str, str]) -> bool:
    """Title contains an un-decoded HTML entity (`&amp;`, `&lt;`, `&#x...`).
    A proper BibTeX title should never carry entities — they should have
    been decoded upstream.
    """
    return bool(re.search(
        r"&(?:amp|lt|gt|quot|apos|#x?\d+|\w{2,8});",
        entry.get("title", ""),
    ))


# ---- Journal-shape --------------------------------------------------------

def _journal_has_issn_or_rights(entry: dict[str, str]) -> bool:
    """Journal field contains masthead boilerplate: ISSN, rights-reserved,
    copyright, or markdown emphasis markers.
    """
    journal = entry.get("journal", "")
    if not journal:
        return False
    return bool(re.search(
        r"ISSN|©|all\s+rights\s+reserved|\*\*",
        journal,
        re.IGNORECASE,
    ))


def _journal_has_city_paren(entry: dict[str, str]) -> bool:
    """Journal name has a trailing `(<City>)` publisher-location tag.

    General: any trailing `(<word>)` where the word is a recognised
    city-name shape — Title-cased single word, no digits. Flags
    `(Basel)`, `(Cham)`, `(London)`, but not `(Pt. A)` which has a
    period.
    """
    journal = entry.get("journal", "")
    return bool(re.search(r"\s*\([A-Z][a-z]{2,}\)\s*$", journal))


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

RULES: list[Rule] = [
    # Structural ------------------------------------------------------------
    (
        "title_missing", "entry has a title field",
        "upstream: _reference_entry_from_citation filter",
        _r_missing_field("title"),
    ),
    (
        "author_missing", "entry has an author field",
        "upstream: _reference_entry_from_citation filter",
        _r_missing_field("author"),
    ),
    (
        "year_missing", "entry has a year field",
        "upstream: citestore.parse year-extraction regex",
        _r_missing_field("year"),
    ),
    (
        "title_placeholder",
        "title is not a known Word/PDF placeholder",
        "parser: docx.core_properties.title without junk guard",
        lambda e: e.get("title", "").strip().lower() in {
            "word document", "untitled", "document1", "document",
            "new microsoft word document",
        },
    ),
    # Author-shape ----------------------------------------------------------
    (
        "author_lowercase_content",
        "all author tokens are capitalised (or particles)",
        "ref parser split on wrong boundary; title bled into author",
        _author_has_lowercase_content_word,
    ),
    (
        "author_colon",
        "author names don't contain `:` (running-header filter)",
        "surname-anchored scanner on IEEE running headers",
        _author_has_prose_separator,
    ),
    (
        "author_institution",
        "author names don't contain institution keywords",
        "extract_authors_from_markdown grabbed affiliation tail",
        _author_has_institution,
    ),
    (
        "author_file_extension",
        "author names don't contain file extensions",
        "YAML frontmatter source_path line leaked into scanner",
        _author_has_file_extension,
    ),
    (
        "author_has_digits",
        "author names don't contain multi-digit numbers",
        "ref parser kept page/volume numbers in author field",
        _author_has_digits,
    ),
    # Title-shape -----------------------------------------------------------
    (
        "title_anchor_id",
        "title is prose; no HTML/PDF anchor ids",
        "upstream: citestore.parse stripping `#anchor`/`/anchor`",
        _title_has_anchor_id,
    ),
    (
        "title_citation_marker",
        "title has no leftover `[N]` / `>[N]` / `N.` prefix",
        "upstream: reference-string parser didn't strip list marker",
        _title_has_citation_marker,
    ),
    (
        "title_year_in_body",
        "title has no embedded 4-digit year",
        "ref-fragment split at wrong boundary (journal YYYY fragment)",
        _title_has_year,
    ),
    (
        "title_volume_pages",
        "title has no vol/pages tuple",
        "ref-fragment: trailing `, NN, NNNN` survived cleanup",
        _title_has_volume_pages,
    ),
    (
        "title_trailing_abbrev",
        "title has no trailing 1–3 word abbreviation with period",
        "ref-fragment: journal abbrev appended as title tail",
        _title_ends_with_journal_abbrev,
    ),
    (
        "title_stranded_initial",
        "title doesn't start with a stranded `<Initial>.`",
        "citation parser kept an author's middle-initial in title slot",
        _title_has_stranded_initial,
    ),
    (
        "title_starts_conjunction",
        "title doesn't start with a conjunction",
        "_clean_bib_title author-prefix strip removed real content",
        _title_starts_with_conjunction,
    ),
    (
        "title_markdown_link",
        "title has no `](` markdown-link fragment",
        "first_heading picked up a banner with a hyperlink",
        _title_contains_markdown_link,
    ),
    (
        "title_html_entity",
        "title has no un-decoded HTML entities (`&amp;`, `&lt;`, `&#x...`)",
        "upstream parser stripped HTML tags but not entities",
        _title_has_html_entity,
    ),
    # Journal-shape ---------------------------------------------------------
    (
        "journal_masthead",
        "journal has no ISSN/rights/copyright/markdown-emphasis",
        "extract_publication_fields accepted masthead as venue",
        _journal_has_issn_or_rights,
    ),
    (
        "journal_city_paren",
        "journal has no trailing `(<City>)`",
        "DOI content-neg returned container-title with location",
        _journal_has_city_paren,
    ),
]


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

_TITLE_NORM_RE = re.compile(r"[^a-z0-9]+")


def _title_norm_key(title: str) -> str:
    return _TITLE_NORM_RE.sub("", title.lower())


def find_duplicates(entries: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Group entries whose normalised title matches."""
    buckets: dict[str, list[dict[str, str]]] = {}
    for e in entries:
        key = _title_norm_key(e.get("title", ""))
        if len(key) < 10:
            continue
        buckets.setdefault(key, []).append(e)
    return [group for group in buckets.values() if len(group) > 1]


# ---------------------------------------------------------------------------
# Scan driver
# ---------------------------------------------------------------------------

def scan(
    entries: list[dict[str, str]], *, only: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, str]]], list[list[dict[str, str]]]]:
    hits: dict[str, list[dict[str, str]]] = {}
    for rule_id, _invariant, _rootcause, predicate in RULES:
        if only and rule_id not in only:
            continue
        matched = [e for e in entries if predicate(e)]
        if matched:
            hits[rule_id] = matched
    dups = find_duplicates(entries)
    return hits, dups


def _print_report(
    name: str,
    n: int,
    hits: dict[str, list[dict[str, str]]],
    dups: list[list[dict[str, str]]],
    *,
    max_examples: int = 3,
) -> None:
    print(f"\n{'=' * 72}\n{name}: {n} entries\n{'=' * 72}")
    if not hits and not dups:
        print("  (no rules triggered)")
        return
    rules_by_id = {r[0]: (r[1], r[2]) for r in RULES}
    for rule_id, matches in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        invariant, rootcause = rules_by_id.get(rule_id, ("?", "?"))
        print(f"\n[{rule_id}] want: {invariant}")
        print(f"    fix-at: {rootcause}")
        print(f"    hits: {len(matches)}")
        for e in matches[:max_examples]:
            key = e.get("_key", "?")[:60]
            title = e.get("title", "")[:90]
            author = e.get("author", "")[:60]
            print(f"      {key}")
            if title:
                print(f"        title:  {title}")
            if author:
                print(f"        author: {author}")
    if dups:
        print(f"\n[duplicates] same-title groups — {len(dups)} groups")
        for group in dups[:max_examples]:
            print(f"  group ({len(group)} entries):")
            for e in group:
                print(f"    {e.get('_key', '?')[:60]}  :: {e.get('title', '')[:60]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default="data/corpora/ald_all_marker")
    parser.add_argument("--only", default="", help="comma-separated rule ids")
    parser.add_argument("--max-examples", type=int, default=3)
    args = parser.parse_args()

    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace",
    )
    only = {r.strip() for r in args.only.split(",") if r.strip()} or None
    root = Path(args.corpus)

    totals: Counter[str] = Counter()
    for bib_name in ("corpus_papers.bib", "cited_works.bib"):
        bib = (root / bib_name).read_text(encoding="utf-8")
        entries = _iter_entries(bib)
        hits, dups = scan(entries, only=only)
        _print_report(bib_name, len(entries), hits, dups,
                      max_examples=args.max_examples)
        for rule_id, matches in hits.items():
            totals[rule_id] += len(matches)
        if dups:
            totals["duplicates"] += sum(len(g) - 1 for g in dups)

    print(f"\n{'=' * 72}\nTotals across both files\n{'=' * 72}")
    for rule_id, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {rule_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
