"""Build bibliography artifacts from wikify Documents.

Corpus papers -> ``corpus_papers.bib`` (one entry per corpus Document).
Cited works -> ``cited_works.bib`` (only CrossRef-resolved references).
Citations -> ``citations.json`` (structured citation graph for matching).

Structured reference fields come exclusively from CrossRef resolution.
We do not regex-parse raw citation text into authors/titles -- that
approach produced garbage and required 800+ lines of repair code.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter

from ..api import Corpus
from ..models import Document
from .metadata import (
    extract_authors_from_markdown,
    extract_document_doi,
    extract_publication_fields,
    parse_filename,
)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")
_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CITATION_INDEX_VERSION = 2


def _sanitize_id(s: str) -> str:
    return _ID_SAFE_RE.sub("_", s).strip("_")[:80] or "unknown"


def _clean_doi(value: object) -> str:
    raw = str(value or "").strip()
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw)
    # Strip trailing punctuation that never belongs to a DOI. Only strip an
    # unbalanced trailing ``)`` so DOIs like 10.1016/S0893-6080(97)00011-7
    # keep their balanced parens.
    raw = raw.rstrip(".,;")
    while raw.endswith(")") and raw.count(")") > raw.count("("):
        raw = raw[:-1]
    return raw


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value).strip() if value else ""


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        # Route through parse_authors which handles the CrossRef pattern
        # "Wang, Tian-Yu and Meng, Jia-Lin" correctly (surname/given pairs,
        # hyphenated given names, trailing affiliation letters). The old
        # naive split on "," + " and " shredded it into singletons.
        from .metadata import parse_authors

        parsed = parse_authors(value)
        if parsed:
            return parsed
        parts = value.replace(" and ", ", ").split(",")
        return [p.strip() for p in parts if p.strip()]
    return []


def _first_text(metadata: dict, *keys: str) -> str:
    for k in keys:
        v = metadata.get(k)
        if v:
            return _as_text(v)
    return ""


def _add_optional(entry: dict[str, str], field: str, value: object) -> None:
    text = _as_text(value)
    if text:
        entry[field] = text


def _clean_title(value: str) -> str:
    text = _as_text(value)
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    return text.strip()


# Unicode ranges for affiliation/footnote symbols that appear next to
# author names in PDFs (Oriya digits, asterisks, private-use glyphs).
_AFFILIATION_RE = re.compile(
    r"[\u0B00-\u0B7F"  # Oriya script (used as superscript markers)
    r"\u204E"           # ⁎ low asterisk
    r"\u2020-\u2021"    # † ‡ daggers
    r"\u00B9\u00B2\u00B3"  # ¹ ² ³ superscript digits
    r"\u2070-\u209F"    # superscript/subscript block
    r"\uE000-\uF8FF"   # private use area (font-specific symbols)
    r"\*]+"
)


# Lowercase name particles + suffixes canonical in metadata.py. Re-exported
# here under private names to keep existing callers untouched.
from .metadata import NAME_PARTICLES as _NAME_PARTICLES  # noqa: E402
from .metadata import NAME_SUFFIXES as _NAME_SUFFIXES  # noqa: E402


def _clean_author_name(name: str) -> str:
    """Normalize an author name: strip affiliation symbols, fix casing."""
    # Strip affiliation/footnote markers
    name = _AFFILIATION_RE.sub("", name).strip()
    # Drop lone backslashes left behind by LaTeX-escape passes (e.g.
    # "Jianshi Tang \") and collapse the resulting double whitespace.
    name = re.sub(r"\\+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Only apply casing fixes when the entire name is all-caps or all-lower.
    # Mixed-case names (van der Waals, McMaster) are left as-is.
    all_upper = name == name.upper() and any(c.isalpha() for c in name)
    all_lower = name == name.lower() and any(c.isalpha() for c in name)
    if not (all_upper or all_lower):
        # In the mixed-case path, a trailing 1-2 letter ALL-LOWERCASE
        # token is an affiliation superscript ("Xin-Gui Tang ab", "Park a")
        # ONLY when the name has ≥2 capitalised tokens before it. A
        # 2-token name like "Yang yi" (cap+lower) is a legitimate
        # given-name pair where the lowercased second token must not
        # be stripped — there's no room for an affiliation marker
        # there. A 3-token name like "Xin-Gui Tang ab" has a clear
        # byline shape with room for the marker.
        tokens = name.split()
        if (
            len(tokens) >= 3
            and re.fullmatch(r"[a-z]{1,2}", tokens[-1])
            and tokens[-2][:1].isupper()
        ):
            return " ".join(tokens[:-1])
        return name
    parts = name.split()
    cleaned = []
    for i, part in enumerate(parts):
        low = part.lower()
        # Preserve particles (van, de, von) unless they start the name
        if i > 0 and low in _NAME_PARTICLES:
            cleaned.append(low)
        elif "-" in part:
            cleaned.append("-".join(w.capitalize() for w in part.split("-")))
        else:
            cleaned.append(part.capitalize())
    return " ".join(cleaned)


def _clean_venue(value: str) -> str:
    text = _as_text(value)
    return re.sub(r"\s+", " ", text).strip()


def _unique_bibkey(base: str, seen: dict[str, int]) -> str:
    key = _sanitize_id(base)
    if key not in seen:
        seen[key] = 0
        return key
    seen[key] += 1
    suffix = chr(ord("a") + min(seen[key] - 1, 25))
    return f"{key}{suffix}"


def _normalise_title_key(value: object) -> str:
    tokens = _TITLE_TOKEN_RE.findall(_as_text(value).casefold())
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Source document entries (library.bib)
# ---------------------------------------------------------------------------


# IEEE-style author-affiliation prose tail: "Jinbin Wang are with the..."
# (also "is with", "was with", "were with"). These come from the first-page
# byline followed by its affiliation sentence that an over-eager author
# extractor fused with the name.
_AFFILIATION_PROSE_TAIL_RE = re.compile(
    r"\s+(?:is|are|was|were)\s+with\b.*$",
    re.IGNORECASE,
)


def _strip_author_artifacts(name: str) -> str:
    """Structurally clean a single author-list token: strip IEEE
    affiliation prose ("... is with the University..."), trailing
    "et al." / "et al" / " - " / " — " / " _" residues.

    Recovery path, not rejection: `"Facai Wu is with the"` becomes
    `"Facai Wu"` rather than being discarded whole.
    """
    s = name.strip()
    s = _AFFILIATION_PROSE_TAIL_RE.sub("", s)
    # Strip trailing "et al." / "et al" optionally followed by a dash.
    s = re.sub(r"\s+et\s+al\.?\s*[-–—_]*\s*$", "", s, flags=re.IGNORECASE)
    # Strip lone trailing dash/em-dash/underscore clusters.
    s = re.sub(r"\s*[-–—_]+\s*$", "", s)
    return s.strip()


def _is_plausible_author(name: str) -> bool:
    """Check if a string looks like an author name (not body text).

    A name must start with a capital, have ≤5 words, and contain no
    prose-residue (lowercase non-particle words, colons, digits, etc.).
    Prose-residue delegates to ``_author_has_prose_residue`` so the
    same rule that filters references (cited_works.bib) also filters
    corpus papers.
    """
    name = name.strip()
    if not name or len(name) < 2:
        return False
    words = name.split()
    if len(words) > 5:
        return False
    # Must start with uppercase
    if not words[0][0].isupper():
        return False
    # Must not contain chemical formulas or numbers
    if re.search(r"\d|[A-Z]{2,}\d", name):
        return False
    # Must not contain prose residue — same rule as references.
    if _author_has_prose_residue(name):
        return False
    return True


def _document_entry(doc: Document) -> dict[str, str]:
    metadata = doc.metadata or {}
    # Clean each author-list token of IEEE affiliation prose and "et al."
    # residue BEFORE plausibility checks so recoverable names survive.
    raw_authors = _as_list(metadata.get("authors"))
    cleaned = [_strip_author_artifacts(a) for a in raw_authors]
    authors_list = [
        _clean_author_name(a) for a in cleaned if _is_plausible_author(a)
    ]
    # Corpus entries should emit even when no author survives cleanup —
    # the title (from filename) is authoritative. Fall back to the
    # filename surname when the author list empties out.
    if not authors_list:
        fn_year, fn_author, fn_title = parse_filename(
            Path(doc.source_path).name if doc.source_path else "",
        )
        if fn_author:
            authors_list = [_clean_author_name(fn_author)]
    title = _clean_bib_title(_clean_title(_as_text(doc.title)))
    # If title is garbage, recover from doc.id
    if _title_needs_fallback(title):
        m = re.match(r"^\[\d{4}\s+[^\]]+\]\s*(.+?)(?:_[0-9a-f]{6,})?$", doc.id)
        if m:
            title = _clean_bib_title(m.group(1).replace("_", " ").strip())

    year = metadata.get("year")
    year_str = str(year) if year else ""
    doi = _clean_doi(metadata.get("doi"))
    venue = _clean_venue(
        _first_text(metadata, "venue", "journal", "publicationTitle"),
    )
    url = _first_text(metadata, "url", "URL")
    if not url and doi:
        url = f"https://doi.org/{doi}"
    keywords = _as_list(metadata.get("keywords") or metadata.get("topics"))
    abstract = _first_text(metadata, "abstract") or _as_text(doc.abstract)

    entry: dict[str, str] = {
        "ENTRYTYPE": "article",
        "ID": _sanitize_id(doc.id),
        "title": title,
        "author": " and ".join(authors_list),
    }
    if year_str:
        entry["year"] = year_str
    if doi:
        entry["doi"] = doi
    if venue:
        entry["journal"] = venue
    _add_optional(entry, "volume", metadata.get("volume"))
    _add_optional(
        entry, "number", metadata.get("number") or metadata.get("issue"),
    )
    _add_optional(entry, "pages", metadata.get("pages"))
    _add_optional(entry, "publisher", metadata.get("publisher"))
    _add_optional(entry, "issn", metadata.get("issn"))
    if url:
        entry["url"] = url
    if abstract:
        entry["abstract"] = abstract
    if keywords:
        entry["keywords"] = ", ".join(keywords)
    return entry


# Prose-residue check: particles + suffixes + the "and" separator that
# shows up between names in our splitter's input.
_AUTHOR_NAME_PARTICLES = _NAME_PARTICLES | _NAME_SUFFIXES | frozenset({"and"})


def _looks_like_author_fragment(piece: str) -> bool:
    """True if ``piece`` has the shape of an author-name fragment —
    1–4 words, at least one capitalised, mostly letters/periods/hyphens,
    no lowercase content word. Used to detect titles that are really
    comma-separated author-list tails ("Galdin-Retailleau, D. Querlioz").
    """
    piece = piece.strip()
    if not piece or len(piece) > 40:
        return False
    words = piece.split()
    if not words or len(words) > 4:
        return False
    if not any(w[0:1].isupper() for w in words):
        return False
    # Reject if any lowercase word is a prose content word.
    if _author_has_prose_residue(piece):
        return False
    # Mostly alphabetic? Allow `.`/`-`/`'`.
    return bool(re.fullmatch(r"[A-Za-z.\-'\s]+", piece))


def _author_has_prose_residue(author: str) -> bool:
    """True if an author-list token contains prose or a colon.

    A legitimate author token consists of initials (uppercase + period)
    and/or capitalised name-words + recognised particles. Any other
    lowercase word (``the``, ``gradual``, ``plasticity``) means prose
    bled into the author slot; a colon means a running header was
    parsed as a name. Reject either.
    """
    if ":" in author:
        return True
    for word in author.split():
        w = word.strip(".,;:-").lower()
        if not w or w.isdigit():
            continue
        if word != word.lower():
            continue  # has uppercase — legitimate
        if w in _AUTHOR_NAME_PARTICLES:
            continue
        return True
    return False


def _strip_year_anchored_tail(title: str) -> str:
    """Strip a trailing citation fragment anchored on a 4-digit year.

    Structural rule: a legitimate paper title almost never contains a
    sentence break followed by a year. When the title does, cut at the
    FIRST punctuation boundary (``. ``, ``, ``, `` (``) before the
    year, provided that boundary is >= 20 chars in so short titles
    aren't decapitated.

    Replaces several publisher-specific trailing-tail regexes that
    grew organically as each new failure mode surfaced (Chinese-style
    citations, Park 2020's Journal-YYYY-vol-pages, Materials (YYYY),
    etc.). Under this one rule every case reduces to "there's a year
    past the title proper; cut at the earliest punctuation before it."
    """
    year_m = re.search(r"\b(?:19|20)\d{2}\b", title)
    if not year_m:
        return title
    prefix = title[: year_m.start()]
    cuts = []
    for sep in (". ", ", ", " (", "? ", "! "):
        # Use ``rfind`` (LAST boundary before the year) so a legitimate
        # subtitle like "Paper Title. Subtitle. Journal 2020" keeps
        # "Paper Title. Subtitle" rather than losing everything after
        # the first period. The trailing-journal-abbrev strip that runs
        # later in ``_clean_bib_title`` catches any short journal-name
        # residue between the subtitle and the year.
        idx = prefix.rfind(sep)
        if idx >= 20:
            cuts.append(idx)
    if not cuts:
        return title
    return title[: max(cuts)].rstrip(" .,;:()?!")


def _clean_bib_title(title: str) -> str:
    """Clean a title for BibTeX output: strip HTML, newlines, leaked metadata."""
    import html as _html

    # Collapse newlines to spaces
    title = title.replace("\n", " ").replace("\r", " ")
    # Decode HTML entities before looking for tags. Some upstream parses
    # double-encode (``&amp;#x00D7;`` → ``&#x00D7;`` → ``×``); loop until
    # the string is stable so both layers unwind.
    for _ in range(3):
        decoded = _html.unescape(title)
        if decoded == title:
            break
        title = decoded
    # Convert HTML subscript/superscript to LaTeX (including <inf> variant)
    title = re.sub(r"<(?:sub|inf)>(.*?)</(?:sub|inf)>", r"$_{\1}$", title, flags=re.I | re.S)
    title = re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", title, flags=re.I | re.S)
    # Strip remaining HTML tags
    title = re.sub(r"<[^>]+>", "", title)
    # Strip any trailing citation fragment anchored on a 4-digit year. One
    # structural rule in place of the several per-publisher regexes that
    # used to live here.
    title = _strip_year_anchored_tail(title)
    # Strip a trailing abbreviated journal name: `. <Word> <Word>?` where
    # each word is ≤12 chars title-cased (common abbrevs: "Neural Comput",
    # "Phys Rev", "ACS Nano", "Briefings Bioinf"). A real title rarely
    # ends with a period followed by 1-3 short capitalised words.
    title = re.sub(
        r"\.\s+[A-Z][A-Za-z]{0,11}(?:\s+[A-Z][A-Za-z]{0,11}){0,2}\.?\s*$",
        "",
        title,
    )
    # Strip a trailing `. <Journal> NN, NNN[-NNN]` tail — journal name
    # followed by volume and page numbers, no year. Complementary to the
    # year-anchored strip (which needs a year) and the trailing-abbrev
    # strip (which doesn't allow numbers). Covers "Proceedings of the
    # IEEE 86, 2278-", "Nat Nanotechnol 11, 693-699".
    title = re.sub(
        r"\.\s+[A-Z][A-Za-z ]{1,40}\s+\d{1,4}\s*,\s*\d+(?:[-–]\d+)?\s*$",
        "",
        title,
    )
    # Strip URLs anywhere in title (including space-broken URLs from PDF)
    title = re.sub(r"\s*https?://[\S\s]*$", "", title)
    # Strip leading "Author et al., " prefix
    title = re.sub(r"^[A-Z][\w.-]+\s+et\s+al\.\s*,?\s*", "", title)
    # Strip leading stranded initial — happens when an author's middle
    # initial bleeds into the title at citation-parse time, producing
    # "H. Memristors based on 2D materials..." (real title:
    # "Memristors based on 2D materials..."). Only strip when the next
    # token is title-cased and ≥4 chars, avoiding genus abbreviations
    # like "A. vulgaris" where the species name is lowercased.
    title = re.sub(r"^[A-Z]\.\s+(?=[A-Z][a-z]{3,})", "", title)
    # Strip leading "Surname, and Author, " (leaked last authors)
    title = re.sub(r"^[A-Z][a-z]+[-\w]*,\s+and\s+[A-Z].*?,\s+", "", title)
    # Strip leading "Surname, Initials" (leaked single author at start)
    title = re.sub(r"^[A-Z][a-z]+[-\w]*,\s+[A-Z]\.\s*[A-Z]?\.\s*", "", title)
    # Strip leading "Name, in YYYY" (conference)
    title = re.sub(r"^[A-Z][a-z]+[-\w]*,\s+in\s+", "In ", title)
    # Strip leading "Name, lowercase" (leaked author + venue)
    title = re.sub(r"^[A-Z][a-z]+[-\w]*,\s+(?=[a-z])", "", title)
    # Strip leading multi-author prefix: "A. Name, B. Name, Title"
    # or "First Last, F. Last, Title" (comma-separated author names).
    # Each "author" must either (a) contain an initial with period, or
    # (b) be at least two words (given + surname). Otherwise the pattern
    # matches leading adjectives in titles like "Flexible, Transparent,
    # and Wafer-Scale Artificial Synapse Array..." and chops the first
    # two words off the title.
    _author_with_initial = r"[A-Z]\.(?:\s*[A-Z]\.)*\s*[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
    _author_two_word = r"[A-Z][a-z]+\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
    _author_name = rf"(?:{_author_with_initial}|{_author_two_word})"
    title = re.sub(
        rf"^(?:{_author_name},\s*){{{2},}}",
        "", title,
    )
    # After stripping the leading author-list, a dangling "and " may be
    # left at the very start of the title (the Oxford comma's "and"
    # that introduced the last author). Drop it.
    title = re.sub(r"^(?:and|&)\s+", "", title, flags=re.IGNORECASE)
    # Strip trailing citation fragment: ", Small Sci 2, 2100049"
    # Requires volume + pages/article-number after journal name.
    title = re.sub(
        r",\s+[A-Z][a-z]+\.?\s+\d{1,4}\s*[,:]\s*\d+.*$", "", title,
    )
    # Strip trailing conference info after ". In: YYYY..." or ". In YYYY..."
    title = re.sub(r"\.\s+In[:\s]+\d{4}\b.*$", "", title)
    # Strip trailing IEEE journal citation fragments:
    # "..., IEEE Trans. Circuit Theory 18 (1971) 507-519"
    # Only when IEEE is followed by a journal abbreviation (Trans., J., Proc.)
    # and volume/year numbers. Preserves "IEEE 802.11" and "IEEE Access" in titles.
    title = re.sub(
        r",?\s*IEEE\s+(?:Trans|J|Proc)\b.*$", "", title,
    )
    # Collapse multiple spaces
    title = re.sub(r"\s{2,}", " ", title).strip()
    return title


def _title_dedup_key(title: str) -> str:
    """Normalize title for dedup: lowercase, strip punctuation/whitespace."""
    key = title.lower()
    key = re.sub(r"[^a-z0-9]", "", key)
    return key


_MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}


def _clean_bib_journal(journal: str) -> str:
    """Strip artifacts from journal field."""
    # Strip leading quotes and brackets (OCR artifacts from scanned PDFs)
    journal = journal.lstrip("'\"[{( ")
    # Collapse multiple spaces (OCR word spacing artifacts)
    journal = re.sub(r"\s{2,}", " ", journal)
    # Remove trailing ", vol. X-" or ", Vol." patterns
    journal = re.sub(r",?\s*[Vv]ol\.?\s*[A-Z0-9\-]*\s*$", "", journal).strip()
    # Remove trailing comma
    journal = journal.rstrip(",").strip()
    # Strip trailing month + year fragments (", Sept. 1969")
    _month_tail = r",?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s*\d*\s*$"
    journal = re.sub(_month_tail, "", journal, flags=re.IGNORECASE).strip()
    # Reject month names as journal ("July", "December", "May")
    if journal.lower() in _MONTH_NAMES:
        return ""
    return journal


def _entries_to_bibtex(entries: list[dict[str, str]]) -> str:
    db = BibDatabase()
    db.entries = entries
    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False
    return bibtexparser.dumps(db, writer)


def paper_to_bibtex(
    doc: Document, citations: list[dict] | None = None,
) -> str:
    """Build a minimal ``@article`` BibTeX entry from a Document."""
    return _entries_to_bibtex([_document_entry(doc)])


# ---------------------------------------------------------------------------
# Reference entries (from CrossRef-resolved citations)
# ---------------------------------------------------------------------------


_JOURNAL_FILTER_WORDS = {
    "trans", "ieee", "phys", "rev", "lett", "proc", "conf",
    "journal", "vol", "acm", "acs", "rsc",
}


def _reference_entry_from_citation(cit: object) -> dict[str, str] | None:
    """Build a BibTeX entry from a CitationEntry or legacy dict.

    Returns None if the citation lacks essential fields (title + authors).
    """
    # Support both CitationEntry (attr access) and legacy dict
    _g = getattr(cit, "__getitem__", None)
    if _g:  # dict-like
        d = cit
    else:
        d = cit.to_dict()

    title = _clean_bib_title(_as_text(d.get("title")))
    authors = _as_list(d.get("authors"))
    if not title or not authors:
        return None
    year = d.get("year")

    # Reject genuinely unrecoverable titles (after cleaning above)
    # These catch text that _clean_bib_title couldn't fix.
    if title.isupper() and len(title.split()) <= 2:
        return None
    # Citation-coordinate title: 6 or fewer words AND contains `<digit>, <digit>`
    # — the "title" is just journal+vol+pages with no prose content.
    if (
        len(title.split()) <= 6
        and re.search(r"\d+\s*,\s*\d{3,}", title)
    ):
        return None
    # Web-PDF anchor IDs in the title mean the upstream reference-string
    # parser emitted a mangled token soup (HTML `<a href="#sbref0021">`
    # fragments with the tags stripped but the anchor IDs intact). The
    # title is structurally unusable; reject the whole entry rather than
    # try to clean a corrupt record.
    if re.search(
        # `/sbref0021`, `#sbref12`, `/fn5`, `/anchor3`, `90110-9/word\d+`,
        # or the trailing `NNNNN-X)` / `NNNN-XX)` pattern seen when
        # anchor IDs were rendered as ")-suffixed numeric blobs.
        r"/(?:sbref|ref|anchor|fn|note|sec|bib)\d+"
        r"|\d+-\d+/\w+\d+"
        r"|,\s*\d{4,}-[A-Z0-9]\)",
        title,
        re.I,
    ):
        return None
    # Citation-marker prefix (`>[N]`, `>N.`, `[N]`, `N.`) means the
    # whole title is a reference-list line the parser didn't strip.
    # Also catches `>[\[N\] V](#page-0-0)...` markdown-link wrappings
    # around the marker.
    if re.match(
        r"^\s*>?\s*[\[\(]\d+[\]\)]"
        r"|^\s*>?\s*\d+\.\s+[A-Z]"
        r"|^\s*>?\s*\[\\\[\d",
        title,
    ):
        return None
    # Markdown-link syntax `](` in the title never appears in a real
    # paper title — it means a banner/TOC line with a hyperlink leaked
    # into the title slot.
    if "](" in title:
        return None
    # Citation-fragment title: the "title" is really a comma-separated
    # list of name-shaped tokens (e.g. `Galdin-Retailleau, D. Querlioz`
    # where the parser kept the tail of an author list as the title).
    # Structural test: every comma-separated piece looks like a name AND
    # at least one piece contains a period-initial (`[A-Z]\.`). The
    # period-initial requirement avoids false positives on genuine
    # comma-list adjective titles like "Flexible, Transparent,
    # Wafer-Scale ..." where no piece carries an author-initial.
    pieces = [p.strip() for p in title.split(",") if p.strip()]
    if (
        len(pieces) >= 2
        and all(_looks_like_author_fragment(p) for p in pieces)
        and any(re.search(r"\b[A-Z]\.", p) for p in pieces)
    ):
        return None
    # Journal+year fragment ("Nanoscale, 2016, 8: 1383")
    if re.match(r"^[A-Z][a-z]+,\s+\d{4}", title):
        return None
    # Journal+vol+pages only ("Mater. 25 1774-9")
    if re.match(r"^[A-Z][a-z]+\.?\s+\d+\s+\d+", title) and len(title) < 30:
        return None
    # Journal-coordinate signature anywhere in a short title: "Vol(Issue):pp-pp"
    # Examples: "Oxid Met 2(1):59–99", "JAP 102(7):074114-1". Safe: real titles
    # almost never contain "\d+\(\d+\):\d+" verbatim.
    if re.search(r"\d+\s*\(\d+\)\s*:\s*\d+", title) and len(title) < 40:
        return None
    # Journal + year + volume + page triplet: "Circuit Theory 1971, 18, 507".
    if re.match(r"^[A-Z][\w\.\s]{2,30}\s+\d{4}\s*,\s*\d+\s*,\s*\d+", title):
        return None
    # Book-chapter fragment: starts with "In " and contains a publisher name.
    # Examples: "In Handbook of Memristor Networks, Springer, Berlin, Germany..."
    # "In Proc. of IEDM, Elsevier...". These are citation tails, not titles.
    if re.match(
        r"^In\s+.+,\s*(Springer|Wiley|Elsevier|Academic|CRC|Taylor|"
        r"Oxford|Cambridge|MIT|World Scientific|Nova|Pergamon)",
        title,
    ):
        return None
    # Conference location/date only ("(ASP-DAC), Incheon, ...")
    if re.match(r"^\(?[A-Z]{2,6}[-\s]?[A-Z]*\)?\s*,?\s*\w+,.*\d{4}", title):
        return None
    # Still has doi.org or too many commas after cleaning
    if "doi.org" in title or title.count(",") > 5:
        return None
    # Final structural reject: a real paper title doesn't contain a
    # 4-digit year. If one survived all the cleanup passes, the cleanup
    # couldn't recover prose from the citation fragment — drop it
    # rather than emit a bib entry whose title is still a citation.
    if re.search(r"\b(?:19|20)\d{2}\b", title):
        return None

    # Filter structurally-broken author tokens: any token containing a
    # lowercase content word that isn't a recognised name particle has
    # either prose bled into it ("L. On the gradual unipolar") or a
    # running-header colon ("M. Erratum:"). Drop those tokens; reject
    # the entry if nothing clean survives.
    authors = [a for a in authors if not _author_has_prose_residue(a)]
    if not authors:
        return None

    # For heuristic-only citations, validate strictly
    api_confirmed = (
        d.get("crossref_resolved")
        or d.get("doi_resolved")
        or d.get("resolution") in ("openalex", "crossref", "doi")
    )
    if not api_confirmed:
        if not year:
            return None
        if len(title) < 15 or len(title.split()) < 3:
            return None
        if title[0].islower() or title[0].isdigit():
            return None
        from .metadata import _looks_like_journal

        clean_authors = [
            a for a in authors
            if len(a.split()) >= 2
            and not _looks_like_journal(a)
            and not any(ch.isdigit() for ch in a)
            and not any(w.lower().rstrip(".") in _JOURNAL_FILTER_WORDS for w in a.split())
        ]
        if not clean_authors:
            return None
        authors = clean_authors

    doi = _clean_doi(d.get("doi"))

    authors = [_clean_author_name(a) for a in authors]
    first_author = authors[0].split()[-1] if authors else "unknown"
    base = _sanitize_id(f"ref_{year}_{first_author}_{title[:30]}")

    entry: dict[str, str] = {
        "ENTRYTYPE": "article",
        "ID": base,
        "title": title,
        "author": " and ".join(authors),
    }
    if year:
        entry["year"] = str(year)
    if doi:
        entry["doi"] = doi
    venue = d.get("venue") or ""
    if venue:
        venue = _clean_bib_journal(venue)
    if venue and len(venue) >= 3:
        _add_optional(entry, "journal", venue)
    # Suppress volume when it equals year (common heuristic-parse error:
    # "Manage. Sci 1960, 324-342" -> volume=1960, year=1960).
    volume = d.get("volume")
    if volume and str(volume) != str(year):
        _add_optional(entry, "volume", volume)
    _add_optional(entry, "pages", d.get("pages"))
    _add_optional(entry, "publisher", d.get("publisher"))
    return entry


# ---------------------------------------------------------------------------
# Citation index
# ---------------------------------------------------------------------------


def _index_record(
    *,
    bibkey: str,
    kind: str,
    cit: dict | None = None,
    entry: dict[str, str] | None = None,
    doc_id: str = "",
) -> dict[str, object]:
    """Build one record for citation_index.json."""
    record: dict[str, object] = {
        "bibkey": bibkey,
        "kind": kind,
        "title": "",
        "authors": [],
        "year": "",
        "venue": "",
        "doi": "",
        "source_doc_ids": [],
        "citation_ords": [],
    }
    if doc_id:
        record["doc_id"] = doc_id

    # Populate from BibTeX entry (source docs)
    if entry:
        record["title"] = _as_text(entry.get("title"))
        record["authors"] = _as_list(entry.get("author"))
        record["year"] = _as_text(entry.get("year"))
        record["venue"] = _as_text(entry.get("journal"))
        record["doi"] = _clean_doi(entry.get("doi"))
        record["url"] = _as_text(entry.get("url"))

    # Populate from citation dict (references)
    if cit:
        record["title"] = _as_text(cit.get("title"))
        record["authors"] = _as_list(cit.get("authors"))
        record["year"] = str(cit["year"]) if cit.get("year") else ""
        record["venue"] = _as_text(cit.get("venue"))
        record["doi"] = _clean_doi(cit.get("doi"))
        if cit.get("raw_text"):
            record["raw_text"] = _as_text(cit["raw_text"])
        if cit.get("crossref_score"):
            record["crossref_score"] = cit["crossref_score"]

    return record


def build_citation_index(
    corpus: Corpus,
    docs: list[Document],
    *,
    resolve_doi: bool = False,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    """Build BibTeX entries plus the structured citation index.

    Returns ``(source_entries, reference_entries, index_payload)``.
    Reference entries are created only for CrossRef-resolved citations
    with valid title + authors. Unresolved citations appear in the index
    with ``kind: "unresolved"`` for corpus-internal matching only.
    """
    source_entries: list[dict[str, str]] = []
    reference_entries: list[dict[str, str]] = []
    entries: dict[str, dict[str, object]] = {}
    doc_bibkeys: dict[str, str] = {}
    doc_citations: dict[str, list[str]] = {}
    doi_bibkeys: dict[str, str] = {}
    title_bibkeys: dict[str, str] = {}  # normalized title -> bibkey (dedup)
    source_seen: dict[str, int] = {}
    ref_seen: dict[str, int] = {}

    # Phase 1: enrich docs and build source entries.
    #
    # DOI content negotiation is the long pole in wave D (1 HTTP RTT per
    # doc * 200+ docs). Pre-fetch all DOIs concurrently (10-way semaphore)
    # before the serial enrichment loop so ``_with_fallback_metadata`` can
    # look up results from the batch cache instead of blocking on
    # individual requests. ~10x speed-up on corpora with many DOIs.
    batch_cache: dict[str, dict[str, object]] = {}
    if resolve_doi and doi_lookup is None:
        # Prefetch DOIs from both explicit metadata AND the pymupdf fallback
        # for PDF-kind docs lacking a DOI. Doing the fallback up-front here
        # (instead of lazily inside _with_fallback_metadata) means every
        # discovered DOI hits the async batch path, not the sync one-off.
        from .metadata import extract_pdf_doi_fallback

        prefetch_dois: list[str] = []
        for doc in docs:
            meta = doc.metadata or {}
            raw = meta.get("doi")
            doi = _clean_doi(raw) if raw else ""
            if not doi and doc.source_path:
                src = Path(doc.source_path)
                if src.suffix.lower() == ".pdf":
                    recovered = extract_pdf_doi_fallback(src)
                    if recovered:
                        doi = _clean_doi(recovered)
            if doi:
                prefetch_dois.append(doi)
        db_path = corpus.root / ".citestore.db" if corpus.root else None
        if prefetch_dois and db_path is not None:
            from ..util.doi_resolver import resolve_many

            batch_cache = resolve_many(prefetch_dois, cache_path=db_path)

        def _cached_lookup(doi: str) -> dict[str, object]:
            key = doi.lower()
            if key in batch_cache:
                return batch_cache[key]
            # DOI discovered at refresh time that wasn't in the initial
            # prefetch (rare; only when extract_document_doi inside
            # _with_fallback_metadata finds a DOI neither in metadata
            # nor in pymupdf's scan). Route through the shared resolver.
            from ..util.doi_resolver import resolve_one

            result = resolve_one(doi, cache_path=db_path) if db_path else {}
            batch_cache[key] = result
            return result

        doi_lookup = _cached_lookup

    enriched_docs = [
        _with_fallback_metadata(
            corpus, doc,
            resolve_doi=resolve_doi,
            doi_lookup=doi_lookup,
        )
        for doc in docs
    ]

    # DOI-based dedup: multiple source files (e.g. a .pdf and .docx of the
    # same paper) can produce separate Document objects with identical DOIs.
    # Emit one bib entry per DOI, preferring the document with richer
    # metadata (valid title, more authors).
    #
    # DOI-less fallback: for pre-DOI papers (pre-~2000) our corpus
    # convention `[YYYY Author] Title.ext` is a reliable identity key.
    # Group DOI-less docs by (year, author-surname, normalised-title) and
    # emit one canonical entry per group; duplicate doc_ids map to the
    # surviving bibkey so CITES / bib-index lookups still resolve.
    from .metadata import is_junk_title

    def _doc_quality(entry: dict[str, str]) -> tuple[int, int]:
        """Sort key for picking the better of two duplicate docs. Higher is
        better: (title_is_real, author_count)."""
        title = _as_text(entry.get("title"))
        title_ok = int(bool(title) and not is_junk_title(title))
        n_authors = len(_as_list(entry.get("author")))
        return (title_ok, n_authors)

    # Build entries once upfront so the dedup pass isn't O(n^2) in
    # _document_entry (which re-cleans titles, authors, etc.).
    entry_by_doc: dict[str, dict[str, str]] = {
        doc.id: _document_entry(doc) for doc in enriched_docs
    }

    # First pass: pick the canonical doc per DOI.
    canonical_by_doi: dict[str, tuple[Document, dict[str, str]]] = {}
    no_doi_docs: list[Document] = []
    for doc in enriched_docs:
        entry = entry_by_doc[doc.id]
        doi = _clean_doi(entry.get("doi"))
        if not doi:
            no_doi_docs.append(doc)
            continue
        prev = canonical_by_doi.get(doi)
        if prev is None or _doc_quality(entry) > _doc_quality(prev[1]):
            canonical_by_doi[doi] = (doc, entry)

    # Build reverse index doi -> [doc.id] so the "point duplicates at
    # canonical bibkey" step is O(n) instead of O(n^2).
    docs_by_doi: dict[str, list[str]] = defaultdict(list)
    for doc in enriched_docs:
        d = _clean_doi(entry_by_doc[doc.id].get("doi"))
        if d:
            docs_by_doi[d].append(doc.id)

    # Second pass: emit one bib entry per canonical doc, then one per
    # DOI-less doc. Map every duplicate doc_id to the surviving bibkey so
    # downstream CITES/bib-index lookups still resolve.
    for doi, (doc, entry) in canonical_by_doi.items():
        entry["ID"] = _unique_bibkey(entry["ID"], source_seen)
        source_entries.append(entry)
        doc_bibkeys[doc.id] = entry["ID"]
        entries[entry["ID"]] = _index_record(
            bibkey=entry["ID"], kind="source",
            entry=entry, doc_id=doc.id,
        )
        doi_bibkeys[doi] = entry["ID"]
        for other_id in docs_by_doi[doi]:
            if other_id != doc.id:
                doc_bibkeys[other_id] = entry["ID"]
    # Group DOI-less docs by filename-convention key; pick a canonical
    # per group using the same quality rule as the DOI dedup.
    def _filename_key(doc: Document) -> tuple[int | None, str, str] | None:
        name = Path(doc.source_path).name
        year, author, title = parse_filename(name)
        if not (year or author or title):
            return None
        norm_author = (author or "").strip().casefold()
        norm_title = _TITLE_TOKEN_RE.sub(
            "", (title or "").casefold(),
        ) if title else ""
        # Second casefold+compact pass using the title-token pattern (same
        # used for bibkey construction) so "The_missing_circuit_element"
        # and "the missing circuit element" hash identically.
        norm_title = "".join(_TITLE_TOKEN_RE.findall((title or "").casefold()))
        if not norm_title:
            return None
        return (year, norm_author, norm_title)

    canonical_by_fn: dict[tuple, tuple[Document, dict[str, str]]] = {}
    unkeyed_docs: list[Document] = []
    fn_key_for_doc: dict[str, tuple] = {}
    for doc in no_doi_docs:
        key = _filename_key(doc)
        if key is None:
            unkeyed_docs.append(doc)
            continue
        fn_key_for_doc[doc.id] = key
        entry = entry_by_doc[doc.id]
        prev = canonical_by_fn.get(key)
        if prev is None or _doc_quality(entry) > _doc_quality(prev[1]):
            canonical_by_fn[key] = (doc, entry)

    # Emit canonicals, then map every duplicate doc.id to the surviving
    # bibkey. Same pattern as the DOI dedup above.
    docs_by_fn: dict[tuple, list[str]] = defaultdict(list)
    for doc in no_doi_docs:
        key = fn_key_for_doc.get(doc.id)
        if key is not None:
            docs_by_fn[key].append(doc.id)

    for key, (doc, entry) in canonical_by_fn.items():
        entry = _document_entry(doc)
        entry["ID"] = _unique_bibkey(entry["ID"], source_seen)
        source_entries.append(entry)
        doc_bibkeys[doc.id] = entry["ID"]
        entries[entry["ID"]] = _index_record(
            bibkey=entry["ID"], kind="source",
            entry=entry, doc_id=doc.id,
        )
        for other_id in docs_by_fn[key]:
            if other_id != doc.id:
                doc_bibkeys[other_id] = entry["ID"]

    for doc in unkeyed_docs:
        entry = _document_entry(doc)
        entry["ID"] = _unique_bibkey(entry["ID"], source_seen)
        source_entries.append(entry)
        doc_bibkeys[doc.id] = entry["ID"]
        entries[entry["ID"]] = _index_record(
            bibkey=entry["ID"], kind="source",
            entry=entry, doc_id=doc.id,
        )

    # Phase 2: process citations from each doc
    from .citations import repair_doi

    for doc in enriched_docs:
        cited_keys: list[str] = []
        for cit_obj in doc.citations:
            cit = cit_obj.to_dict() if hasattr(cit_obj, "to_dict") else cit_obj
            bibkey = None

            # Heal DOIs that were persisted truncated by the pre-fix extractor
            # (``10.1038/s41467-``, ``10.1016/S0893-6080(97``). Safe: only
            # replaces the stored DOI when raw_text yields a longer / better
            # balanced candidate.
            repaired = repair_doi(cit.get("raw_text") or "", cit.get("doi") or "")
            if repaired and repaired != cit.get("doi"):
                cit["doi"] = repaired

            # Try to match to an existing source doc by DOI
            cit_doi = _clean_doi(cit.get("doi"))
            if cit_doi and cit_doi in doi_bibkeys:
                bibkey = doi_bibkeys[cit_doi]

            # Build reference entry from enriched citation data
            if bibkey is None:
                ref_entry = _reference_entry_from_citation(cit)
                if ref_entry is not None:
                    # Dedup by DOI
                    ref_doi = _clean_doi(ref_entry.get("doi"))
                    if ref_doi and ref_doi in doi_bibkeys:
                        bibkey = doi_bibkeys[ref_doi]
                    # Dedup by normalized title
                    if bibkey is None:
                        tkey = _title_dedup_key(ref_entry.get("title", ""))
                        if tkey and tkey in title_bibkeys:
                            bibkey = title_bibkeys[tkey]
                    # New entry
                    if bibkey is None:
                        ref_entry["ID"] = _unique_bibkey(
                            ref_entry["ID"], ref_seen,
                        )
                        bibkey = ref_entry["ID"]
                        reference_entries.append(ref_entry)
                        entries[bibkey] = _index_record(
                            bibkey=bibkey, kind="reference", cit=cit,
                        )
                        if ref_doi:
                            doi_bibkeys[ref_doi] = bibkey
                        tkey = _title_dedup_key(ref_entry.get("title", ""))
                        if tkey:
                            title_bibkeys[tkey] = bibkey

            # Unresolved: no .bib entry, just index record for matching
            if bibkey is None:
                raw = _as_text(cit.get("raw_text"))
                h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
                bibkey = f"unresolved_{h}"
                if bibkey not in entries:
                    entries[bibkey] = _index_record(
                        bibkey=bibkey, kind="unresolved", cit=cit,
                    )

            # Link citation to its bibkey
            if bibkey not in cited_keys:
                cited_keys.append(bibkey)
            record = entries.get(bibkey)
            if record is not None:
                source_ids = set(record.get("source_doc_ids", []))
                source_ids.add(doc.id)
                record["source_doc_ids"] = sorted(source_ids)
                ords = list(record.get("citation_ords", []))
                marker = {"doc_id": doc.id, "ord": cit.get("ord")}
                if marker not in ords:
                    ords.append(marker)
                record["citation_ords"] = ords
                if cit.get("raw_text") and not record.get("raw_text"):
                    record["raw_text"] = _as_text(cit["raw_text"])

        doc_citations[doc.id] = cited_keys

    return source_entries, reference_entries, {
        "schema_version": _CITATION_INDEX_VERSION,
        "entries": entries,
        "doc_bibkeys": doc_bibkeys,
        "doc_citations": doc_citations,
        "doi_bibkeys": doi_bibkeys,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def write_corpus_bibtex(
    corpus: Corpus,
    docs: list[Document],
    *,
    resolve_doi: bool = False,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> Path:
    """Write ``corpus_papers.bib`` containing one entry per Document."""
    corpus.ensure()
    bib_path = corpus.library_bib_path
    seen: dict[str, int] = {}
    entries: list[dict[str, str]] = []
    for doc in docs:
        enriched = _with_fallback_metadata(
            corpus, doc,
            resolve_doi=resolve_doi,
            doi_lookup=doi_lookup,
        )
        entry = _document_entry(enriched)
        entry["ID"] = _unique_bibkey(entry["ID"], seen)
        entries.append(entry)
    from wikify.corpus.chunks import atomic_write_text

    atomic_write_text(bib_path, _entries_to_bibtex(entries))
    return bib_path


def write_corpus_bibliography(
    corpus: Corpus,
    docs: list[Document],
    *,
    resolve_doi: bool = False,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> dict[str, Path]:
    """Write corpus_papers.bib, cited_works.bib, and citations.json."""
    corpus.ensure()
    source_entries, reference_entries, index = build_citation_index(
        corpus, docs,
        resolve_doi=resolve_doi,
        doi_lookup=doi_lookup,
    )

    from wikify.corpus.chunks import atomic_write_text

    atomic_write_text(
        corpus.library_bib_path, _entries_to_bibtex(source_entries),
    )
    atomic_write_text(
        corpus.references_bib_path, _entries_to_bibtex(reference_entries),
    )
    atomic_write_text(
        corpus.citation_index_path,
        json.dumps(index, indent=2, sort_keys=True) + "\n",
    )
    return {
        "library": corpus.library_bib_path,
        "references": corpus.references_bib_path,
        "citation_index": corpus.citation_index_path,
    }


# ---------------------------------------------------------------------------
# BibTeX parsing helpers (used by the shared DOI resolver's doi.org path)
# ---------------------------------------------------------------------------


# BibTeX allows ``month=jan`` style bare-word macros; bibtexparser does not
# define these by default, so CrossRef/ACM records containing them used to
# fail parsing entirely (observed on ACM responses for e.g. 10.1145/...).
# Strip the month field with a regex instead of teaching the parser every
# macro — we never consume ``month`` anyway and the stripped value contains
# the fields we actually need.
_BIBTEX_MONTH_RE = re.compile(
    r",\s*month\s*=\s*[a-z]+\s*(?=,|\})", re.IGNORECASE,
)

def _metadata_from_bibtex_entry(bibtex_text: str) -> dict[str, object]:
    """Parse a single BibTeX entry string into a metadata dict."""
    cleaned = _BIBTEX_MONTH_RE.sub("", bibtex_text)
    try:
        db = bibtexparser.loads(cleaned)
    except Exception:
        return {}
    if not db.entries:
        return {}
    entry = db.entries[0]
    result: dict[str, object] = {}
    if entry.get("title"):
        result["title"] = _clean_title(entry["title"])
    if entry.get("author"):
        result["authors"] = _as_list(entry["author"])
    for key in ("journal", "year", "volume", "pages", "publisher", "issn"):
        if entry.get(key):
            result[key] = _as_text(entry[key])
    # Conference proceedings use booktitle in place of journal; map for
    # our downstream pipeline which keys off journal/venue.
    if entry.get("booktitle") and not result.get("journal"):
        result["journal"] = _as_text(entry["booktitle"])
    if result.get("journal"):
        result["venue"] = _clean_venue(result["journal"])
    return result


# ---------------------------------------------------------------------------
# Metadata fallback (for library.bib enrichment)
# ---------------------------------------------------------------------------


def _with_fallback_metadata(
    corpus: Corpus,
    doc: Document,
    *,
    resolve_doi: bool,
    doi_lookup: Callable[[str], dict[str, object]] | None,
) -> Document:
    """Fill missing bibliographic fields from markdown and optional DOI."""
    original_metadata = dict(doc.metadata or {})
    metadata = dict(original_metadata)
    source_path = Path(doc.source_path) if doc.source_path else Path()
    _, fn_author, _ = parse_filename(source_path.name)

    if metadata.get("doi"):
        metadata["doi"] = _clean_doi(metadata.get("doi"))
    for venue_key in ("venue", "journal", "publicationTitle"):
        if metadata.get(venue_key):
            metadata[venue_key] = _clean_venue(
                _as_text(metadata.get(venue_key)),
            )

    needs_publication = not (
        metadata.get("venue")
        or metadata.get("journal")
        or metadata.get("volume")
        or metadata.get("pages")
    )
    needs_doi = not metadata.get("doi")
    needs_authors = _authors_need_fallback(metadata, fn_author)
    needs_title = _title_needs_fallback(doc.title)
    # If we have a DOI, don't early-return even when every local field
    # looks "present" — the DOI-authoritative merge needs to run so
    # junk-but-non-empty values (ISSN lines in the venue slot, etc.)
    # get overwritten with the publisher-registered truth.
    has_doi = bool(metadata.get("doi"))
    if (
        not needs_publication
        and not needs_doi
        and not needs_authors
        and not needs_title
        and not has_doi
        and metadata == original_metadata
    ):
        return doc

    text = _read_doc_markdown(corpus, doc)
    title = doc.title

    if text and needs_title:
        from .metadata import choose_document_title

        # Filename-first priority: `[YYYY Author] Real Title.ext` is
        # authoritative. Heuristic heading extraction is the fallback.
        chosen = choose_document_title(text, source_path)
        if chosen and not _title_needs_fallback(chosen):
            title = _clean_title(chosen)
    if text and needs_authors:
        from .metadata import validate_authors_against_filename

        authors = extract_authors_from_markdown(text, fn_author=fn_author)
        authors = validate_authors_against_filename(authors, fn_author)
        if authors:
            metadata["authors"] = authors
    if text and needs_publication:
        for key, value in extract_publication_fields(text).items():
            if not metadata.get(key):
                metadata[key] = value
    if text and needs_doi:
        doi = extract_document_doi(text)
        if doi:
            metadata["doi"] = doi
    # pymupdf fallback: Marker strips DOIs printed in header/footer layout
    # bands, so they never reach the cached markdown. Re-scan the source PDF
    # directly; fixes ~80% of otherwise-DOI-less PDF entries at refresh time.
    if needs_doi and not metadata.get("doi") and source_path.suffix.lower() == ".pdf":
        from .metadata import extract_pdf_doi_fallback

        doi = extract_pdf_doi_fallback(source_path)
        if doi:
            metadata["doi"] = doi

    clean_doi = _clean_doi(metadata.get("doi"))
    if clean_doi:
        metadata["doi"] = clean_doi

    if resolve_doi and clean_doi:
        if doi_lookup is None:
            from ..util.doi_resolver import resolve_one

            db_path = corpus.root / ".citestore.db" if corpus.root else None
            external = (
                resolve_one(clean_doi, cache_path=db_path) if db_path else {}
            )
        else:
            external = doi_lookup(clean_doi)
        _merge_external_metadata(
            metadata,
            external,
            prefer_authors=_authors_need_fallback(metadata, fn_author),
        )

    # Sync Document.title with metadata["title"] — _document_entry reads
    # doc.title, but _merge_external_metadata writes to metadata["title"].
    # Without this sync, a DOI-returned title silently fails to reach the
    # bibtex entry even though it's in the metadata dict.
    meta_title = _as_text(metadata.get("title"))
    if meta_title and meta_title != title:
        title = _clean_title(meta_title)

    if metadata == original_metadata and title == doc.title:
        return doc
    return replace(doc, title=title, metadata=metadata)


_JUNK_AUTHOR_RE = re.compile(
    r"(?:\bAir\s+Force\b|\bResearch\s+Laboratory\b|\bUniversity\b|"
    r"\bInstitute\s+of\b|\bSchool\s+of\b|\bDepartment\s+of\b|"
    r"\bPolytechnic\b|\bCollege\s+of\b|\.pdf\b|\.docx\b)",
    re.IGNORECASE,
)


def _authors_need_fallback(metadata: dict, fn_author: str | None) -> bool:
    authors = _as_list(metadata.get("authors"))
    if not authors:
        return True
    if len(authors) == 1:
        author = authors[0].strip()
        if fn_author and author.casefold() == fn_author.casefold():
            return True
        if len(author.split()) == 1:
            return True
    # Any entry containing an institution marker, file extension, or
    # similar non-person signal is a parse artifact; re-derive the list.
    if any(_JUNK_AUTHOR_RE.search(a) for a in authors):
        return True
    return False


_GARBAGE_TITLE_RE = re.compile(
    r"^(\[\d{4}\s+[^\]]+\]"
    r"|EDITED\s+BY"
    r"|RESEARCH\s+ARTICLE"
    r"|ORIGINAL\s+(ARTICLE|PAPER|RESEARCH)"
    r"|MEETING[-\s]?REPORT"
    r"|PAPER\b"
    r"|ARTICLE\b"
    r")",
    re.I,
)


def _title_needs_fallback(title: str) -> bool:
    clean = title.strip()
    if not clean or len(clean) < 10:
        return True
    if clean.isupper():
        return True
    # Delegate placeholder detection ("Word Document", "Untitled", section
    # headers like "1 Introduction", repository banners, markdown links) to
    # the shared is_junk_title vocabulary so refresh fixes are picked up on
    # cached docs without re-parse.
    from .metadata import is_junk_title

    if is_junk_title(clean):
        return True
    return bool(_GARBAGE_TITLE_RE.match(clean))


def _best_markdown_title(md_text: str, fallback_title: str) -> str:
    """Pick the heading that best matches the filename-derived title."""
    target_tokens = set(
        _normalise_title_key(
            _strip_filename_title_prefix(fallback_title),
        ).split(),
    )
    best: tuple[float, int, str] = (0.0, 0, "")
    for heading in _markdown_headings(md_text):
        if _heading_is_generic(heading):
            continue
        tokens = set(_normalise_title_key(heading).split())
        if not tokens:
            continue
        overlap = len(tokens & target_tokens)
        score = overlap / max(len(target_tokens), 1)
        if score > best[0] or (score == best[0] and len(heading) > best[1]):
            best = (score, len(heading), heading)
    if best[0] >= 0.25:
        return best[2]
    return ""


def _markdown_headings(md_text: str) -> list[str]:
    headings: list[str] = []
    in_frontmatter = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        match = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if not match:
            continue
        heading = _clean_title(
            re.sub(r"[\ue000-\uf8ff]", "", match.group(1)),
        )
        if heading:
            headings.append(heading)
    return headings


def _strip_filename_title_prefix(title: str) -> str:
    match = re.match(
        r"^\[\d{4}\s+[^\]]+\]\s*[-\u2013\u2014]?\s*(.+)$", title.strip(),
    )
    return match.group(1) if match else title


def _heading_is_generic(heading: str) -> bool:
    lower = heading.casefold().strip()
    generic = {
        "article", "articles", "letters", "paper", "review",
        "open access", "research article",
        "articles you may be interested in",
        "references", "bibliography", "affiliations", "abstract",
    }
    if lower in generic:
        return True
    journalish = r"\b(journal|science|nature|iscience|flexmat)\b"
    if len(heading.split()) <= 2 and re.search(journalish, lower):
        return True
    return False


def _merge_external_metadata(
    metadata: dict[str, object],
    external: dict[str, object],
    *,
    prefer_authors: bool,
) -> None:
    """Merge DOI-content-negotiation data over locally extracted metadata.

    doi.org content negotiation returns the publisher-registered canonical
    record. It is the authoritative source for bibliographic-identity
    fields when a DOI is available, so we overwrite local values with
    DOI values for: title, journal/venue, volume, pages, publisher, issn,
    url. Summary and year are kept local-preferring (summary because the
    DOI record sometimes carries marketing copy; year because our filename
    convention is reliable and the DOI response sometimes lacks it).

    Authors follow the existing prefer_authors rule: DOI wins when the
    local list was flagged as junk (_authors_need_fallback), otherwise
    local wins since DOI records often abbreviate given names.
    """
    doi_authoritative = (
        "title", "journal", "venue", "volume", "pages", "publisher",
        "issn", "url",
    )

    for key, value in external.items():
        if not value:
            continue
        if key == "authors":
            if prefer_authors:
                metadata[key] = value
            elif not metadata.get(key):
                metadata[key] = value
            continue
        if key in doi_authoritative:
            metadata[key] = value
            continue
        # For everything else (summary, year, etc.), keep local when present.
        if not metadata.get(key):
            metadata[key] = value



def _read_doc_markdown(corpus: Corpus, doc: Document) -> str:
    candidates = [corpus.markdown_dir / f"{doc.id}.md"]
    if doc.markdown_path:
        candidates.append(Path(doc.markdown_path))
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""
