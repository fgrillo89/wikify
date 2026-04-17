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
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter

from ..models import Document
from ..paths import CorpusPaths
from .metadata import (
    extract_authors_from_markdown,
    extract_document_doi,
    extract_publication_fields,
    first_heading,
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


# Lowercase name particles preserved during capitalization.
_NAME_PARTICLES = frozenset({
    "van", "von", "der", "de", "da", "di", "la", "le", "du",
    "del", "den", "dos", "el", "al", "bin", "ibn",
})


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


def _is_plausible_author(name: str) -> bool:
    """Check if a string looks like an author name (not body text)."""
    name = name.strip()
    if not name or len(name) < 2:
        return False
    words = name.split()
    if len(words) > 5:
        return False
    # Must start with uppercase
    if not words[0][0].isupper():
        return False
    # Must not contain common non-name words
    noise = {"particular", "variabilities", "abstract", "results", "however",
             "therefore", "moreover", "furthermore", "respectively", "simultaneously"}
    if any(w.lower() in noise for w in words):
        return False
    # Must not contain chemical formulas or numbers
    if re.search(r"\d|[A-Z]{2,}\d", name):
        return False
    return True


def _document_entry(doc: Document) -> dict[str, str]:
    metadata = doc.metadata or {}
    authors_list = [
        _clean_author_name(a) for a in _as_list(metadata.get("authors"))
        if _is_plausible_author(a)
    ]
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


def _clean_bib_title(title: str) -> str:
    """Clean a title for BibTeX output: strip HTML, newlines, leaked metadata."""
    # Collapse newlines to spaces
    title = title.replace("\n", " ").replace("\r", " ")
    # Convert HTML subscript/superscript to LaTeX (including <inf> variant)
    title = re.sub(r"<(?:sub|inf)>(.*?)</(?:sub|inf)>", r"$_{\1}$", title, flags=re.I | re.S)
    title = re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", title, flags=re.I | re.S)
    # Strip remaining HTML tags
    title = re.sub(r"<[^>]+>", "", title)
    # Strip trailing ". Journal, Year, Vol, Pages" (Chinese-style citations)
    title = re.sub(
        r"\.\s+[A-Z][a-z]+[^,]*,\s*\d{4}\s*,\s*\d+.*$", "", title,
    )
    # Strip URLs anywhere in title (including space-broken URLs from PDF)
    title = re.sub(r"\s*https?://[\S\s]*$", "", title)
    # Strip leading "Author et al., " prefix
    title = re.sub(r"^[A-Z][\w.-]+\s+et\s+al\.\s*,?\s*", "", title)
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
    corpus: CorpusPaths,
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
        if prefetch_dois:
            batch_cache = resolve_doi_metadata_batch(
                prefetch_dois, cache_path=db_path,
            )

        # Cache-miss fallback uses the sync single-DOI helper — spinning up
        # a fresh asyncio event loop + httpx.AsyncClient for a single
        # lookup costs ~250 ms (profiled: 50 misses ≈ 13 s init overhead).
        def _cached_lookup(doi: str) -> dict[str, object]:
            key = doi.lower()
            if key in batch_cache:
                return batch_cache[key]
            result = resolve_doi_metadata(doi) or {}
            batch_cache[key] = result
            # Persist to the shared cache so subsequent refreshes skip the
            # network entirely, matching the batch path's contract.
            if result and db_path is not None:
                try:
                    from ..citestore.db import DOICache

                    with DOICache(db_path) as cache:
                        cache.put(doi, result, source="doi.org")
                except Exception:  # noqa: BLE001
                    pass
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
    # metadata (valid title, more authors). Docs without a DOI are always
    # emitted — we have no reliable dedup signal for them.
    from .metadata import is_junk_title

    def _doc_quality(entry: dict[str, str]) -> tuple[int, int]:
        """Sort key for picking the better of two duplicate docs. Higher is
        better: (title_is_real, author_count)."""
        title = _as_text(entry.get("title"))
        title_ok = int(bool(title) and not is_junk_title(title))
        n_authors = len(_as_list(entry.get("author")))
        return (title_ok, n_authors)

    # First pass: pick the canonical doc per DOI.
    canonical_by_doi: dict[str, tuple[Document, dict[str, str]]] = {}
    no_doi_docs: list[Document] = []
    for doc in enriched_docs:
        entry = _document_entry(doc)
        doi = _clean_doi(entry.get("doi"))
        if not doi:
            no_doi_docs.append(doc)
            continue
        prev = canonical_by_doi.get(doi)
        if prev is None or _doc_quality(entry) > _doc_quality(prev[1]):
            canonical_by_doi[doi] = (doc, entry)

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
        # Point any other docs sharing this DOI at the canonical bibkey.
        for other in enriched_docs:
            if other.id != doc.id and _clean_doi(
                _document_entry(other).get("doi")
            ) == doi:
                doc_bibkeys[other.id] = entry["ID"]
    for doc in no_doi_docs:
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
    corpus: CorpusPaths,
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
    from ..store.corpus import atomic_write_text

    atomic_write_text(bib_path, _entries_to_bibtex(entries))
    return bib_path


def write_corpus_bibliography(
    corpus: CorpusPaths,
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

    from ..store.corpus import atomic_write_text

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
# DOI content negotiation (for source document metadata enrichment)
# ---------------------------------------------------------------------------


def resolve_doi_metadata(
    doi: str, *, timeout: float = 10.0,
) -> dict[str, object]:
    """Fetch BibTeX metadata for a single DOI via doi.org content negotiation.

    Synchronous single-DOI helper. When resolving a batch (the refresh
    path in ``build_citation_index``), use ``resolve_doi_metadata_batch``
    which fans requests out concurrently via an async semaphore.
    """
    import httpx

    url = f"https://doi.org/{doi}"
    headers = {"Accept": "application/x-bibtex"}
    try:
        resp = httpx.get(
            url, headers=headers, timeout=timeout, follow_redirects=True,
        )
        if resp.status_code != 200:
            return {}
    except (httpx.HTTPError, Exception):
        return {}
    return _metadata_from_bibtex_entry(resp.text)


def resolve_doi_metadata_batch(
    dois: list[str],
    *,
    cache_path: Path | None = None,
    concurrency: int = 10,
    requests_per_second: float = 5.0,
    timeout: float = 10.0,
) -> dict[str, dict[str, object]]:
    """Resolve many DOIs concurrently via doi.org content negotiation.

    Uses the same limiter + semaphore decorator pattern as
    ``citestore.resolver.AsyncResolver`` (aiolimiter.AsyncLimiter for the
    polite-qps floor, asyncio.Semaphore for the hard-concurrency cap) so
    refresh-time DOI enrichment matches the idiom already established for
    OpenAlex. Results are persisted in the ``citestore`` DOI cache
    (``<corpus>/.citestore.db``) when ``cache_path`` is given — a second
    refresh on the same corpus skips the network entirely.

    Returns a dict keyed by input DOI (lowercased); missing / failed
    lookups are omitted. Runs a fresh event loop so callers stay sync.
    """
    if not dois:
        return {}
    import asyncio
    from functools import wraps

    import httpx
    from aiolimiter import AsyncLimiter

    from ..citestore.db import DOICache

    unique = list(dict.fromkeys(d.lower() for d in dois if d))
    if not unique:
        return {}

    results: dict[str, dict[str, object]] = {}
    to_fetch: list[str] = []

    # Cache lookup — pull already-resolved DOIs, regardless of source
    # (CrossRef / doi.org / OpenAlex all land in the same `works` table).
    if cache_path is not None:
        with DOICache(cache_path) as cache:
            hits = cache.get_many(unique)
        results.update(hits)
        to_fetch = [d for d in unique if d not in hits]
    else:
        to_fetch = unique

    if not to_fetch:
        return results

    async def _run() -> dict[str, dict[str, object]]:
        sem = asyncio.Semaphore(concurrency)
        limiter = AsyncLimiter(1, round(1 / requests_per_second, 3))
        fetched: dict[str, dict[str, object]] = {}
        headers = {"Accept": "application/x-bibtex"}

        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
        ) as client:
            @wraps(client.get)
            async def _get(url: str) -> httpx.Response:
                async with limiter, sem:
                    return await client.get(url, headers=headers)

            async def _one(doi: str) -> None:
                try:
                    resp = await _get(f"https://doi.org/{doi}")
                except httpx.HTTPError:
                    return
                if resp.status_code != 200:
                    return
                parsed = _metadata_from_bibtex_entry(resp.text)
                if parsed:
                    fetched[doi] = parsed

            await asyncio.gather(*(_one(d) for d in to_fetch))
        return fetched

    fetched = asyncio.run(_run())
    results.update(fetched)

    # Persist fresh results to the shared DOI cache.
    if cache_path is not None and fetched:
        with DOICache(cache_path) as cache:
            for doi, meta in fetched.items():
                try:
                    cache.put(doi, meta, source="doi.org")
                except Exception:  # noqa: BLE001
                    pass
    return results


# BibTeX allows ``month=jan`` style bare-word macros; bibtexparser does not
# define these by default, so CrossRef/ACM records containing them used to
# fail parsing entirely (observed on ACM responses for e.g. 10.1145/...).
# Strip the month field with a regex instead of teaching the parser every
# macro — we never consume ``month`` anyway and the stripped value contains
# the fields we actually need.
_BIBTEX_MONTH_RE = re.compile(
    r",\s*month\s*=\s*[a-z]+\s*(?=,|\})", re.IGNORECASE,
)

# inproceedings records set booktitle instead of journal. Our fallback
# pipeline expects journal/venue as the publication identity, so map it.
_BIBTEX_BOOKTITLE_VENUE = ("booktitle",)


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
    # Conference proceedings set booktitle; map to journal/venue.
    for key in _BIBTEX_BOOKTITLE_VENUE:
        if entry.get(key) and not result.get("journal"):
            result["journal"] = _as_text(entry[key])
            break
    if result.get("journal"):
        result["venue"] = _clean_venue(result["journal"])
    return result


# ---------------------------------------------------------------------------
# Metadata fallback (for library.bib enrichment)
# ---------------------------------------------------------------------------


def _with_fallback_metadata(
    corpus: CorpusPaths,
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
    if (
        not needs_publication
        and not needs_doi
        and not needs_authors
        and not needs_title
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
        lookup = doi_lookup or resolve_doi_metadata
        _merge_external_metadata(
            metadata,
            lookup(clean_doi),
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
    _DOI_AUTHORITATIVE = (
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
        if key in _DOI_AUTHORITATIVE:
            metadata[key] = value
            continue
        # For everything else (summary, year, etc.), keep local when present.
        if not metadata.get(key):
            metadata[key] = value


# Local values we treat as junk for DOI-authoritative fields. Matches the
# ISSN header lines, copyright tails, and markdown formatting noise we saw
# leaking from Marker-parsed mastheads into venue/journal fields.
_JUNK_VALUE_RE = re.compile(
    r"^\*\*|ISSN[:\s]|©\s*\d{4}|all\s+rights\s+reserved",
    re.IGNORECASE,
)


def _value_is_junk(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    if _JUNK_VALUE_RE.search(stripped):
        return True
    # Volume/pages fields that are just placeholders ("xxx", "n/a", "-").
    if stripped.lower() in {"xxx", "n/a", "na", "-", "—", "tbd", "in press"}:
        return True
    return False


def _read_doc_markdown(corpus: CorpusPaths, doc: Document) -> str:
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
