"""Caption-first figure / table / scheme reference extractor.

Ported from ``archive/wikify/ingest/extract/figure_refs.py``. Scans
markdown body text for inline caption patterns like
``Fig. 1. Schematic of …`` or ``Table 2. Summary of measured values …``
and emits one record per unique key. The result is the *body-side*
catalogue of figures: it covers cases where the binary figure extractor
missed an image (scanned PDFs, complex layouts) but the caption is
still in the prose, AND it gives every reference a section anchor so
the distill sampler can dispatch a chunk knowing which figure it is
about.

Returned records are plain dicts:

    {
        "key":          "Fig. 1",
        "kind":         "figure" | "table" | "scheme",
        "num":          1,
        "sub":          "" | "a" | "b" | …,
        "caption":      "Schematic of the device …" (max 500 chars),
        "section_path": ["II. FABRICATION", "B. Patterning"],
        "char_offset":  int,  # byte position in the source markdown
    }

The keying is deduplicated within a paper: only the first occurrence of
each ``(kind, num, sub)`` triple wins (subsequent mentions of the same
figure are body references, not new captions).
"""

import re

__all__ = ["extract_figure_refs"]


# Inline caption matchers. We allow optional bold wrapping (``**Fig. 1.**``)
# and a wider variety of separators (em-dash, en-dash, period, colon).
_FIG_CAPTION_RE = re.compile(
    r"(?im)^\s*\*{0,2}"
    r"(?P<key>Fig(?:ure)?\.?\s*(?P<num>\d+)(?P<sub>[a-z])?)"
    r"\*{0,2}"
    r"\s*[.:\-—\u2014]+\s*"
    r"(?P<caption>.+)"
)
_TABLE_CAPTION_RE = re.compile(
    r"(?im)^\s*\*{0,2}"
    r"(?P<key>Table\.?\s*(?P<num>\d+)(?P<sub>[a-z])?)"
    r"\*{0,2}"
    r"\s*[.:\-—\u2014]+\s*"
    r"(?P<caption>.+)"
)
_SCHEME_CAPTION_RE = re.compile(
    r"(?im)^\s*\*{0,2}"
    r"(?P<key>Scheme\.?\s*(?P<num>\d+)(?P<sub>[a-z])?)"
    r"\*{0,2}"
    r"\s*[.:\-—\u2014]+\s*"
    r"(?P<caption>.+)"
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _section_path_at(md_text: str, offset: int) -> list[str]:
    """Return the heading stack covering ``offset`` in ``md_text``.

    Walks the document from the start, maintaining a heading-level stack
    so we can answer "which section does this character offset live in"
    in O(headings before offset). Used to anchor each figure caption to
    a section path mirroring the chunker's own section_path field.
    """
    stack: list[tuple[int, str]] = []
    for line_match in re.finditer(r"^.*$", md_text[:offset], re.MULTILINE):
        line = line_match.group(0)
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    if not stack:
        return ["body"]
    return [t for _, t in stack]


def extract_figure_refs(md_text: str) -> list[dict]:
    """Extract figure / table / scheme caption records from markdown.

    Returns a list of dicts deduplicated on ``(kind, num, sub)``. The
    dispatch is line-based — captions almost always live on their own
    line in pymupdf4llm output — but the regex also catches inline
    bold-wrapped captions like ``**Fig. 1.** caption text``.
    """
    if not md_text:
        return []

    out: list[dict] = []
    seen: set[tuple[str, int, str]] = set()

    patterns = [
        ("figure", _FIG_CAPTION_RE),
        ("table", _TABLE_CAPTION_RE),
        ("scheme", _SCHEME_CAPTION_RE),
    ]

    for kind, pattern in patterns:
        for m in pattern.finditer(md_text):
            num = int(m.group("num"))
            sub = (m.group("sub") or "").lower()
            key_triple = (kind, num, sub)
            if key_triple in seen:
                continue
            seen.add(key_triple)

            caption = m.group("caption").strip()
            # Strip trailing markdown emphasis closers and excess whitespace.
            caption = re.sub(r"\s+", " ", caption).strip().rstrip("*_ ")
            if not caption:
                continue

            out.append(
                {
                    "key": m.group("key").strip().rstrip("."),
                    "kind": kind,
                    "num": num,
                    "sub": sub,
                    "caption": caption[:500],
                    "section_path": _section_path_at(md_text, m.start()),
                    "char_offset": m.start(),
                }
            )

    out.sort(key=lambda r: r["char_offset"])
    return out
