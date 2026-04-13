"""Build bibliography artifacts from wikify Documents.

Operates on the wikify Document model.
The source library is written to ``<corpus>/library.bib``. Parsed
reference lists are also projected into ``references.bib``,
``bibliography.bib``, and ``citation_index.json`` so downstream stages can
use structured citations without scraping PDF text.
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
from .citations import parse_reference
from .metadata import (
    extract_authors_from_markdown,
    extract_document_doi,
    extract_publication_fields,
    first_heading,
    parse_filename,
)

_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")
_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_VENUE_FRAGMENT_RE = re.compile(
    r"\b(?:IEEE|ACM|Proc|Proceedings|Trans|Journal|Phys|Rev|Mater|Nano|"
    r"Nature|Science|Circuits|Electron|Devices|Conference|Symp|Int\.?|Nat|"
    r"Commun|Adv|Sci|Rep|ACS|Appl|Lett|Nanoscale|Horiz|Angew|Chem|"
    r"Front|Neurosci)\b"
)
_CITATION_INDEX_VERSION = 1
_TITLE_DEDUPE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "the",
    "to",
    "toward",
    "towards",
    "use",
    "used",
    "using",
    "via",
    "with",
    "without",
}
_REFERENCE_SIGNATURE_MIN_TOKENS = 6
_REFERENCE_SIGNATURE_PREFIX_TOKENS = 10
_TITLE_KEY_MIN_TOKENS = 3


def _sanitize_id(s: str) -> str:
    s = _ID_SAFE_RE.sub("_", s).strip("_")
    return s or "unknown"


def _clean_doi(value: object) -> str:
    text = str(value or "").strip()
    match = re.search(r"(10\.\d{4,}/[^\s<>\]]+)", text)
    if match:
        doi = re.split(r"[?#&\]]", match.group(1), maxsplit=1)[0]
        return doi.rstrip(".,;)]}>")
    return text.rstrip(".,;)]}>")


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if re.search(r"\s+and\s+", value):
            return [v.strip() for v in re.split(r"\s+and\s+", value) if v.strip()]
        return [v.strip() for v in re.split(r"[,;]", value) if v.strip()]
    if not isinstance(value, (list, tuple, set)):
        text = _as_text(value)
        return [text] if text else []
    return [str(v).strip() for v in value if str(v).strip()]


def _first_text(metadata: dict, *keys: str) -> str:
    for key in keys:
        value = _as_text(metadata.get(key))
        if value:
            return value
    return ""


def _add_optional(entry: dict[str, str], field: str, value: object) -> None:
    text = _as_text(value)
    if text:
        entry[field] = text


def _clean_title(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    text = re.sub(r"[\ue000-\uf8ff]", "", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_venue(value: str) -> str:
    text = re.sub(r"(?i)^(?:cite\s+as|citation)\s*:\s*", "", value.strip())
    return re.sub(r"\s+", " ", text).strip(" -:;,")


def _document_entry(doc: Document) -> dict[str, str]:
    metadata = doc.metadata or {}
    authors_list = _as_list(metadata.get("authors"))
    title = _clean_title(_as_text(doc.title))

    year = metadata.get("year")
    year_str = str(year) if year else ""
    doi = _clean_doi(metadata.get("doi"))
    venue = _clean_venue(_first_text(metadata, "venue", "journal", "publicationTitle"))
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
    _add_optional(entry, "number", metadata.get("number") or metadata.get("issue"))
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


def _entries_to_bibtex(entries: list[dict[str, str]]) -> str:
    db = BibDatabase()
    db.entries = entries
    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False
    return bibtexparser.dumps(db, writer)


def paper_to_bibtex(doc: Document, citations: list[dict] | None = None) -> str:
    """Build a minimal ``@article`` BibTeX entry from a Document.

    The entry id is the sanitized ``doc.id``. ``citations`` is accepted
    for symmetry with the caller signature but is currently unused at
    the entry level (it is the corpus-side relation, not a per-entry
    field).
    """
    return _entries_to_bibtex([_document_entry(doc)])


def write_corpus_bibtex(
    corpus: CorpusPaths,
    docs: list[Document],
    *,
    resolve_doi: bool = False,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> Path:
    """Write ``<corpus>/library.bib`` containing one entry per Document."""
    corpus.ensure()
    bib_path = corpus.library_bib_path
    seen: dict[str, int] = {}
    entries: list[dict[str, str]] = []
    for doc in docs:
        enriched = _with_fallback_metadata(
            corpus,
            doc,
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
    """Write source, reference, union BibTeX, and JSON citation index.

    The function is intentionally corpus-wide: callers pass the active
    document set, and all citation artifacts are regenerated from that
    coherent snapshot. This keeps incremental ingest simple and correct.
    """
    corpus.ensure()
    source_entries, reference_entries, index = build_citation_index(
        corpus,
        docs,
        resolve_doi=resolve_doi,
        doi_lookup=doi_lookup,
    )

    from ..store.corpus import atomic_write_text

    atomic_write_text(corpus.library_bib_path, _entries_to_bibtex(source_entries))
    atomic_write_text(corpus.references_bib_path, _entries_to_bibtex(reference_entries))
    atomic_write_text(
        corpus.bibliography_bib_path,
        _entries_to_bibtex([*source_entries, *reference_entries]),
    )
    atomic_write_text(
        corpus.citation_index_path,
        json.dumps(index, indent=2, sort_keys=True) + "\n",
    )
    return {
        "library": corpus.library_bib_path,
        "references": corpus.references_bib_path,
        "bibliography": corpus.bibliography_bib_path,
        "citation_index": corpus.citation_index_path,
    }


def build_citation_index(
    corpus: CorpusPaths,
    docs: list[Document],
    *,
    resolve_doi: bool = False,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    """Build BibTeX entries plus the structured citation index payload."""
    source_entries: list[dict[str, str]] = []
    reference_entries: list[dict[str, str]] = []
    entries: dict[str, dict[str, object]] = {}
    reference_by_bibkey: dict[str, dict[str, str]] = {}
    doc_bibkeys: dict[str, str] = {}
    doc_citations: dict[str, list[str]] = {}
    doi_bibkeys: dict[str, str] = {}
    title_bibkeys: dict[str, str] = {}
    signature_bibkeys: dict[str, str] = {}
    source_seen: dict[str, int] = {}
    ref_seen: dict[str, int] = {}

    enriched_docs = [
        _with_fallback_metadata(
            corpus,
            doc,
            resolve_doi=resolve_doi,
            doi_lookup=doi_lookup,
        )
        for doc in docs
    ]

    for doc in enriched_docs:
        entry = _document_entry(doc)
        entry["ID"] = _unique_bibkey(entry["ID"], source_seen)
        source_entries.append(entry)
        doc_bibkeys[doc.id] = entry["ID"]
        record = _index_record_from_entry(entry, kind="source", doc_id=doc.id)
        entries[entry["ID"]] = record
        _remember_bibkey(
            entry,
            entry["ID"],
            doi_bibkeys=doi_bibkeys,
            title_bibkeys=title_bibkeys,
            signature_bibkeys=signature_bibkeys,
        )

    for doc in enriched_docs:
        cited_keys: list[str] = []
        for citation in doc.citations:
            bibkey = _resolve_citation_bibkey(
                citation,
                doi_bibkeys=doi_bibkeys,
                title_bibkeys=title_bibkeys,
                signature_bibkeys=signature_bibkeys,
            )
            if bibkey and bibkey in reference_by_bibkey:
                entry = _reference_entry(
                    citation,
                    resolve_doi=resolve_doi,
                    doi_lookup=doi_lookup,
                )
                if _is_exportable_reference_entry(entry, citation):
                    _merge_reference_entry(reference_by_bibkey[bibkey], entry)
                    _refresh_index_record(entries[bibkey], reference_by_bibkey[bibkey])
                    _remember_bibkey(
                        reference_by_bibkey[bibkey],
                        bibkey,
                        doi_bibkeys=doi_bibkeys,
                        title_bibkeys=title_bibkeys,
                        signature_bibkeys=signature_bibkeys,
                    )
            if not bibkey:
                entry = _reference_entry(
                    citation,
                    resolve_doi=resolve_doi,
                    doi_lookup=doi_lookup,
                )
                if not _is_exportable_reference_entry(entry, citation):
                    continue
                bibkey = _resolve_entry_bibkey(
                    entry,
                    doi_bibkeys=doi_bibkeys,
                    title_bibkeys=title_bibkeys,
                    signature_bibkeys=signature_bibkeys,
                )
                if bibkey:
                    if bibkey in reference_by_bibkey:
                        _merge_reference_entry(reference_by_bibkey[bibkey], entry)
                        _refresh_index_record(entries[bibkey], reference_by_bibkey[bibkey])
                    _remember_bibkey(
                        reference_by_bibkey.get(bibkey, entry),
                        bibkey,
                        doi_bibkeys=doi_bibkeys,
                        title_bibkeys=title_bibkeys,
                        signature_bibkeys=signature_bibkeys,
                    )
                if not bibkey:
                    entry["ID"] = _unique_bibkey(_reference_key(entry, citation), ref_seen)
                    bibkey = entry["ID"]
                    reference_entries.append(entry)
                    reference_by_bibkey[bibkey] = entry
                    entries[bibkey] = _index_record_from_entry(entry, kind="reference")
                    _remember_bibkey(
                        entry,
                        bibkey,
                        doi_bibkeys=doi_bibkeys,
                        title_bibkeys=title_bibkeys,
                        signature_bibkeys=signature_bibkeys,
                    )

            if bibkey not in cited_keys:
                cited_keys.append(bibkey)
            record = entries.get(bibkey)
            if record is not None:
                source_doc_ids = set(record.get("source_doc_ids", []))
                source_doc_ids.add(doc.id)
                record["source_doc_ids"] = sorted(source_doc_ids)
                ord_value = citation.get("ord")
                ords = list(record.get("citation_ords", []))
                marker = {"doc_id": doc.id, "ord": ord_value}
                if marker not in ords:
                    ords.append(marker)
                record["citation_ords"] = ords
                if citation.get("raw_text") and not record.get("raw_text"):
                    record["raw_text"] = _as_text(citation.get("raw_text"))
        doc_citations[doc.id] = cited_keys

    return source_entries, reference_entries, {
        "schema_version": _CITATION_INDEX_VERSION,
        "entries": entries,
        "doc_bibkeys": doc_bibkeys,
        "doc_citations": doc_citations,
        "doi_bibkeys": doi_bibkeys,
        "title_bibkeys": title_bibkeys,
        "signature_bibkeys": signature_bibkeys,
    }


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


def _resolve_citation_bibkey(
    citation: dict,
    *,
    doi_bibkeys: dict[str, str],
    title_bibkeys: dict[str, str],
    signature_bibkeys: dict[str, str],
) -> str:
    doi = _clean_doi(citation.get("doi"))
    if doi and doi.casefold() in doi_bibkeys:
        return doi_bibkeys[doi.casefold()]
    title_key = _dedupe_title_key(citation.get("title"))
    if title_key and title_key in title_bibkeys:
        return title_bibkeys[title_key]
    signature = _reference_signature(citation)
    if signature and signature in signature_bibkeys:
        return signature_bibkeys[signature]
    return ""


def _resolve_entry_bibkey(
    entry: dict[str, str],
    *,
    doi_bibkeys: dict[str, str],
    title_bibkeys: dict[str, str],
    signature_bibkeys: dict[str, str],
) -> str:
    doi = _clean_doi(entry.get("doi"))
    if doi and doi.casefold() in doi_bibkeys:
        return doi_bibkeys[doi.casefold()]
    title_key = _dedupe_title_key(entry.get("title"))
    if title_key and title_key in title_bibkeys:
        return title_bibkeys[title_key]
    signature = _reference_signature(entry)
    if signature and signature in signature_bibkeys:
        return signature_bibkeys[signature]
    return ""


def _remember_bibkey(
    entry: dict[str, str],
    bibkey: str,
    *,
    doi_bibkeys: dict[str, str],
    title_bibkeys: dict[str, str],
    signature_bibkeys: dict[str, str],
) -> None:
    doi = _clean_doi(entry.get("doi"))
    if doi:
        doi_bibkeys[doi.casefold()] = bibkey
    title_key = _dedupe_title_key(entry.get("title"))
    if title_key:
        title_bibkeys[title_key] = bibkey
    signature = _reference_signature(entry)
    if signature:
        signature_bibkeys[signature] = bibkey


def _merge_reference_entry(target: dict[str, str], candidate: dict[str, str]) -> None:
    for field in (
        "title",
        "author",
        "year",
        "journal",
        "doi",
        "url",
        "volume",
        "number",
        "pages",
        "publisher",
    ):
        if candidate.get(field) and not target.get(field):
            target[field] = candidate[field]


def _refresh_index_record(record: dict[str, object], entry: dict[str, str]) -> None:
    record["title"] = _as_text(entry.get("title"))
    record["authors"] = _as_list(entry.get("author"))
    record["year"] = _as_text(entry.get("year"))
    record["venue"] = _as_text(entry.get("journal"))
    record["doi"] = _clean_doi(entry.get("doi"))
    record["url"] = _as_text(entry.get("url"))
    for key in ("volume", "number", "pages", "publisher", "issn"):
        if entry.get(key):
            record[key] = entry[key]


def _reference_signature(entry_or_citation: dict) -> str:
    year = _as_text(entry_or_citation.get("year"))
    if not year:
        return ""
    title = _as_text(entry_or_citation.get("title"))
    tokens = _significant_title_tokens(title)
    if len(tokens) < _REFERENCE_SIGNATURE_MIN_TOKENS:
        return ""
    prefix = " ".join(tokens[:_REFERENCE_SIGNATURE_PREFIX_TOKENS])
    return f"{year}|{prefix}"


def _dedupe_title_key(value: object) -> str:
    if len(_significant_title_tokens(value)) < _TITLE_KEY_MIN_TOKENS:
        return ""
    return _normalise_title_key(value)


def _significant_title_tokens(value: object) -> list[str]:
    return [
        token
        for token in _TITLE_TOKEN_RE.findall(_as_text(value).casefold())
        if len(token) > 2 and token not in _TITLE_DEDUPE_STOPWORDS
    ]


def _reference_entry(
    citation: dict,
    *,
    resolve_doi: bool,
    doi_lookup: Callable[[str], dict[str, object]] | None,
) -> dict[str, str]:
    doi = _clean_doi(citation.get("doi"))
    external: dict[str, object] = {}
    if resolve_doi and doi:
        lookup = doi_lookup or resolve_doi_metadata
        external = lookup(doi)

    authors = _as_list(external.get("authors")) or _as_list(citation.get("authors"))
    title = _clean_title(_first_text(external, "title") or _as_text(citation.get("title")))
    year = _first_text(external, "year") or _as_text(citation.get("year"))
    venue = _clean_venue(
        _first_text(external, "venue", "journal") or _as_text(citation.get("venue"))
    )
    doi = _clean_doi(external.get("doi") or doi)
    authors, title, year, venue, doi = _repair_reference_fields_from_raw(
        citation,
        authors=authors,
        title=title,
        year=year,
        venue=venue,
        doi=doi,
    )
    if _is_identifier_title(title, doi):
        title = ""
    if _reference_title_is_unusable(title):
        title = ""
    url = _first_text(external, "url")
    if not url and doi:
        url = f"https://doi.org/{doi}"

    entry: dict[str, str] = {
        "ENTRYTYPE": "article",
        "ID": _reference_key(
            {"title": title, "author": " and ".join(authors), "year": year, "doi": doi},
            citation,
        ),
    }
    if title:
        entry["title"] = title
    if authors:
        entry["author"] = " and ".join(authors)
    if year:
        entry["year"] = year
    if venue:
        entry["journal"] = venue
    if doi:
        entry["doi"] = doi
    if url:
        entry["url"] = url
    _add_optional(entry, "volume", external.get("volume"))
    _add_optional(entry, "number", external.get("number") or external.get("issue"))
    _add_optional(entry, "pages", external.get("pages"))
    _add_optional(entry, "publisher", external.get("publisher"))
    _add_optional(entry, "note", citation.get("raw_text"))
    return entry


def _repair_reference_fields_from_raw(
    citation: dict,
    *,
    authors: list[str],
    title: str,
    year: str,
    venue: str,
    doi: str,
) -> tuple[list[str], str, str, str, str]:
    raw = _as_text(citation.get("raw_text"))
    if not raw:
        return authors, title, year, venue, doi
    raw_authors, raw_year, raw_title, raw_venue, raw_doi = parse_reference(raw)
    raw_title = _clean_title(raw_title)
    raw_venue = _clean_venue(raw_venue)
    title_was_unusable = _reference_title_is_unusable(title)
    if raw_title and (title_was_unusable or len(raw_title) > len(title) + 20):
        title = raw_title
    if raw_authors and (not authors or title_was_unusable or _authors_look_polluted(authors)):
        authors = raw_authors
    if raw_year and not year:
        year = str(raw_year)
    if raw_venue and (not venue or venue == title):
        venue = raw_venue
    if raw_doi and not doi:
        doi = _clean_doi(raw_doi)
    return authors, title, year, venue, doi


def _is_identifier_title(title: str, doi: str) -> bool:
    clean = _as_text(title).casefold()
    if not clean:
        return False
    if clean.startswith(("doi:", "http://", "https://")):
        return True
    return bool(doi and _normalise_title_key(clean) == _normalise_title_key(doi))


def _is_exportable_reference_entry(entry: dict[str, str], citation: dict) -> bool:
    """Return whether a parsed cited work is structured enough for BibTeX.

    Reference extraction is best-effort and parser output sometimes splits
    bibliography lines into partial phrases, acknowledgements, or author-only
    fragments. Those are useful as raw text in docs, but they should not become
    fake BibTeX records. Keep entries with a DOI, or entries that have the
    minimum scholarly shape: title + year + (author or venue).
    """
    if _clean_doi(entry.get("doi")):
        return True
    title = _as_text(entry.get("title"))
    year = _as_text(entry.get("year"))
    authors = _as_list(entry.get("author"))
    venue = _as_text(entry.get("journal"))
    if not title or not year or not (authors or venue):
        return False
    if _reference_title_is_unusable(title):
        return False
    if _title_looks_like_raw_reference(title, citation):
        return False
    return True


def _reference_title_is_unusable(title: str) -> bool:
    text = _as_text(title)
    if not text:
        return True
    if not re.search(r"[A-Za-z]", text):
        return True
    if re.match(r"^\d", text) and (
        len(_significant_title_tokens(text)) < 4
        or re.search(r"(?:https?://|doi\.org|\bdoi\b)", text)
    ):
        return True
    if len(_significant_title_tokens(text)) < 2:
        return True
    if _VENUE_FRAGMENT_RE.search(text) and len(_significant_title_tokens(text)) < 5:
        return True
    return bool(re.fullmatch(r"[\d\s,().:;\-]+", text))


def _authors_look_polluted(authors: list[str]) -> bool:
    joined = " ".join(authors)
    if re.search(r"\b(?:IEEE|Phys|Mater|Nano|Journal|Proceedings|Trans)\b", joined):
        return True
    return any(len(author.split()) > 5 for author in authors)


def _title_looks_like_raw_reference(title: str, citation: dict) -> bool:
    title_key = _normalise_title_key(title)
    raw_key = _normalise_title_key(citation.get("raw_text"))
    if raw_key and title_key == raw_key:
        return True
    # Titles beginning with a dense author list usually mean parsing failed
    # and the whole raw reference was copied into the title field.
    if re.match(r"^(?:[A-Z]\.?\s*){1,4}[A-Z][A-Za-z'’.-]+[,;]\s+", title):
        return True
    if re.match(r"^[A-Z][A-Za-z'’.-]+,\s*(?:[A-Z]\.\s*){1,4}", title):
        return True
    if re.search(r"\bet\s*al\.?,?\s*$", title, flags=re.IGNORECASE):
        return True
    if len(title.split()) < 2:
        return True
    return False


def _reference_key(entry: dict[str, str], citation: dict) -> str:
    doi = _clean_doi(entry.get("doi"))
    if doi:
        return "ref_" + hashlib.sha1(doi.casefold().encode("utf-8")).hexdigest()[:12]
    title = _as_text(entry.get("title"))
    authors = _as_text(entry.get("author"))
    year = _as_text(entry.get("year"))
    first_author = authors.split(" and ", 1)[0] if authors else ""
    base = " ".join(part for part in (year, first_author, title[:80]) if part)
    if base:
        return "ref_" + _sanitize_id(base)
    raw = _as_text(citation.get("raw_text")) or json.dumps(citation, sort_keys=True)
    return "ref_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _index_record_from_entry(
    entry: dict[str, str],
    *,
    kind: str,
    doc_id: str = "",
) -> dict[str, object]:
    record: dict[str, object] = {
        "bibkey": entry["ID"],
        "kind": kind,
        "title": _as_text(entry.get("title")),
        "authors": _as_list(entry.get("author")),
        "year": _as_text(entry.get("year")),
        "venue": _as_text(entry.get("journal")),
        "doi": _clean_doi(entry.get("doi")),
        "url": _as_text(entry.get("url")),
        "source_doc_ids": [],
        "citation_ords": [],
    }
    if doc_id:
        record["doc_id"] = doc_id
    for key in ("volume", "number", "pages", "publisher", "issn"):
        if entry.get(key):
            record[key] = entry[key]
    if entry.get("note"):
        record["raw_text"] = entry["note"]
    return record


def resolve_doi_metadata(doi: str, *, timeout: float = 10.0) -> dict[str, object]:
    """Fetch BibTeX metadata for a DOI using DOI content negotiation.

    This is intentionally generic: DOI registration agencies decide the
    returned journal, author, volume, and page metadata. Callers opt in so
    normal ingest remains deterministic and offline-friendly.
    """
    clean = _clean_doi(doi)
    if not clean:
        return {}

    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    request = Request(
        f"https://doi.org/{quote(clean, safe='/')}",
        headers={
            "Accept": "application/x-bibtex",
            "User-Agent": "wikify metadata resolver",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError):
        return {}

    parsed = bibtexparser.loads(payload)
    if not parsed.entries:
        return {}
    return _metadata_from_bibtex_entry(parsed.entries[0])


def _metadata_from_bibtex_entry(entry: dict[str, str]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for src, dst in (
        ("doi", "doi"),
        ("journal", "venue"),
        ("booktitle", "venue"),
        ("volume", "volume"),
        ("number", "issue"),
        ("pages", "pages"),
        ("publisher", "publisher"),
        ("issn", "issn"),
        ("url", "url"),
        ("year", "year"),
    ):
        value = _as_text(entry.get(src))
        if value:
            metadata[dst] = value

    title = _as_text(entry.get("title"))
    if title:
        metadata["title"] = title
    authors = _as_text(entry.get("author"))
    if authors:
        metadata["authors"] = [a.strip() for a in re.split(r"\s+and\s+", authors) if a.strip()]
    return metadata


def _with_fallback_metadata(
    corpus: CorpusPaths,
    doc: Document,
    *,
    resolve_doi: bool,
    doi_lookup: Callable[[str], dict[str, object]] | None,
) -> Document:
    """Fill missing bibliographic fields from markdown and optional DOI metadata.

    This lets derived artifact rebuilds improve ``library.bib`` for unchanged
    docs without forcing an expensive PDF reparse.
    """
    original_metadata = dict(doc.metadata or {})
    metadata = dict(original_metadata)
    source_path = Path(doc.source_path) if doc.source_path else Path()
    _, fn_author, _ = parse_filename(source_path.name)

    if metadata.get("doi"):
        metadata["doi"] = _clean_doi(metadata.get("doi"))
    for venue_key in ("venue", "journal", "publicationTitle"):
        if metadata.get(venue_key):
            metadata[venue_key] = _clean_venue(_as_text(metadata.get(venue_key)))

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
        metadata_title = _as_text(metadata.get("title"))
        heading = (
            metadata_title
            if metadata_title and not _title_needs_fallback(metadata_title)
            else ""
        )
        if not heading:
            heading = _best_markdown_title(text, title) or first_heading(text) or ""
        if heading and not _title_needs_fallback(heading):
            title = _clean_title(heading)
    if text and needs_authors:
        authors = extract_authors_from_markdown(text, fn_author=fn_author)
        if len(authors) >= 2 or authors:
            metadata["authors"] = authors
    if text and needs_publication:
        for key, value in extract_publication_fields(text).items():
            if not metadata.get(key):
                metadata[key] = value
    if text and needs_doi:
        doi = extract_document_doi(text)
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

    if metadata == original_metadata and title == doc.title:
        return doc
    return replace(doc, title=title, metadata=metadata)


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
    return False


def _title_needs_fallback(title: str) -> bool:
    clean = title.strip()
    return bool(re.match(r"^\[\d{4}\s+[^\]]+\]", clean))


def _best_markdown_title(md_text: str, fallback_title: str) -> str:
    """Pick the heading that best matches the filename-derived title."""
    target_tokens = set(_normalise_title_key(_strip_filename_title_prefix(fallback_title)).split())
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
        heading = _clean_title(re.sub(r"[\ue000-\uf8ff]", "", match.group(1)))
        if heading:
            headings.append(heading)
    return headings


def _strip_filename_title_prefix(title: str) -> str:
    match = re.match(r"^\[\d{4}\s+[^\]]+\]\s*[-–—]?\s*(.+)$", title.strip())
    return match.group(1) if match else title


def _heading_is_generic(heading: str) -> bool:
    lower = heading.casefold().strip()
    generic = {
        "article",
        "articles",
        "letters",
        "paper",
        "review",
        "open access",
        "research article",
        "articles you may be interested in",
        "references",
        "bibliography",
        "affiliations",
        "abstract",
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
    for key, value in external.items():
        if not value:
            continue
        if key == "authors" and prefer_authors:
            metadata[key] = value
        elif key == "title" and not metadata.get("title"):
            metadata[key] = value
        elif not metadata.get(key):
            metadata[key] = value
        else:
            metadata.setdefault(key, value)


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
