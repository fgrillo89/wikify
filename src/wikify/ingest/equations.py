"""Extract mathematical, chemical, and named equations from markdown.

Emits plain ``dict`` records (serialised alongside ``Document.citations``).
Chunk binding happens outside this module -- the pipeline walks each
chunk's ``char_span`` once and attaches any equation ids whose source
offset falls inside it. This keeps the extractor a pure function of
markdown text.

Returned records:
    {
        "id":          str,            # sha1 of normalised latex
        "latex":       str,            # equation body (no delimiters)
        "type":        "display" | "inline" | "chemical" | "named" | "unicode" | "image",
        "label":       str | None,     # "(1)", "(2a)", "Eq. 4" if nearby
        "context":     str,            # sentence before + sentence after
        "char_offset": int,            # byte offset into the source markdown
    }

The extractor is intentionally tolerant: false positives are cheap (the
handler can ignore them) while false negatives mean the model never sees
the equation at all.
"""

import hashlib
import re

__all__ = ["extract_equations"]


# -- display math ---------------------------------------------------------

_DISPLAY_MATH_PATTERNS = [
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(.+?)\\\]", re.DOTALL),
    re.compile(r"\\begin\{equation\*?\}(.+?)\\end\{equation\*?\}", re.DOTALL),
    re.compile(r"\\begin\{align\*?\}(.+?)\\end\{align\*?\}", re.DOTALL),
]

# Inline $...$ excluding currency ($100, $5.00). Must not be preceded or
# followed by another dollar (which would indicate display math), and the
# first char after the opening $ must not be a digit.
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(?!\d)([^\$]+?)(?<!\$)\$(?!\$)")


# -- chemical equations ---------------------------------------------------

_CHEM_EQUATION_RE = re.compile(
    r"([A-Z][a-z]?\d*(?:\([^)]+\)\d*)*"
    r"(?:\s*\+\s*[A-Z][a-z]?\d*(?:\([^)]+\)\d*)*)*)"
    r"\s*(?:->|-->|\u2192|\u27f6|\\to|\\rightarrow)\s*"
    r"([A-Z][a-z]?\d*(?:\([^)]+\)\d*)*"
    r"(?:\s*\+\s*[A-Z][a-z]?\d*(?:\([^)]+\)\d*)*)*)"
)


# -- equation labels ------------------------------------------------------

_EQUATION_LABEL_RE = re.compile(
    r"(?:\((\d+[a-z]?)\)|(?:Eq(?:uation)?\.?\s*(\d+[a-z]?)))"
)


# -- Unicode / plain-text math --------------------------------------------

_MATH_TOKEN = (
    r"[A-Za-z\u0394\u03b1-\u03c9\u0391-\u03a9]"
    r"[A-Za-z0-9_()\u0394\u03b1-\u03c9\u0391-\u03a9"
    r"\u2080-\u2089\u00b2\u00b3\u207f]*"
)
_MATH_RHS_TOKEN = (
    r"[A-Za-z0-9_()\u0394\u03b1-\u03c9\u0391-\u03a9"
    r"\u2080-\u2089\u00b2\u00b3\u207f^]+"
)
_MATH_OP = r"[+\-*/\u00b7\u22c5]"

_UNICODE_EQUATION_RE = re.compile(
    r"(?:^|(?<=\s))"
    r"("
    + _MATH_TOKEN
    + r"\s*[=<>\u2264\u2265\u2248\u221d\u00b1]+\s*"
    + _MATH_RHS_TOKEN
    + r"(?:\s*"
    + _MATH_OP
    + r"\s*"
    + _MATH_RHS_TOKEN
    + r")*"
    + r")"
    r"(?=\s|$|[,;:!?.])",
    re.MULTILINE,
)

_PROSE_STOPWORDS = frozenset(
    {
        "is", "are", "was", "were", "the", "and", "that", "this", "which",
        "with", "from", "for", "not", "but", "its", "has", "have", "had",
        "been", "will", "can", "may", "also", "than", "then", "such",
        "each", "into", "over", "both", "only", "very", "when", "where",
        "while", "about", "after", "before", "other", "between", "through",
        "during", "without", "however", "because", "although", "therefore",
        "important",
    }
)


# -- picture-omitted placeholders (pymupdf4llm) ----------------------------

_PICTURE_OMITTED_RE = re.compile(
    r"\*?\*?=+>.*?(?:picture|image).*?(?:omitted|removed).*?<+=+\*?\*?",
    re.IGNORECASE,
)
_EQUATION_CONTEXT_RE = re.compile(
    r"(?:where|equation|given\s+by|defined\s+as|expressed\s+as|formula|"
    r"can\s+be\s+written|is\s+described\s+by|according\s+to|"
    r"Eq\.|Equation|relation)",
    re.IGNORECASE,
)


# -- named equations ------------------------------------------------------

_NAMED_EQUATION_RE = re.compile(
    r"(?:the\s+)?"
    r"([A-Z][a-z]{2,}(?:['\u2019]s)?)"
    r"\s+"
    r"(?:(?:first|second|third|fourth|zeroth)\s+)?"
    r"(law|equation|relation|formula|rule|principle|theorem)",
)

_NAMED_EQUATION_EXCLUDE = frozenset(
    {
        "the", "this", "that", "each", "our", "their", "its", "any", "new",
        "general", "simple", "basic", "above", "following", "resulting",
        "governing",
    }
)


# -- helpers --------------------------------------------------------------


def _equation_id(latex: str) -> str:
    normalised = re.sub(r"\s+", " ", latex.strip())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:12]


def _extract_context(md: str, start: int, end: int) -> str:
    """Return the sentence before and after the match, joined with ``[...]``.

    Capped at 500 chars to keep the emitted record small.
    """
    before_text = md[:start]
    sentence_ends = list(re.finditer(r"\.\s+", before_text))
    if len(sentence_ends) >= 2:
        prev_end = sentence_ends[-2].end()
        before_sentence = before_text[prev_end:].strip()
    elif sentence_ends:
        before_sentence = before_text[sentence_ends[-1].end() :].strip()
    else:
        before_sentence = before_text[-200:].strip()

    after_text = md[end:]
    after_match = re.search(r"[.!?]\s+", after_text)
    if after_match:
        after_sentence = after_text[: after_match.end()].strip()
    else:
        after_sentence = after_text[:200].strip()

    ctx = f"{before_sentence} [...] {after_sentence}".strip()
    return ctx[:500]


def _find_label_near(md: str, start: int, end: int) -> str | None:
    search_region = md[max(0, start - 50) : end + 100]
    m = _EQUATION_LABEL_RE.search(search_region)
    if m:
        return m.group(1) or m.group(2)
    return None


def _is_plausible_equation(text: str) -> bool:
    """Reject prose that happens to have an equals sign.

    Real equations use short variable names and have at least one
    mathy glyph. Prose lines dominate the false-positive pool in
    pymupdf4llm output, so the filter errs on the strict side.
    """
    if not re.search(r"[=<>\u2264\u2265\u2248\u221d\u00b1]", text):
        return False
    parts = re.split(r"\s*[=<>\u2264\u2265\u2248\u221d\u00b1]+\s*", text, maxsplit=1)
    if len(parts) < 2:
        return False
    lhs, rhs = parts[0].strip(), parts[1].strip()
    if not lhs or not rhs:
        return False
    rhs_words = re.findall(r"[A-Za-z]+", rhs.lower())
    if rhs_words and all(w in _PROSE_STOPWORDS for w in rhs_words):
        return False
    rhs_long_words = [w for w in rhs_words if len(w) >= 4]
    if len(rhs_long_words) >= 3:
        return False
    lhs_words = re.findall(r"[A-Za-z]+", lhs.lower())
    if lhs_words and all(w in _PROSE_STOPWORDS for w in lhs_words):
        return False
    lhs_long_words = [w for w in lhs_words if len(w) >= 4]
    if len(lhs_long_words) >= 3:
        return False
    has_var = any(len(w) <= 3 for w in re.findall(r"[A-Za-z]+", lhs + rhs))
    has_math_char = bool(
        re.search(r"[\d()\u0394\u03b1-\u03c9\u0391-\u03a9\u2080-\u2089\u00b2\u00b3^_/]", text)
    )
    return has_var or has_math_char


# Tokens that indicate something is plausibly an equation rather than a
# stray bracketed bibliography number. ``\\`` covers all LaTeX commands;
# the rest are explicit math operators or relational symbols.
_EQ_REAL_RE = re.compile(
    r"\\|=|\^|_|\+|\-|\*|/|\\frac|\\sqrt|"
    r"\u2264|\u2265|\u2248|\u221d|\u00b1|\u2192|\u00b7|\u22c5|"
    r"[a-zA-Z][a-zA-Z]"  # at least 2 alphabetic chars in a row (var name / func)
)


def _is_real_equation(latex: str, *, kind: str) -> bool:
    """Drop obvious junk that the markdown regexes pick up.

    The display-math regex (``$$...$$``) silently swallows bibliography
    numbers ("$$10$$"), bare punctuation, and other Marker artifacts.
    Apply the same minimum-content filter we already use for unicode
    math to *every* extraction kind so the equations.json index
    isn't dominated by single-character / pure-digit records.

    Named equations are exempt because they're matched by phrase
    ("Ohm's law"), not by math syntax. Image-equation placeholders
    are exempt because their ``latex`` field is the literal string
    "[image equation]".
    """
    if kind in {"named", "image"}:
        return True
    s = (latex or "").strip()
    if len(s) < 3:
        return False
    if s.isdigit():
        return False
    # Pure punctuation: reject.
    if not re.search(r"[A-Za-z0-9]", s):
        return False
    # Need at least one math signal: a LaTeX command, a math operator,
    # a relational symbol, or a multi-letter variable / function token.
    if not _EQ_REAL_RE.search(s):
        return False
    return True


# -- main extractor -------------------------------------------------------


def extract_equations(md_text: str) -> list[dict]:
    """Extract mathematical and chemical equations from markdown text.

    Returns a list of dicts, deduplicated by ``id``. Order is:
    display math → inline math → chemical → unicode math →
    image-equation placeholders → named equations. ``char_offset`` is
    the byte offset of the first character of the match in ``md_text``,
    which the caller uses to bind equations to chunks.
    """
    if not md_text:
        return []

    out: list[dict] = []
    seen_ids: set[str] = set()

    def _push(latex: str, kind: str, start: int, end: int, label: str | None = None) -> None:
        latex = latex.strip()
        if not latex:
            return
        if not _is_real_equation(latex, kind=kind):
            return
        eq_id = _equation_id(f"{kind}:{latex}")
        if eq_id in seen_ids:
            return
        seen_ids.add(eq_id)
        out.append(
            {
                "id": eq_id,
                "latex": latex[:500],
                "type": kind,
                "label": label,
                "context": _extract_context(md_text, start, end),
                "char_offset": start,
            }
        )

    # 1. Display math
    for pattern in _DISPLAY_MATH_PATTERNS:
        for m in pattern.finditer(md_text):
            label = _find_label_near(md_text, m.start(), m.end())
            _push(m.group(1), "display", m.start(), m.end(), label)

    # 2. Inline math
    for m in _INLINE_MATH_RE.finditer(md_text):
        _push(m.group(1), "inline", m.start(), m.end())

    # 3. Chemical equations (arrow-separated reactants→products)
    for m in _CHEM_EQUATION_RE.finditer(md_text):
        label = _find_label_near(md_text, m.start(), m.end())
        _push(m.group(0), "chemical", m.start(), m.end(), label)

    # 4. Unicode / plain-text math without LaTeX delimiters
    for m in _UNICODE_EQUATION_RE.finditer(md_text):
        text = m.group(1).strip().rstrip(".")
        if len(text) < 3 or not _is_plausible_equation(text):
            continue
        _push(text, "unicode", m.start(), m.end())

    # 5. Picture-omitted placeholders near equation-context prose
    for m in _PICTURE_OMITTED_RE.finditer(md_text):
        region_start = max(0, m.start() - 300)
        region_end = min(len(md_text), m.end() + 300)
        if not _EQUATION_CONTEXT_RE.search(md_text[region_start:region_end]):
            continue
        _push("[image equation]", "image", m.start(), m.end(),
              _find_label_near(md_text, m.start(), m.end()))

    # 6. Named equations (e.g. "Fick's second law", "Ohm's law")
    seen_names: set[str] = set()
    for m in _NAMED_EQUATION_RE.finditer(md_text):
        name_part = m.group(1).rstrip("'s\u2019").lower()
        if name_part in _NAMED_EQUATION_EXCLUDE:
            continue
        full = m.group(0).strip()
        name_key = full.lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        _push(full, "named", m.start(), m.end())

    # Stable ordering by source offset so downstream display is predictable.
    out.sort(key=lambda e: e["char_offset"])
    return out
