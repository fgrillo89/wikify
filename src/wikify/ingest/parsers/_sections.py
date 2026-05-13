"""Shared helper: build (heading_path, start, end) spans from a markdown string.

Used by all parsers (pdf, docx, pptx, html, markdown) so they share one
section-extraction implementation. The chunker consumes these spans
directly.

Two entry points:

* ``section_spans(body)`` — derive spans from markdown ``#+`` headings
  alone. Used for source formats that have no structural TOC (md/html/
  docx/pptx) and as the fallback for PDFs whose ``doc.get_toc()`` is
  empty or short.
* ``toc_spans(body, toc_entries)`` — given a TOC parsed from a PDF
  bookmark tree (``[(level, title, page)]``), match each entry's title
  as a substring of ``body`` and split spans there. Falls back to
  ``section_spans`` if too few TOC titles can be located in the body.
"""

import re

_H_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# Format / control / zero-width characters that PDF outline entries
# frequently carry — used by ``_clean_toc_title`` to scrub raw titles.
_TOC_NOISE_CHARS = ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\u00ad")


# Publisher boilerplate that Docling promotes to ``#`` headings on
# journal-PDF front matter and end matter. These are pure template
# tags — they carry no per-paper information that downstream readers
# would query for. Compared case-insensitively after collapsing
# whitespace + stripping bracketing punctuation.
#
# What is INTENTIONALLY NOT in this set:
#   - ``acknowledgments`` and variants -> classified as ACKNOWLEDGMENTS;
#     dropping the path entry would hide the chunks from
#     ``exclude_kinds=['acknowledgments']`` queries.
#   - ``supporting information`` / ``supplementary information`` ->
#     classified as APPENDIX; same reasoning.
#   - ``author contributions`` / ``data availability`` / ``funding`` /
#     ``conflict of interest`` -> carry meaningful per-paper info
#     (who funded what, what data is public) that researchers query
#     directly. Keep them as named sections so they remain locatable.
_BOILERPLATE_HEADINGS = frozenset({
    "open access",
    "open",
    "*correspondence",
    "correspondence",
    "corresponding authors",
    "corresponding author",
    "citation",
    "copyright",
    "keywords",
    "a r t i c l e i n f o",
    "article info",
    "article history",
    "publisher's note",
    "publisher s note",
    "publishers note",
    "generative ai statement",
    "correction note",
    "corrigendum",
    "associated content",
    "abbreviations",
    "abbreviations used",
    "orcid",
    "author information",
    "materials & correspondence",
    "access",
    "metrics & more",
    "article recommendations",
    "cite this",
    "received",
    "accepted",
    "revised",
    "published",
})

# Single-token journal names that Docling lifts as headings on the
# cover page. Lowercased for comparison.
_JOURNAL_NAME_HEADINGS = frozenset({
    "nanoscale",
    "nano energy",
    "applied surface science",
    "advanced functional materials",
    "advanced materials",
    "advanced electronic materials",
    "nature",
    "nature communications",
    "nature electronics",
    "science",
    "iscience",
    "acs nano",
    "acs applied electronic materials",
    "acs applied materials & interfaces",
    "ieee transactions on electron devices",
    "frontiers in",
    "nanotechnology",
    "small",
    "paper",
    "article",
    "full length article",
    "research article",
    "review article",
    "letters",
})

_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DOI_RE = re.compile(r"^(?:doi[: ]|10\.\d{4,}/)", re.IGNORECASE)


def _is_boilerplate_heading(title: str) -> bool:
    """Return True when a heading is publisher boilerplate rather than
    a real document section. Compared after whitespace collapse and
    casefold; matches the literal sets above plus URL / DOI heads.
    """
    if not title:
        return True
    norm = re.sub(r"\s+", " ", title).strip(" *:#-").lower()
    if not norm:
        return True
    if norm in _BOILERPLATE_HEADINGS:
        return True
    if norm in _JOURNAL_NAME_HEADINGS:
        return True
    if _HTTP_URL_RE.match(norm) or _DOI_RE.match(norm):
        return True
    # Pure separator / decoration: e.g. "---" or single non-word char.
    if not re.search(r"[a-zA-Z]", norm):
        return True
    return False


def section_spans(body: str) -> list[tuple[list[str], int, int]]:
    matches = list(_H_RE.finditer(body))
    if not matches:
        return [(["body"], 0, len(body))]
    spans: list[tuple[list[str], int, int]] = []
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        # Drop publisher boilerplate from the section hierarchy. The
        # span still gets emitted under the enclosing real section so
        # the boilerplate's body text isn't lost — it just doesn't
        # spawn its own section_path entry.
        if _is_boilerplate_heading(title):
            if stack:
                spans.append(([t for _, t in stack], start, end))
            else:
                spans.append((["body"], start, end))
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = [t for _, t in stack]
        spans.append((path, start, end))
    if matches[0].start() > 0:
        spans.insert(0, (["preamble"], 0, matches[0].start()))
    return spans


def _clean_toc_title(title: str) -> str:
    """Strip BOMs, zero-width chars, and exotic whitespace from a TOC title.

    PDF outline entries from acrobat-distilled docs frequently carry
    runs of ``U+FEFF`` (zero-width no-break space) and ``U+2002`` (en
    space) interleaved with the displayed characters. Without this we
    end up with section paths like ``"\ufeff\ufeff\ufeff1.\u2002\ufeff
    Introduction"`` and downstream display + matching is broken.
    """
    if not title:
        return ""
    # Drop format / control / zero-width characters.
    cleaned = "".join(c for c in title if c not in _TOC_NOISE_CHARS)
    # Replace exotic whitespace (en space, em space, NBSP, etc) with ASCII space.
    cleaned = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalise_for_match(s: str) -> str:
    """Lowercase + collapse non-alnum for fuzzy substring matching.

    A TOC title like ``"4.1.1. 128x64 Array Development"`` should match a
    body fragment like ``"4.1.1. 128x64 Array Development."`` ignoring
    trailing punctuation, case, and inserted whitespace. We strip
    everything that isn't alphanumeric and lowercase.
    """
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _find_toc_title(body: str, title: str, search_from: int) -> int:
    """Locate ``title`` in ``body`` starting at ``search_from``.

    First tries an exact case-insensitive substring search. Falls back
    to a normalised match scanning a sliding window — necessary when the
    PDF TOC title differs from the rendered text by punctuation or
    whitespace (extremely common in pymupdf4llm output).
    Returns -1 if no plausible match is found.
    """
    if not title:
        return -1
    # Direct case-insensitive search.
    idx = body.lower().find(title.lower(), search_from)
    if idx >= 0:
        return idx

    # Normalised fallback: walk every line break in the search region
    # and compare its first ~60 normalised chars against the title.
    target = _normalise_for_match(title)
    if len(target) < 4:
        return -1
    # Look only at line starts to avoid mid-paragraph false positives.
    for m in re.finditer(r"^[^\n]{0,150}", body[search_from:], re.MULTILINE):
        line_start = search_from + m.start()
        line = m.group(0)
        if not line.strip():
            continue
        norm_line = _normalise_for_match(line)
        if norm_line.startswith(target):
            return line_start
    return -1


def toc_spans(
    body: str,
    toc_entries: list[tuple[int, str, int]],
    *,
    min_matches: int = 3,
) -> list[tuple[list[str], int, int]] | None:
    """Build section spans from a PDF TOC instead of markdown headings.

    ``toc_entries`` is a fitz-style TOC: ``[(level, title, page), ...]``.
    Returns ``None`` when fewer than ``min_matches`` TOC titles can be
    located in ``body`` — the caller should fall back to
    ``section_spans``. Otherwise returns the same shape as
    ``section_spans``: a list of ``(heading_path, start, end)`` tuples
    with a heading stack so nested levels accumulate into the path.

    Section ends are computed by walking pairs: each section runs from
    its title's position to the next title's position. Skipped entries
    (titles we couldn't locate) inherit their predecessor's range.
    """
    if not toc_entries or not body:
        return None

    located: list[tuple[int, int, str, int]] = []  # (offset, level, title, ord)
    cursor = 0
    for ord_, (level, raw_title, _page) in enumerate(toc_entries):
        title = _clean_toc_title(raw_title)
        if not title:
            continue
        offset = _find_toc_title(body, title, cursor)
        if offset >= 0:
            located.append((offset, level, title, ord_))
            cursor = offset + len(title)
    if len(located) < min_matches:
        return None

    located.sort(key=lambda t: t[0])
    spans: list[tuple[list[str], int, int]] = []
    stack: list[tuple[int, str]] = []
    if located[0][0] > 0:
        spans.append((["preamble"], 0, located[0][0]))
    for i, (offset, level, title, _ord) in enumerate(located):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        start = offset + len(title)
        end = located[i + 1][0] if i + 1 < len(located) else len(body)
        path = [t for _, t in stack]
        spans.append((path, start, end))
    return spans
