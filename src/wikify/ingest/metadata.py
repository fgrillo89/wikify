"""Metadata extraction helpers for parsers (pdf/docx/pptx/html).

Stdlib imports only, no dataclasses returned to the outside. Helpers
cover title, authors, summary, year, DOI, venue, and a slide-aware
summary synthesiser.
"""

import re
from dataclasses import dataclass

# Footnote/affiliation glyphs that publishers attach to author names.
# Includes Greek archaic koppa (U+0377, used as a footnote marker by
# some Wiley journals), the asterisk operator (U+2217, not the ASCII
# *), Private Use Area glyphs (U+E000-U+F8FF, custom journal fonts),
# explicitly invalid codepoints (U+0378-U+0379), and the standard
# dagger/section sign cluster.
_AUTHOR_GLYPH_NOISE_RE = re.compile(
    "["
    "§"            # SECTION SIGN
    "*"                  # ASCII ASTERISK
    "ː-˿"    # spacing modifier SYMBOLS only (tilde, breve, length
                          # marks). EXCLUDES U+02B0-U+02CF which contains
                          # transliteration apostrophes (Hawaiian okina
                          # U+02BB, modifier prime U+02B9) and other modifier
                          # letters that survive in real names.
    "ͷ-͹"    # Greek archaic koppa + invalid codepoints
    "†-‡"    # DAGGER, DOUBLE DAGGER
    "⁎"            # LOW ASTERISK
    "∗"            # ASTERISK OPERATOR
    "✱"            # HEAVY ASTERISK
    "✉"            # ENVELOPE
    "-"    # Private Use Area
    "]"
)


def _strip_inline_markup(name: str) -> str:
    """Drop leftover ``<sup>…</sup>``/``<sub>…</sub>`` tags and tidy whitespace.

    Parsers occasionally leak affiliation markup like ``<sup>c</sup>`` into
    author strings when the sup-ref bracketiser (which targets numeric
    citation markers) leaves non-numeric affiliation markers untouched.
    Also strips footnote glyphs (koppa, asterisk-operator, PUA chars)
    and title-cases all-caps names ("DEBASHIS PANDA" -> "Debashis
    Panda") from older IEEE templates that print bylines in upper
    case.
    """
    name = re.sub(r"<sup>[^<]*</sup>", "", name, flags=re.IGNORECASE)
    name = re.sub(r"<sub>[^<]*</sub>", "", name, flags=re.IGNORECASE)
    name = _AUTHOR_GLYPH_NOISE_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" ,.;")
    # Title-case names that arrived in all caps. Guard on a small token
    # count so romanized Asian names like "WANG TIANYU" (rare but
    # legitimate in some byline conventions) get cased, while
    # acronymic strings of arbitrary length are not coerced into
    # implausible names.
    if name and name == name.upper() and 1 <= len(name.split()) <= 4:
        parts = []
        for token in name.split():
            parts.append(
                "-".join(p.capitalize() for p in token.split("-"))
            )
        name = " ".join(parts)
    return name

# --- public surface ------------------------------------------------------


def first_heading(md_text: str) -> str | None:
    in_frontmatter = False
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        match = re.match(r"^#{1,6}\s+(?P<title>.+)$", stripped)
        if match:
            heading = match.group("title").strip()
            heading = clean_markdown(heading)
            heading = re.sub(r"[\ue000-\uf8ff]", "", heading)
            heading = re.sub(r"\s+", " ", heading).strip()
            if heading and not _is_heading_noise(heading):
                return heading
    return None


def parse_filename(filename: str) -> tuple[int | None, str | None, str | None]:
    """Parse a [YYYY Author] Title.ext filename. Returns (year, author, title)."""
    m = re.match(r"\[(\d{4})\s+([^\]]+)\]\s*(.+?)\.(?:pdf|docx|pptx)$", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), m.group(2).strip(), m.group(3).strip()
    m = re.match(r"\[(\d{4})\]\s*(.+?)\.(?:pdf|docx|pptx)$", filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), None, m.group(2).strip()
    return None, None, None


# --- junk-title rejection -----------------------------------------------
#
# Known placeholder strings emitted by Word / PDF producers when no real
# title was ever set on the document. These leak in via the docx parser
# (``core_properties.title``) and, on Word-exported PDFs, via the PDF's
# embedded ``/Title`` metadata field. Stored as casefolded strings for
# O(1) lookup.

_JUNK_TITLE_LITERALS = frozenset({
    "word document",
    "untitled",
    "untitled.docx",
    "document1",
    "document",
    "document 1",
    "new document",
    "new microsoft word document",
    "title",
})


_MS_WORD_JUNK_RE = re.compile(r"^\s*microsoft\s+word\s*[-–—:]?\s*", re.IGNORECASE)

# Common section-header names that some parsers lift as the document title
# when the real title is in a different layout band.
_SECTION_HEADER_LITERALS = frozenset({
    "abstract", "summary", "introduction", "background",
    "methods", "method", "materials and methods", "experimental",
    "experimental section", "experimental methods", "experimental details",
    "results", "results and discussion", "discussion",
    "conclusion", "conclusions", "conclusions and outlook",
    "references", "bibliography", "acknowledgments", "acknowledgements",
    "appendix", "supporting information", "supplementary information",
    # Front-matter section labels Marker sometimes lifts as the title:
    "conflict of interest", "competing interests", "funding", "ethics",
    "data availability", "author contributions", "author contribution",
})

# Numbered section headers: "1 Introduction", "2. Methods", "III. Results".
_NUMBERED_SECTION_RE = re.compile(
    r"^\s*(?:\d+|[ivxIVX]+)[.\s)]+\s*\w+", re.IGNORECASE,
)

# Repository / institution page banners: "University of ... [STARS]",
# "Stanford University Libraries", "MIT Open Access".
_REPOSITORY_BANNER_RE = re.compile(
    r"^\s*(?:University|College|Institute|School|Department)\s+of\s+",
    re.IGNORECASE,
)

# Markdown link fragments that sometimes leak into a heading-derived title
# when Marker's first-heading extractor picks up a banner or TOC line.
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(")


def is_junk_title(title: str, *, venue_hints: tuple[str, ...] = ()) -> bool:
    """Return True when ``title`` is a known placeholder or a venue name.

    Catches Word's default ``"Word Document"`` / ``"Untitled"`` placeholders,
    ``"Microsoft Word - foo.docx"`` save-as artifacts, numbered section
    headers (``"1 Introduction"``, ``"III. Methods"``), standalone section
    names (``"Abstract"``, ``"Conclusions"``), university/repository banners,
    and markdown-link fragments. When a venue name slipped into the title
    slot and the same string appears in ``venue_hints``, it is treated as
    junk too.
    """
    if title is None:
        return True
    collapsed = re.sub(r"\s+", " ", title).strip()
    if not collapsed:
        return True
    folded = collapsed.casefold()
    if folded in _JUNK_TITLE_LITERALS:
        return True
    if folded in _SECTION_HEADER_LITERALS:
        return True
    if _MS_WORD_JUNK_RE.match(collapsed):
        return True
    # Numbered section headers only when they end after the first word or
    # two; "1 Introduction" fine to reject, but "1 Introduction to ALD" is
    # a legitimate book-chapter title.
    if _NUMBERED_SECTION_RE.match(collapsed) and len(collapsed.split()) <= 3:
        return True
    if _REPOSITORY_BANNER_RE.match(collapsed):
        return True
    if _MARKDOWN_LINK_RE.search(collapsed):
        return True
    if is_garbled_title(collapsed):
        return True
    for hint in venue_hints:
        hint_folded = re.sub(r"\s+", " ", (hint or "")).strip().casefold()
        if hint_folded and hint_folded == folded:
            return True
    return False


_FILENAME_HASH_SUFFIX_RE = re.compile(r"_[0-9a-f]{6,}$", re.IGNORECASE)


def choose_document_title(
    md_text: str,
    path,
    *,
    venue_hints: tuple[str, ...] = (),
    xmp_title: str = "",
    info_title: str = "",
    extra_title: str = "",
    bib_title: str = "",
) -> str:
    """Pick the document title from available signals, preferring authoritative
    sources over heuristics.

    Priority:
      1. ``fn_title`` from ``[YYYY Author] Real Title.ext`` filename convention.
         User-curated, directly authoritative. Use when >=20 chars and not
         flagged by ``is_junk_title``.
      1b. ``bib_title`` — title written to ``corpus_papers.bib`` by a
         prior refresh. Reflects DOI-resolved + bib-cleaned truth from
         the previous pipeline run; consulted right after the filename
         so a clean prior-run title can heal a stuck-state ``doc.title``
         that survives the heuristic junk filters.
      2. ``xmp_title`` — publisher-injected XMP ``dc:title`` when present.
         Clean on modern PDFs; garbled manuscript IDs ("acs_nn_nn-...") and
         "untitled" placeholders are caught by ``is_junk_title``.
      3. ``extra_title`` — parser-specific candidate (e.g. Docling's
         ``doc.name``). Optional.
      4. ``info_title`` — the older ``/Info`` dict title. Often empty or a
         Word save-as artifact.
      5. ``first_heading(md_text)`` when ≥20 chars and not junk. Guards
         against section-label headings ("Conflict of Interest", "1
         Introduction", "Abstract").
      6. ``clean_filename_title(path.name)`` — filename stem tidied.
      7. ``path.stem`` — last resort.

    Filename > all extracted signals > heading > stem remains the structural
    rule. XMP/Info are inserted below the authoritative filename and above
    the often-noisy heading-based extraction.
    """
    fn_year, fn_author, fn_title = parse_filename(getattr(path, "name", str(path)))
    fn_clean = clean_filename_title(getattr(path, "name", str(path)))

    # In the `[YYYY Author] Title.ext` filename convention, underscores
    # are word separators (filename-friendly substitute for spaces). Map
    # them back before passing through clean_markdown — otherwise
    # `Memristor-The_missing_circuit_element` gets eaten by the markdown-
    # italic regex `_(.+?)_` and collapses to
    # `Memristor-Themissingcircuit_element`.
    if fn_title:
        fn_title = fn_title.replace("_", " ")

    # (candidate_text, min_length_for_acceptance). We still accept a
    # shorter-but-non-junk candidate in pass 2 if nothing longer passes.
    candidates: list[tuple[str, int]] = [
        (clean_markdown(fn_title or ""), 20),
        (clean_markdown(bib_title or ""), 20),
        (clean_markdown(xmp_title or ""), 20),
        (clean_markdown(extra_title or ""), 20),
        (clean_markdown(info_title or ""), 20),
        (clean_markdown(first_heading(md_text) or ""), 20),
        (fn_clean, 0),
        (getattr(path, "stem", str(path)), 0),
    ]

    # Pass 1: accept long, non-junk candidates in priority order.
    for cand, min_len in candidates:
        if cand and len(cand) >= min_len and not is_junk_title(
            cand, venue_hints=venue_hints
        ):
            return cand

    # Pass 2: accept any non-junk candidate, even short ones.
    for cand, _ in candidates:
        if cand and not is_junk_title(cand, venue_hints=venue_hints):
            return cand

    # Everything is junk — return the cleaned filename as the least-bad option.
    return fn_clean or getattr(path, "stem", "")


def assemble_pdf_metadata(
    path,
    md_text: str,
    *,
    fitz_doc=None,
    extra_title_candidate: str = "",
    resolved: dict | None = None,
    doi_hint: str = "",
) -> dict:
    """Fuse all available metadata sources for a PDF-backed parse.

    One function, one priority decision per field. Parsers call this
    instead of re-implementing the chains. Sources fused:

    - Filename (``[YYYY Author] Title.ext`` convention, user-curated)
    - XMP packet (``dc:title``, ``dc:creator``, ``prism:doi``, ...)
    - ``/Info`` dict (the older PDF metadata block, often sparse)
    - Markdown body (DOI, author lines, venue / volume / pages regex,
      summary)
    - Parser-specific signal via ``extra_title_candidate`` (e.g.
      Docling's ``doc.name``)

    Priority chains (highest wins, all gated by junk/length filters):

    - title:    filename → XMP → extra → /Info → first_heading → stem
                (see ``choose_document_title``)
    - authors:  markdown≥2 → XMP≥2 → /Info≥2 → any → [fn_author]
                (all validated against filename surname)
    - year:     filename → XMP pub date → /Info creation date
    - doi:      markdown body → raw-PDF fallback scan → XMP
    - venue/volume/pages: markdown regex → XMP gap-fill
    - keywords: XMP ``dc:subject`` (single source)

    ``fitz_doc`` can be passed when the caller already has it open; the
    caller keeps ownership. When ``None``, a short-lived fitz.open is made
    here and closed before return.

    ``resolved`` is an optional CrossRef / doi.org record (fetched
    upstream via ``util.doi_resolver.resolve_many``) for a DOI we already
    found in XMP. When truthy the expensive raw-PDF DOI fallback scan
    (``extract_pdf_doi_fallback``, which re-opens the PDF and pulls its
    cover/last pages) is skipped — we already know the DOI resolves, so
    re-scanning the PDF is wasted work. The DOI-authoritative merge of
    title / journal / volume / pages into the final metadata still
    happens later in ``bibtex._merge_external_metadata`` at bibliography
    build time; we don't duplicate that here.

    ``doi_hint`` is a DOI string the caller already discovered in an
    earlier pass (e.g. the ingest DAG's pass-1 XMP / raw-PDF scan). When
    set, the fallback scan is suppressed even if ``resolved`` is ``None``
    (resolution may simply have missed), and the hint is used as the DOI
    value when the markdown body doesn't print one. Net effect: on a
    full ingest each PDF's cover pages are scanned once (pass 1) instead
    of twice (pass 1 + pass 4).
    """
    from .xmp import read_xmp

    info: dict = {}
    xmp: dict = {}
    opened = False
    if fitz_doc is None:
        try:
            import fitz  # pymupdf

            fitz_doc = fitz.open(str(path))
            opened = True
        except Exception:  # noqa: BLE001 - missing/broken file
            fitz_doc = None
    if fitz_doc is not None:
        try:
            info = fitz_doc.metadata or {}
            xmp = read_xmp(fitz_doc)
        finally:
            if opened:
                fitz_doc.close()

    fn_year, fn_author, _ = parse_filename(getattr(path, "name", str(path)))

    # Markdown-derived signals.
    md_authors = extract_authors_from_markdown(md_text, fn_author=fn_author)
    md_authors = validate_authors_against_filename(md_authors, fn_author)
    publication = extract_publication_fields(md_text)
    for field in ("venue", "volume", "pages"):
        if not publication.get(field) and xmp.get(field):
            publication[field] = xmp[field]

    venue_hints = tuple(
        v for v in (publication.get("venue"), publication.get("journal")) if v
    )
    title = choose_document_title(
        md_text,
        path,
        venue_hints=venue_hints,
        xmp_title=xmp.get("title") or "",
        info_title=(info.get("title") or "").strip(),
        extra_title=extra_title_candidate,
    )

    # Authors: XMP and /Info lists also get the filename-surname guard so a
    # publisher-supplied list from the wrong paper can't slip through.
    # XMP's dc:creator is supposed to be one rdf:li per author, but some
    # publishers (notably IOP, Elsevier) stuff the entire author list
    # into a single rdf:li separated by commas/semicolons. Run each XMP
    # element through ``parse_authors`` whenever it contains a separator
    # so the byline gets split correctly. ``parse_authors`` is a no-op on
    # a single name without separators.
    xmp_authors_flat = _flatten_xmp_authors(list(xmp.get("authors") or []))
    xmp_authors = validate_authors_against_filename(
        xmp_authors_flat, fn_author
    )
    info_raw = (info.get("author") or "").strip()
    info_authors = validate_authors_against_filename(
        parse_authors(info_raw) if info_raw else [], fn_author
    )
    authors: list[str] = []
    for group in (md_authors, xmp_authors, info_authors):
        if len(group) >= 2:
            authors = group
            break
    if not authors:
        for group in (md_authors, xmp_authors, info_authors):
            if group:
                authors = group
                break
    if not authors and fn_author:
        authors = [fn_author]

    # Universal post-sanitation: strip leftover <sup>…</sup>/<sub>…</sub>
    # markup that sneaks through via markdown/XMP author strings (e.g.
    # affiliation markers like ``<sup>c</sup>``).
    authors = [a for a in (_strip_inline_markup(a) for a in authors) if a]
    # Drop journal-abbreviation tokens that survived earlier filters
    # (``ACS Nano``, ``Adv. Mater``, ``Adv. Funct. Mater``). These leak
    # in via reference-list lines that happen to mention the filename
    # surname; the strategy-1 fix in ``extract_authors_from_markdown``
    # catches most cases, but XMP / /Info paths can still drop them in.
    authors = [a for a in authors if not _looks_like_journal_name(a)]

    year = fn_year or xmp.get("year") or extract_year_from_pdf_meta(info)

    doi = extract_document_doi(md_text)
    # If a caller already resolved the DOI upstream, or merely probed it
    # in an earlier ingest pass, skip the expensive raw-PDF fallback scan
    # that re-opens the PDF just to find a DOI we already have.
    already_probed = bool(resolved) or bool(doi_hint)
    if not doi and not already_probed:
        doi = extract_pdf_doi_fallback(path)
    if not doi and xmp.get("doi"):
        doi = extract_doi(xmp["doi"]) or ""
    if not doi and doi_hint:
        doi = doi_hint

    metadata = {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi or "",
        "summary": extract_summary(md_text),
    }
    metadata.update(publication)
    if xmp.get("keywords"):
        metadata["keywords"] = xmp["keywords"]
    return metadata


def validate_authors_against_filename(
    authors: list[str], fn_author: str | None,
) -> list[str]:
    """Keep an extracted author list only if it includes the filename author.

    Our filenames encode the first author's surname by convention
    (``[2023 Song]``). When ``extract_authors_from_markdown`` returns a list
    that contains no variant of that surname, the extractor almost certainly
    latched onto a title, banner, or section label that superficially looked
    like a comma-separated list. In that case the caller should fall back to
    ``[fn_author]``. Returns ``[]`` to signal rejection.
    """
    if not authors or not fn_author:
        return authors
    surname = _extract_surname(fn_author) or fn_author
    if not surname:
        return authors
    folded = surname.casefold()
    for name in authors:
        if folded in name.casefold():
            return authors
    return []


def clean_filename_title(filename: str) -> str:
    """Derive a human-readable title from a corpus filename.

    Strips the ``[YYYY Author]`` prefix, the trailing ``_<hexhash>``
    incremental-ingest suffix and any file extension, then replaces
    ``_``/``-`` with spaces and collapses whitespace. Returns ``""`` when
    nothing readable remains.
    """
    if not filename:
        return ""
    stem = re.sub(r"\.(?:pdf|docx|pptx|md|html?)$", "", filename, flags=re.IGNORECASE)
    _, _, fn_title = parse_filename(filename)
    base = fn_title if fn_title else stem
    # Drop the leading ``[YYYY Author]`` bracket when parse_filename didn't match
    # (parse_filename returns None for some filename variants).
    base = re.sub(r"^\[\d{4}(?:\s+[^\]]+)?\]\s*", "", base)
    base = _FILENAME_HASH_SUFFIX_RE.sub("", base)
    base = base.replace("_", " ").replace("-", " ")
    base = re.sub(r"\s+", " ", base).strip()
    return base


def parse_authors(raw: str) -> list[str]:
    raw = raw.replace(";", ",").replace(" and ", ",")
    parts = [_strip_trailing_affiliation_letter(a.strip()) for a in raw.split(",") if a.strip()]
    parts = [p for p in parts if p]
    assembled: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if i + 1 < len(parts):
            nxt = parts[i + 1].strip()
            is_initials = bool(re.match(r"^[A-Z][.\s]*(?:[A-Z]\.?\s*)*$", nxt))
            # Accept a plain Capitalized given name (``Tian``) OR a hyphenated
            # Chinese given name (``Tian-Yu``, ``Jia-Lin``). Hyphens are the
            # only thing that was missing here -- the old ``[A-Z][a-z]{1,14}``
            # regex silently rejected half of the Chinese author lists we
            # ingest.
            is_first_name = bool(
                re.match(r"^[A-Z][a-z]{1,14}(?:-[A-Z][a-z]{1,14})?$", nxt)
                and len(part.split()) == 1
                and part[0:1].isupper()
            )
            if is_initials or is_first_name:
                assembled.append(f"{nxt} {part}")
                i += 2
                continue
        assembled.append(part)
        i += 1
    return [a for a in assembled if _is_valid_author(a)]


def _flatten_xmp_authors(raw_list: list[str]) -> list[str]:
    """Flatten XMP ``dc:creator`` entries into individual author names.

    ``dc:creator`` is supposed to be one ``rdf:li`` per author, but some
    publishers (notably IOP, Elsevier) stuff the whole byline into a single
    ``rdf:li`` separated by commas/semicolons. Split those with
    ``parse_authors`` (which validates each name); validate the single-name
    entries too, so an editorial-band line captured as a lone creator
    ("Received 5th July") cannot pass through unchecked.
    """
    out: list[str] = []
    for raw in raw_list:
        if not raw:
            continue
        if re.search(r"[,;]| and ", raw):
            out.extend(parse_authors(raw))
        elif _is_valid_author(raw):
            out.append(raw)
    return out


# Canonical name-particle / name-suffix vocabularies. Kept here so the
# rules in bibtex.py (_clean_author_name, _author_has_prose_residue)
# share the same membership — adding a new particle (e.g. "zu", "vor")
# only has to happen in one place.
NAME_PARTICLES = frozenset({
    "van", "von", "der", "de", "da", "di", "la", "le", "du",
    "del", "den", "dos", "el", "al", "bin", "ibn",
})
NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


# A single lowercase letter surrounded by whitespace at the end of an author
# token is an affiliation superscript that was flattened inline ("Mi Hyang
# Park a"). We strip it when not preceded by a period (so proper initials
# like "J. Smith" are left alone).
_TRAILING_AFFIL_LETTER_RE = re.compile(r"(?<=[a-z])\s+[a-z]{1,2}$")


def _strip_trailing_affiliation_letter(token: str) -> str:
    token = token.strip()
    if not token:
        return token
    # Repeat to strip double markers like "... Vu a a" → "... Vu".
    while True:
        new = _TRAILING_AFFIL_LETTER_RE.sub("", token).strip()
        if new == token:
            return new
        token = new


def extract_doi(text: str) -> str | None:
    m = re.search(r"(10\.\d{4,}/[^\s<>\]]+)", text)
    if m:
        doi = re.split(r"[?#&\]]", m.group(1), maxsplit=1)[0]
        doi = doi.rstrip(".,;)]}>")
        return _normalise_doi_path(doi)
    return None


# Supplementary / supporting / publisher-variant DOI suffixes. A publisher
# often exposes the supplemental-info file under the DOI path; the registered
# DOI is the prefix. Truncate so DOI negotiation hits the real record.
_DOI_SUFFIX_TRIM = re.compile(
    r"/(?:suppl_file|supplementary|supplemental|supporting|"
    r"suppdata|appendix|pdf|fulltext)(?:/.*)?$",
    re.IGNORECASE,
)


def _normalise_doi_path(doi: str) -> str:
    """Strip known publisher-variant tails that don't belong in a DOI.

    Examples:
        ``10.1021/acsami.4c11743/suppl_file/...`` → ``10.1021/acsami.4c11743``
        ``10.1038/s41467-023-39033-z.pdf`` → ``10.1038/s41467-023-39033-z``
    """
    trimmed = _DOI_SUFFIX_TRIM.sub("", doi)
    # Also strip trailing file extensions that sometimes follow a valid DOI.
    trimmed = re.sub(r"\.(?:pdf|html?|xml)$", "", trimmed, flags=re.IGNORECASE)
    return trimmed.rstrip(".,;)]}>")


def extract_document_doi(md_text: str) -> str | None:
    """Extract a document DOI while ignoring the references section."""
    return extract_doi(_pre_references_window(md_text))


def extract_pdf_doi_fallback(path) -> str | None:
    """Scan a PDF via pymupdf for a DOI printed on page 1-2 or the last page.

    Marker classifies the header/footer band that usually carries the DOI
    (e.g. ``doi: 10.1021/...`` or ``https://doi.org/...``) as page furniture
    and drops it from the markdown. pymupdf preserves that text in its raw
    page extraction, so we can recover the DOI for ~all journal PDFs that
    Marker otherwise reports as DOI-less. Returns None if pymupdf is
    unavailable or no DOI pattern matches.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return None
    try:
        doc = fitz.open(str(path))
    except Exception:  # noqa: BLE001 - any fitz-internal open failure
        return None
    try:
        pages = [doc[i].get_text() for i in range(min(2, len(doc)))]
        if len(doc) > 2:
            pages.append(doc[-1].get_text())
        return extract_doi("\n".join(pages))
    finally:
        doc.close()


def extract_year_from_pdf_meta(meta: dict) -> int | None:
    for key in ("creationDate", "modDate"):
        val = meta.get(key, "")
        m = re.search(r"((?:19|20)\d{2})", val)
        if m:
            return int(m.group(1))
    return None


def extract_summary(md_text: str) -> str | None:
    """Extract a document summary using slide-aware → labeled-section →
    first-prose-paragraph → first-400-words fallbacks.
    """
    slides = _parse_slides(md_text)
    if len(slides) >= 3:
        summary = _synthesize_slide_summary(slides)
        if summary and len(summary) > 50:
            return summary

    search_text = clean_markdown(md_text[:10000])

    label_re = re.compile(
        r"(?:^|\n)\s*(?:#+\s*)?"
        r"(?:abstract|summary|executive\s+summary|overview|scope|synopsis"
        r"|project\s+(?:summary|description)|purpose)"
        r"\s*[:\-—.]*\s*",
        re.IGNORECASE,
    )
    match = label_re.search(search_text)
    if match:
        after_label = search_text[match.end() :]
        end_re = re.compile(
            r"\n\s*(?:#+\s+|(?:keywords?|introduction|index\s+terms"
            r"|i\.\s+introduction|table\s+of\s+contents|background)\b)",
            re.IGNORECASE,
        )
        end_match = end_re.search(after_label)
        text = (after_label[: end_match.start()] if end_match else after_label[:3000]).strip()
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        text = re.sub(r"\n{2,}", "\n\n", text)
        paragraphs = text.split("\n\n")
        text = paragraphs[0].strip()
        if len(text.split()) < 50 and len(paragraphs) > 1:
            for extra in paragraphs[1:]:
                extra = extra.strip()
                if _is_noise_paragraph(extra):
                    break
                text += " " + extra
                if len(text.split()) >= 50:
                    break
        if len(text) > 50 and not _is_noise_paragraph(text):
            return clean_markdown(text)

    paragraphs = re.split(r"\n\s*\n", search_text)
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        if _is_noise_paragraph(para):
            continue
        if len(para) > 100 and re.search(r"[.!?]", para):
            return clean_markdown(para)

    body_words: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("#") or _is_noise_paragraph(para) or len(para) < 10:
            continue
        body_words.extend(para.split())
        if len(body_words) >= 400:
            break
    if body_words:
        text = " ".join(body_words[:400])
        last_period = max(text.rfind(". "), text.rfind(".\n"), text.rfind("."))
        if last_period > 50:
            text = text[: last_period + 1]
        return clean_markdown(text)

    return None


def extract_venue(md_text: str) -> str | None:
    """Extract a likely journal / venue name from parser markdown.

    This is intentionally conservative: it only accepts structural publisher
    patterns that show up in paper front/back matter, and returns ``None`` for
    generic landing text such as "Contents lists available at ScienceDirect".
    """
    window = _pre_references_window(md_text)

    for line in window.splitlines():
        venue = _venue_from_homepage_line(line)
        if venue:
            return venue

    for line in window.splitlines():
        venue = _venue_from_italic_citation(line)
        if venue:
            return venue

    for line in window.splitlines():
        venue = _venue_from_volume_line(line, require_heading=False)
        if venue:
            return venue

    # Some parser outputs leave the journal citation as a final heading after
    # article body text, e.g. "## Nature 453, 80-83 (2008)". Only accept this
    # whole-document scan for headings to avoid harvesting reference entries.
    for line in md_text.splitlines():
        venue = _venue_from_volume_line(line, require_heading=True)
        if venue:
            return venue

    return None


def extract_publication_fields(md_text: str) -> dict[str, str]:
    """Extract BibTeX-ready publication fields from parser markdown."""
    window = _pre_references_window(md_text)

    fields: dict[str, str] = {}

    for line in window.splitlines():
        line_fields = _publication_from_cite_this_line(line)
        if line_fields:
            fields.update(line_fields)
            return fields

    for line in window.splitlines():
        venue = _venue_from_published_by_line(line) or _venue_from_homepage_line(line)
        if venue:
            fields.setdefault("venue", venue)
            return fields

    for line in window.splitlines():
        line_fields = _publication_from_italic_citation(line)
        if line_fields:
            fields.update(line_fields)
            return fields

    for line in window.splitlines():
        line_fields = _publication_from_volume_line(line, require_heading=False)
        if line_fields:
            fields.update(line_fields)
            return fields

    for line in md_text.splitlines():
        line_fields = _publication_from_volume_line(line, require_heading=True)
        if line_fields:
            fields.update(line_fields)
            return fields
    return fields


def clean_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = text.strip()
    # Slug-shaped title: pure hyphen-joined slug from a publisher's URL
    # path used as a filename ("artificial-synapse-based-on-..."). When
    # there are 5+ hyphens, no internal spaces, and the body is all
    # lowercase letters/digits/hyphens, replace hyphens with spaces and
    # title-case so the corpus reads naturally.
    if (
        text
        and " " not in text
        and text.count("-") >= 5
        and re.fullmatch(r"[a-z0-9\-]+", text)
    ):
        text = " ".join(w.capitalize() for w in text.split("-"))
    return text


def is_garbled_title(title: str) -> bool:
    if re.search(r"\d+\.\.\d+", title):
        return True
    if re.match(r"^[a-z0-9_\-]{3,20}$", title, re.IGNORECASE):
        return True
    if re.match(r"^untitled$", title, re.IGNORECASE):
        return True
    if len(title) < 5 and not any(c.isalpha() for c in title):
        return True
    if re.match(r"^[a-z]{2,4}[_\-]", title) and re.search(r"\d{4}", title):
        return True
    return False


def _strip_yaml_frontmatter(md_text: str) -> str:
    """Drop the leading ``---\\n...\\n---`` block if present.

    Obsidian-style YAML frontmatter is machine-generated metadata (source
    paths, cites, similar_to) and should never be scanned as author or
    publication content. Its ``source_path`` line in particular contains
    the filename including its extension — a trap for any author parser
    that anchors on the fn_author surname.
    """
    if not md_text.startswith("---"):
        return md_text
    # Find the closing delimiter on its own line. Accept both ``---`` and
    # ``...`` as YAML terminators.
    match = re.search(r"\n(?:---|\.\.\.)\s*\n", md_text[3:])
    if not match:
        return md_text
    return md_text[3 + match.end():]


def extract_authors_from_markdown(md_text: str, fn_author: str | None = None) -> list[str]:
    """Find the paper's author list in the rendered markdown body.

    Many journal PDFs (AIP, IOP, APL, etc.) prepend a "landing page" with a
    recommendations block ("You may also like ...") whose own author lists
    appear *before* the real paper title. The first-heading heuristic is
    fooled by these. When the caller passes ``fn_author`` (surname parsed
    from the filename) we anchor on it: scan the first ~12k chars for any
    reasonable-length line containing that surname as a whole word, and
    return the first parse whose names include the surname.
    """
    # Strip Obsidian-style YAML frontmatter before scanning. The frontmatter
    # contains source_path / cites / similar_to lines that will snare any
    # surname-anchored scanner (e.g. `source_path: "...[2012 Li] Title.pdf"`
    # matches `fn_author="Li"` and the tail of the filename gets parsed as
    # authors).
    body = _strip_yaml_frontmatter(md_text)
    # Widened from 12000 to 40000 to reach the author byline on PDFs
    # with long front-matter (DoD Form 298 reports, thesis cover pages,
    # journal landing pages with "You may also like" recommendation
    # blocks). The extra scan is cheap — per-line regex, not embedder.
    window = body[:40000]
    # Clip at the references heading so a reference entry can never be
    # the first surname match. The reference-list-shape guard further
    # down requires a byline-shape candidate to already exist before it
    # rejects a ``Lastname Initial`` line — that exception is needed
    # for Asian-journal bylines (Chinese Physics Letters, Acta Phys
    # Sin) which are themselves printed in ``Lastname Initial`` form.
    # Without this clip, a paper with no visible byline would return
    # the first reference entry as authors.
    ref_match = _REFERENCES_HEADING_RE.search(window)
    if ref_match:
        window = window[: ref_match.start()]
    lines = window.split("\n")

    # Strategy 1: filename-surname anchor. Most robust when the PDF has a
    # landing page that would otherwise fool a first-heading scanner.
    #
    # Algorithm (after two rounds of audit):
    #   1. Scan lines in document order for any reasonable-length line
    #      containing the filename surname.
    #   2. Parse to a name list, drop journal-abbreviation tokens.
    #   3. Reject reference-list-shaped candidates (``Lastname Initial``
    #      majority) ONLY if we already have at least one byline-shape
    #      candidate. Some Asian-journal bylines (Chinese Physics Letters,
    #      Acta Phys Sin) are themselves printed in ``Lastname Initial``
    #      form — the first surname-matching line in the document is
    #      virtually always the byline, so always keep that first one.
    #   4. Collect the first ``_AUTHOR_CANDIDATE_LIMIT`` valid multi-author
    #      candidates. Then pick the LONGEST among them. This keeps the
    #      long-form byline (``Jonathan Joshua Yang, Strachan, Williams``)
    #      over the running-header short form (``J. J. Yang``) without
    #      reaching out to the reference list.
    #   5. Fall back to the earliest single-author candidate if no
    #      multi-author line was found.
    if fn_author:
        surname = _extract_surname(fn_author)
        if surname:
            surname_re = re.compile(rf"\b{re.escape(surname)}\b", re.IGNORECASE)
            multi_candidates: list[list[str]] = []
            best_single: list[str] = []
            for line in lines:
                s = line.strip()
                if not s or len(s) > 500:
                    continue
                if not surname_re.search(s):
                    continue
                if re.search(r"(?i)(correspondence|nanotechnology|j\. phys\.)", s):
                    continue
                names = _parse_author_line(_author_line_prefix(s))
                if not names or not any(surname.lower() in n.lower() for n in names):
                    continue
                names = [n for n in names if not _looks_like_journal_name(n)]
                if not names or not any(surname.lower() in n.lower() for n in names):
                    continue
                if _looks_like_reference_list(names) and multi_candidates:
                    # Already have a byline-shape candidate; this
                    # ``Lastname Initial`` line is a reference entry.
                    continue
                if len(names) >= 2:
                    multi_candidates.append(names)
                    if len(multi_candidates) >= _AUTHOR_CANDIDATE_LIMIT:
                        break
                elif not best_single:
                    best_single = names
            if multi_candidates:
                return max(multi_candidates, key=len)
            if best_single:
                return best_single

    # Strategy 2: first-heading heuristic. Used for single-author papers or
    # when no fn_author hint is available. Unchanged from the original.
    title_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped.lstrip("# ")) > 5:
            title_idx = i
            break
    if title_idx < 0:
        return []
    candidates: list[str] = []
    for i in range(title_idx + 1, min(title_idx + 15, len(lines))):
        line = lines[i].strip()
        if not line:
            continue
        if re.match(r"(?i)^#*\s*\*?\*?(abstract|introduction|index\s+terms|keywords)", line):
            break
        candidates.append(_author_line_prefix(line))
    for line in candidates:
        names = _parse_author_line(line)
        if len(names) >= 2:
            return names
    return []


def _author_line_prefix(line: str) -> str:
    """Keep the author segment before affiliation/email prose starts."""
    return re.split(
        r"(?i)\b(?:department|institute|school|laboratory|centre|center|university"
        r"|college|faculty|e-mail|email)\b|@",
        line,
        maxsplit=1,
    )[0].strip(" ,;")


# Reference-list entries print author names as ``Lastname Initial`` (e.g.
# ``Hu M``, ``Yang JJ``, ``Li-Wei S-K``). The byline format is the
# opposite: ``M. Hu``, ``J. J. Yang``, with the initial first and
# usually followed by a period. When a candidate "author line" parses
# to a list dominated by Lastname-Initial shapes, it's a citation entry
# that happened to mention the filename surname — not the real byline.
#
# Asian-journal bylines (Chinese Physics Letters, Acta Phys Sin) print
# author lists in the same ``Lastname Initial`` form, so the rejection
# in ``extract_authors_from_markdown`` only fires when a real byline
# was already seen earlier in the document.
_REF_LIST_NAME_RE = re.compile(
    r"^[A-Z][a-zA-Z'\-]+\s+[A-Z]{1,3}(?:\-[A-Z])?$"
)

# How many multi-author candidates to collect before picking the
# longest. Three is enough to cover a short running-header form
# (``J. J. Yang``) plus the full byline (``Jonathan Joshua Yang,
# Strachan, Williams, ...``) plus one extra slot for an interstitial
# co-author block, while staying well within the front-matter region
# so reference-list contamination cannot enter.
_AUTHOR_CANDIDATE_LIMIT = 3


def _looks_like_reference_list(names: list[str]) -> bool:
    """True when a STRICT majority of names match the ``Lastname Initial``
    shape that only appears in citation entries. Ties between byline
    and reference shape go to byline — when the list is genuinely
    mixed, we want to preserve information rather than aggressively
    drop the line.
    """
    if not names or len(names) < 2:
        return False
    ref_shape = sum(1 for n in names if _REF_LIST_NAME_RE.match(n))
    return ref_shape > len(names) / 2


# JATS journal-name tokens. Author lines occasionally include a trailing
# journal abbreviation (``ACS Nano``, ``Adv. Mater``, ``Adv. Funct.
# Mater``, ``Nat. Commun``) when the parser latches onto a reference
# line. We detect them by tokenising on whitespace and checking that
# EVERY token (with trailing period stripped) is in the known-journal
# vocabulary. A real author name will always have at least one token
# outside this vocabulary (Joshua, Williams, Strachan, etc.).
_JOURNAL_TOKENS = frozenset({
    # Prefix abbreviations.
    "adv", "acta", "annu", "appl", "acs", "ieee", "nat", "sci", "phys",
    "chem", "mater", "j", "proc", "rev", "trans", "comm", "commun",
    "npg", "inorg", "surf", "solid", "opt", "anal", "synth",
    "microelectron", "nano", "nanoscale", "nanotechnology", "curr",
    "cell", "nature", "science", "springer", "funct", "rsc",
    # Subject suffix words.
    "lett", "letters", "eng", "tech", "today", "reviews", "review",
    "reports", "report", "interfaces", "energy", "electron",
    "electronics", "mol", "soc", "acad", "crystallogr", "photonics",
    "catalysis", "plus", "asia", "materials", "communications",
    "synthesis", "metals",
})


def _looks_like_journal_name(name: str) -> bool:
    """True when a parsed author name is actually a journal abbreviation
    (``ACS Nano``, ``Adv. Mater``, ``Adv. Funct. Mater``, ``Nat.
    Commun``). Detected by tokenising on whitespace and confirming
    every token belongs to the journal vocabulary.
    """
    if not name:
        return False
    tokens = [t.rstrip(".,").lower() for t in name.split()]
    tokens = [t for t in tokens if t]
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    return all(t in _JOURNAL_TOKENS for t in tokens)


def _extract_surname(author_hint: str) -> str:
    """Get the surname from a filename author tag like 'Strukov' or 'Jie Ma'."""
    tokens = [t for t in re.split(r"\s+", author_hint.strip()) if t]
    if not tokens:
        return ""
    # Filename author tags are usually "Surname" or "First Last" — the last
    # token is conventionally the surname in both cases. Strip any trailing
    # punctuation.
    return tokens[-1].strip(" .,;:")


# --- internal ------------------------------------------------------------


_REFERENCES_HEADING_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:references|bibliography|works\s+cited)\b"
)


def _pre_references_window(md_text: str) -> str:
    """Return early paper text, stopping before references when detectable."""
    window = md_text[:12000]
    match = _REFERENCES_HEADING_RE.search(window)
    if match:
        return window[: match.start()]
    return window


_HOMEPAGE_MARKER_RE = re.compile(r"\s+journal\s+homepage\s*:", re.IGNORECASE)


def _venue_from_homepage_line(line: str) -> str | None:
    # Fast fail: skip the expensive regex + string-cleanup work when the
    # line doesn't contain "journal homepage" at all. extract_publication_
    # fields calls this helper on every markdown line of every doc
    # (13k+ calls on a 200-paper corpus); the early exit drops wave D
    # regex cost from ~55 s to ~0.5 s.
    if "journal homepage" not in line.casefold():
        return None
    cleaned = _strip_heading(line)
    match = _HOMEPAGE_MARKER_RE.search(cleaned)
    if not match:
        return None
    return _clean_venue_candidate(cleaned[: match.start()])


def _venue_from_published_by_line(line: str) -> str | None:
    cleaned = _plain_markdown_line(line)
    match = re.search(
        r"(?P<venue>[A-Z][A-Za-z0-9& .:'/\-]{2,120}?)\s+published\s+by\b",
        cleaned,
        re.IGNORECASE,
    )
    if not match:
        return None
    candidate = match.group("venue").split(".")[-1]
    return _clean_venue_candidate(candidate)


def _venue_from_italic_citation(line: str) -> str | None:
    fields = _publication_from_italic_citation(line)
    return fields.get("venue") if fields else None


def _publication_from_italic_citation(line: str) -> dict[str, str] | None:
    match = re.match(
        r"^\s*(?:#+\s*)?[_*]{1,2}(?P<venue>[^_*]{2,120})[_*]{1,2}"
        r"\s+(?P<volume>\d{1,4})\s*,\s*(?P<pages>[A-Za-z]?\d+[A-Za-z]?(?:[-\u2013]\d+)?)",
        line,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _publication_fields_from_match(match)


def _publication_from_cite_this_line(line: str) -> dict[str, str] | None:
    cleaned = _plain_markdown_line(line)
    cleaned = re.sub(r"(?i)^cite\s+this:\s*", "", cleaned).strip()
    match = re.match(
        r"^(?P<venue>[A-Z][A-Za-z0-9& .:'/\-]{1,100}?)\s+"
        r"(?:19|20)\d{2}\s*,\s*"
        r"(?P<volume>\d{1,4})\s*,\s*"
        r"(?P<pages>[A-Za-z]?\d+[A-Za-z]?(?:[-\u2013]\d+)?)",
        cleaned,
    )
    if not match:
        return None
    return _publication_fields_from_match(match)


def _venue_from_volume_line(line: str, *, require_heading: bool) -> str | None:
    fields = _publication_from_volume_line(line, require_heading=require_heading)
    return fields.get("venue") if fields else None


def _publication_from_volume_line(
    line: str, *, require_heading: bool
) -> dict[str, str] | None:
    if require_heading and not re.match(r"^\s*#+\s+", line):
        return None
    cleaned = _plain_markdown_line(line)
    match = re.match(
        r"^(?P<venue>[A-Z][A-Za-z0-9& .:'/\-]{1,100}?)\s+"
        r"(?P<volume>\d{1,4})\s*,\s*(?P<pages>[A-Za-z]?\d+[A-Za-z]?(?:[-\u2013]\d+)?)"
        r"\s*\((?:19|20)\d{2}\)",
        cleaned,
    )
    if not match:
        return None
    return _publication_fields_from_match(match)


def _publication_fields_from_match(match: re.Match[str]) -> dict[str, str] | None:
    venue = _clean_venue_candidate(match.group("venue"))
    if not venue:
        return None
    fields = {"venue": venue}
    volume = (match.groupdict().get("volume") or "").strip()
    pages = (match.groupdict().get("pages") or "").strip().replace("\u2013", "-")
    if volume:
        fields["volume"] = volume
    if pages:
        fields["pages"] = pages
    return fields


def _strip_heading(line: str) -> str:
    return re.sub(r"^\s*#+\s*", "", line.strip())


def _plain_markdown_line(line: str) -> str:
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    return clean_markdown(_strip_heading(line))


def _clean_venue_candidate(candidate: str) -> str | None:
    candidate = clean_markdown(candidate)
    candidate = re.sub(r"<[^>]+>", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -:;,")
    candidate = re.sub(r"(?i)^(?:cite\s+as|citation)\s*:\s*", "", candidate).strip()
    candidate = re.sub(
        r"\s+journal\s+homepage\b.*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" -:;,")
    if not _is_valid_venue(candidate):
        return None
    return candidate


def _is_valid_venue(candidate: str) -> bool:
    lower = candidate.lower()
    if len(candidate) < 2 or len(candidate) > 120:
        return False
    if not any(c.isalpha() for c in candidate):
        return False
    if re.search(r"https?://|www\.|@", lower):
        return False
    noise = (
        "contents lists available",
        "sciencedirect",
        "journal homepage",
        "articles you may be interested in",
        "article info",
        "abstract",
        "keywords",
        "introduction",
        "references",
        "bibliography",
        "doi",
        "citation",
        "downloaded",
        "copyright",
        "view export",
        "accepted manuscript",
    )
    if any(marker in lower for marker in noise):
        return False
    if len(candidate.split()) > 12:
        return False
    return True


def _is_heading_noise(heading: str) -> bool:
    lower = heading.casefold()
    if lower in {
        "articles you may be interested in",
        "letters",
        "paper",
        "articles",
        "review",
        "open access",
        "references",
        "bibliography",
        "works cited",
        "affiliations",
        "abstract",
        "article",
        "check for updates",
        "rapid communications",
        "topical review",
        "introduction",
        "original article",
        "research article",
        "communication",
        "full paper",
        "you may also like",
        "conflicts of interest",
        "acknowledgements",
        "acknowledgments",
        "supplementary information",
        "supporting information",
        "author information",
        "data availability",
        "article open",
        "highlights",
        "citation",
        "reviewed by",
        "iscience",
        "applied sciences and engineering",
        "nanoscale",
    }:
        return True
    # Numbered section headers (e.g. "1. Introduction")
    if re.match(r"^\d+\.\s", heading):
        return True
    # "PAPER - OPEN ACCESS", ". RESEARCH PAPER .", etc.
    stripped = lower.strip(". -")
    if stripped in {"research paper", "paper", "open access", "research article"}:
        return True
    return False

_AUTHOR_NOISE = {
    "ieee",
    "member",
    "senior",
    "fellow",
    "student",
    "life",
    "associate",
    "et",
    "al",
    "and",
    "the",
    "of",
    "vol",
    "no",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "transactions",
    "journal",
    "proceedings",
    "letters",
    # Affiliation / section nouns that occasionally show up as
    # single-token "names" when a parser misclassifies a header
    # fragment as a byline. The mononym path in ``_is_valid_author``
    # otherwise lets long-enough single tokens through.
    "department",
    "departments",
    "institute",
    "institution",
    "school",
    "schools",
    "laboratory",
    "laboratories",
    "centre",
    "center",
    "university",
    "college",
    "faculty",
    "abstract",
    "introduction",
    "background",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "appendix",
    "references",
    "bibliography",
    "acknowledgments",
    "acknowledgements",
}


# Journal editorial-workflow date lines ("Received 5th July 2016",
# "Accepted 2nd October 2016", "Revised ...") sit in the same layout band
# as the author byline on RSC / Wiley / Nature PDFs. When a PDF has no
# ``[YYYY Author]`` filename hint the author scanner falls back to the
# heading heuristic and can latch onto these lines: "Received 5th July"
# clears every existing guard (only "July" is in ``_AUTHOR_NOISE``, and
# the digit-at-end check misses the mid-token "5th"). Two structural
# markers identify such a line — an opening submission verb, or an
# ordinal date token — and neither ever appears in a real name.
_EDITORIAL_WORKFLOW_WORDS = frozenset({
    "received", "accepted", "revised", "resubmitted", "submitted",
    "published", "communicated", "corrected", "reviewed",
})
_ORDINAL_DATE_RE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b", re.IGNORECASE)
_MONTH_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
})


def _looks_like_editorial_line(name: str) -> bool:
    """True when a candidate author is a journal editorial-workflow date
    line ("Received 5th July", "Accepted 2nd October") rather than a byline.

    Two signals, both structural to publisher front matter: the candidate
    opens with a submission verb, or it pairs an ordinal date token with a
    month name. The ordinal is gated on a month so a generational suffix
    ("John Smith 3rd") is not mistaken for a date.
    """
    if not name:
        return False
    tokens = [t.lower().strip(".,;:") for t in name.split()]
    if not tokens:
        return False
    if tokens[0] in _EDITORIAL_WORKFLOW_WORDS:
        return True
    if _ORDINAL_DATE_RE.search(name) and any(t in _MONTH_WORDS for t in tokens):
        return True
    return False


def _is_valid_author(name: str) -> bool:
    name = name.strip()
    if not name or len(name) < 2:
        return False
    words = name.split()
    if len(words) == 1:
        # Single-token names: legitimate mononyms (Hadiyawarman,
        # Madonna, Pel\u00e9) plus CJK / Hangul ideographs. Accept when the
        # token is 5+ letters, starts with uppercase, contains only
        # letters, and isn't all-caps (rules out section headers like
        # ``INTRODUCTION`` / ``ABSTRACT`` that the heading scanner
        # otherwise feeds us).
        is_cjk = any(
            "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af"
            for c in name
        )
        is_mononym = (
            len(name) >= 5
            and name[0].isupper()
            and name != name.upper()
            and all(c.isalpha() or c in "'-" for c in name)
            and name.lower() not in _AUTHOR_NOISE
        )
        if not (is_cjk or is_mononym):
            return False
    if len(words) > 5:
        return False
    if not words[0][0:1].isupper():
        return False
    if re.search(r"[(\[|]|\d+\s*$", name):
        return False
    # Reject names containing a colon: real author names never do, but
    # journal running headers ("CHUA: MEMRISTOR-MISSING CIRCUIT ELEMENT
    # 509") and byline/title concatenations routinely produce
    # colon-joined strings that a surname-anchored scanner otherwise
    # accepts as authors.
    if ":" in name:
        return False
    if all(w.lower() in _AUTHOR_NOISE for w in words):
        return False
    # Reject journal / venue names that slip through citation parsing.
    if _looks_like_journal(name):
        return False
    # Reject "et al." fragments that leak from citation parsing.
    if "et al" in name.lower():
        return False
    # Reject names containing ampersand (citation parsing artifact like
    # "B. & Alibart" from broken "Alibart, F. & Strukov, D. B." splits).
    if "&" in name:
        return False
    # Reject well-known place names that leak from affiliations.
    if _normalize_lower(name) in _PLACE_NAMES:
        return False
    # Reject names containing common non-name words (title/topic fragments).
    if _has_non_name_words(name):
        return False
    # Reject journal editorial-workflow date lines ("Received 5th July",
    # "Accepted 2nd October") that publishers print in the byline band.
    if _looks_like_editorial_line(name):
        return False
    return True


# Words that never appear in a person's name. When any of these appear
# (case-insensitive) in a candidate "author" string, it's a citation
# parsing artifact (e.g. "J. Vector-matrix multiply" or "Information
# Technology").
_NON_NAME_WORDS = {
    "multiply",
    "learning",
    "training",
    "network",
    "circuit",
    "device",
    "memory",
    "synapse",
    "computing",
    "technology",
    "information",
    "system",
    "systems",
    "analysis",
    "design",
    "control",
    "model",
    "models",
    "method",
    "theory",
    "simulation",
    "process",
    "energy",
    "performance",
    "structure",
    "material",
    "materials",
    "effect",
    "effects",
    "properties",
    "application",
    "applications",
    "based",
    "using",
    "toward",
    "towards",
    "novel",
    "high",
    "low",
    "ultra",
    "nano",
    "micro",
    "oxide",
    "metal",
    "thin",
    "film",
    "layer",
    "switching",
    "resistive",
    "neuromorphic",
    "memristor",
    "memristive",
    "crossbar",
    "array",
    "integrated",
    "operation",
    "architecture",
    "reconfigurable",
    "analog",
    "digital",
    "vector",
    "matrix",
    "wiley",
    "springer",
    "elsevier",
    "taylor",
    "francis",
    "usa",
    "ieee",
    "acm",
}


_PLACE_NAMES = {
    "san francisco",
    "new york",
    "los angeles",
    "san jose",
    "san diego",
    "washington",
    "boston",
    "chicago",
    "seattle",
    "london",
    "berlin",
    "tokyo",
    "beijing",
    "shanghai",
    "usa wiley",
    "usa_ wiley",
}


def _normalize_lower(s: str) -> str:
    return re.sub(r"[_\s]+", " ", s.lower()).strip()


def _has_non_name_words(name: str) -> bool:
    """Return True if name contains words that indicate a title fragment, not a person."""
    words_lower = {w.lower().rstrip(".,;:-") for w in name.split()}
    return bool(words_lower & _NON_NAME_WORDS)


# Abbreviated journal tokens. A name containing 2+ of these is almost
# certainly a publication venue, not a person.
_JOURNAL_ABBREV_TOKENS = {
    "adv",
    "appl",
    "chem",
    "commun",
    "electron",
    "eng",
    "funct",
    "lett",
    "mater",
    "nanotechnol",
    "phys",
    "rev",
    "sci",
    "technol",
    "trans",
    "proc",
    "int",
    "conf",
    "symp",
}
_JOURNAL_FULL_WORDS = {
    "journal",
    "proceedings",
    "transactions",
    "letters",
    "review",
    "reviews",
    "annals",
    "bulletin",
    "reports",
    "communications",
    "magazine",
    "quarterly",
    "archives",
    "nano",
    "nature",
    "science",
    "cell",
}


def _looks_like_journal(name: str) -> bool:
    """Return True if ``name`` looks like an abbreviated or full journal title."""
    # Split into normalized tokens (strip trailing dots, commas, etc.)
    tokens = [w.lower().rstrip(".,;:") for w in name.split()]
    # Count abbreviated journal tokens (e.g. "Adv", "Funct", "Mater")
    abbrev_hits = sum(1 for t in tokens if t in _JOURNAL_ABBREV_TOKENS)
    # If 2+ abbreviated tokens match, it's a journal
    if abbrev_hits >= 2:
        return True
    # Full journal-word match
    token_set = set(tokens)
    journal_hits = token_set & _JOURNAL_FULL_WORDS
    if journal_hits:
        non_journal = token_set - _JOURNAL_FULL_WORDS - _JOURNAL_ABBREV_TOKENS
        # All words are journal-ish
        if not non_journal:
            return True
        # 2-word name where one is a journal word ("ACS Nano", "RSC Adv")
        if len(tokens) <= 2:
            return True
    # Single abbreviated token + short name (e.g. "RSC Adv." = 2 words)
    if abbrev_hits >= 1 and len(tokens) <= 2:
        # Check if the other token looks like an abbreviation too (all caps or short)
        non_abbrev = [t for t in tokens if t not in _JOURNAL_ABBREV_TOKENS]
        if all(len(t) <= 4 or t.upper() == t for t in non_abbrev):
            return True
    return False


def _parse_author_line(line: str) -> list[str]:
    cleaned = re.sub(r"^#+\s*", "", line)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    # Strip leading bullet markers (-, *, +, •) that pymupdf4llm leaves on
    # recommendation lists like "- Zhao Jin-Wei, ...".
    cleaned = re.sub(r"^[\-*+•]\s+", "", cleaned)
    cleaned = re.sub(r"_+", " ", cleaned)
    cleaned = re.sub(
        r",?\s*(?:Life |Senior |Student |Associate )?(?:Fellow|Member),?\s*(?:IEEE)?,?",
        ",",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    # Strip parenthetical nicknames / middle-name expansions. Common on
    # byline lines: "Hai (Helen) Li and Robinson E. Pino" — the "(Helen)"
    # middle name otherwise tripped _is_valid_author's paren filter and
    # the whole author got rejected, falling back to just the fn_author
    # surname.
    cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned)
    # Footnote/affiliation marker cluster like " *" or " †" or " ✉" right
    # after a name becomes a hard separator. Convert to comma BEFORE we
    # strip the markers — this is how we pry apart lines where pymupdf4llm
    # concatenated a title and an author list through a footnote star,
    # e.g. "... Memristor * Sungjun Kim, Hyungjin Kim ...".
    cleaned = re.sub(r"(?<=[A-Za-z\)\]])\s*[†‡§✉✱*]+", ",", cleaned)
    cleaned = re.sub(r"[†‡§✉✱*]+", "", cleaned)
    # Treat ampersand as an author separator ("A, B & C").
    cleaned = cleaned.replace("&", ",")
    # Strip trailing per-author affiliation superscripts like "H. Kim 1,2"
    # -> "H. Kim" and "M. R. Mahmoodi 1" -> "M. R. Mahmoodi".
    cleaned = re.sub(
        r",\s*\d+(?:\s*,\s*\d+)*(?:\s*,\s*[a-z])?\)?\s*",
        ", ",
        cleaned,
    )
    cleaned = re.sub(r"(?<=[A-Za-z])(?:\s+\d+(?:\s*,\s*\d+)*)+\b", "", cleaned)
    # Digits glued directly to a surname ("Tian-Yu Wang1, Jia-Lin Meng2")
    # are affiliation superscripts the PDF stripped of whitespace. Remove
    # the trailing digit cluster; keep the name.
    cleaned = re.sub(r"(?<=[A-Za-z])\d+(?=[,\s]|$)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    # Split on commas OR semicolons OR " and " — AIP landing pages often
    # use semicolons ("Yu. Matveyev ; K. Egorov; A. Markeev; A. Zenkevich").
    parts = re.split(r"[,;|]\s*|\s+and\s+", cleaned)
    names: list[str] = []
    for part in parts:
        part = part.strip().rstrip(",. ")
        part = re.sub(r"\s+et\s+al\.?$", "", part, flags=re.IGNORECASE).strip()
        # Strip a leading "and " that survives when the line uses an Oxford
        # comma before "and" (", and X") — the comma splitter leaves
        # "and X" as its own part.
        part = re.sub(r"^(?:and|&)\s+", "", part, flags=re.IGNORECASE).strip()
        # Trailing isolated lowercase letter is an affiliation superscript
        # flattened inline ("Mi Hyang Park a" → "Mi Hyang Park").
        # Guarded on the letter being preceded by lowercase so proper
        # initials ("J. Smith", "J.") survive.
        part = _strip_trailing_affiliation_letter(part)
        if not part:
            continue
        words = part.split()
        if all(w.lower() in _AUTHOR_NOISE for w in words):
            continue
        if re.match(r"^\d", part) or len(part) < 2:
            continue
        if not words[0][0:1].isupper():
            continue
        if len(words) > 5:
            continue
        if len(words) == 1 and not any(
            "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" for c in part
        ):
            continue
        if re.search(r"[(\[|]|\d+\s*$", part):
            continue
        # Reject colon-containing parts: journal running headers
        # ("CHUA: MEMRISTOR-MISSING CIRCUIT ELEMENT 509" after the
        # trailing page-number strip) otherwise fool the
        # surname-anchored scanner. Author names never contain ":".
        if ":" in part:
            continue
        # Reject journal editorial-workflow date lines ("Received 5th
        # July", "Accepted 2nd October") printed in the byline band.
        if _looks_like_editorial_line(part):
            continue
        names.append(part)
    return names


def _is_noise_paragraph(text: str) -> bool:
    """Return True if a paragraph is bibliographic / boilerplate metadata,
    not real content.
    """
    lower = text.lower()
    noise_markers = (
        "authorized licensed use",
        "downloaded on",
        "©",
        "copyright",
        "all rights reserved",
        "using government drawings",
        "this report is the result of",
        "ieee transactions",
        "proceedings of",
        "permission to make digital",
        "this article has been accepted",
        "personal use of this material",
        "redistribution",
        "university of",
        "department of",
        "manuscript received",
        "doi:",
        "digital object identifier",
        "color versions of",
        "published by",
        "accepted for publication",
        "public release; distribution",
        "fundamental research",
        "approved for public",
        "report number",
        "technical report",
        "contract no",
        "scientific and technical information",
        "in the interest of",
        "==> picture",
        "intentionally omitted",
        "----- start of picture text -----",
        "----- end of picture text -----",
    )
    return any(m in lower for m in noise_markers)


# Public alias so other modules don't reach into a private name.
is_noise_paragraph = _is_noise_paragraph


@dataclass
class _Slide:
    index: int
    title: str
    body: str
    notes: str


def _parse_slides(md_text: str) -> list[_Slide]:
    slide_splits = re.split(r"(?=^## (?:Slide \d+))", md_text, flags=re.MULTILINE)
    slides: list[_Slide] = []
    for block in slide_splits:
        block = block.strip()
        if not block:
            continue
        h = re.match(r"^## Slide (\d+)(?::\s*(.+))?$", block, re.MULTILINE)
        if not h:
            continue
        index = int(h.group(1))
        title = (h.group(2) or "").strip()
        rest = block[h.end() :].strip()
        body_lines: list[str] = []
        note_lines: list[str] = []
        for line in rest.splitlines():
            if line.strip().startswith(">"):
                note_lines.append(line.strip().lstrip("> ").strip())
            else:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()
        notes = " ".join(note_lines).strip()
        notes = re.sub(r"^Note:\s*", "", notes, flags=re.IGNORECASE).strip()
        slides.append(_Slide(index=index, title=title, body=body, notes=notes))
    return slides


def _is_conclusion_slide(slide: _Slide) -> bool:
    title_lower = slide.title.lower()
    keywords = (
        "conclusion",
        "concluding",
        "summary",
        "takeaway",
        "key finding",
        "wrap up",
        "wrap-up",
        "closing",
        "final",
        "outlook",
        "future work",
    )
    return any(kw in title_lower for kw in keywords)


def _synthesize_slide_summary(slides: list[_Slide]) -> str:
    parts: list[str] = []
    for slide in slides[:3]:
        slide_text = slide.title or ""
        body_clean = clean_markdown(slide.body)
        if body_clean:
            words = body_clean.split()
            excerpt = " ".join(words[:150])
            slide_text = f"{slide_text}. {excerpt}" if slide_text else excerpt
        if slide.notes:
            notes_clean = clean_markdown(slide.notes)
            words = notes_clean.split()
            notes_excerpt = " ".join(words[:80])
            slide_text = f"{slide_text} {notes_excerpt}" if slide_text else notes_excerpt
        if slide_text:
            parts.append(slide_text.strip())

    tail_slides = slides[-3:] if len(slides) > 3 else []
    conclusion_parts: list[str] = []
    for slide in tail_slides:
        if _is_conclusion_slide(slide):
            conclusion_text = slide.title or ""
            body_clean = clean_markdown(slide.body)
            if body_clean:
                words = body_clean.split()
                excerpt = " ".join(words[:200])
                conclusion_text = f"{conclusion_text}. {excerpt}" if conclusion_text else excerpt
            if slide.notes:
                notes_clean = clean_markdown(slide.notes)
                words = notes_clean.split()
                notes_excerpt = " ".join(words[:100])
                conclusion_text = (
                    f"{conclusion_text} {notes_excerpt}" if conclusion_text else notes_excerpt
                )
            if conclusion_text:
                conclusion_parts.append(conclusion_text.strip())

    if conclusion_parts:
        parts.append("Conclusions: " + ". ".join(conclusion_parts))

    if not parts:
        return ""
    text = ". ".join(parts)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
