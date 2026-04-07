"""Extract mathematical and chemical equations from markdown text."""

from __future__ import annotations

import hashlib
import json
import re

from wikify.core.store.models import Chunk, Equation

# ── Display math patterns ──────────────────────────────────────────────────

DISPLAY_MATH_PATTERNS = [
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(.+?)\\\]", re.DOTALL),
    re.compile(r"\\begin\{equation\*?\}(.+?)\\end\{equation\*?\}", re.DOTALL),
    re.compile(r"\\begin\{align\*?\}(.+?)\\end\{align\*?\}", re.DOTALL),
]

# Inline math: $...$ but NOT currency like $100, $5.00
# Require non-digit after opening $ and non-whitespace content
INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(?!\d)([^\$]+?)(?<!\$)\$(?!\$)")

# Chemical equation patterns: reactants -> products
CHEM_EQUATION_RE = re.compile(
    r"([A-Z][a-z]?\d*(?:\([^)]+\)\d*)*"
    r"(?:\s*\+\s*[A-Z][a-z]?\d*(?:\([^)]+\)\d*)*)*)"
    r"\s*(?:->|-->|\u2192|\u27f6|\\to|\\rightarrow)\s*"
    r"([A-Z][a-z]?\d*(?:\([^)]+\)\d*)*"
    r"(?:\s*\+\s*[A-Z][a-z]?\d*(?:\([^)]+\)\d*)*)*)"
)

# Equation label patterns: (1), (2a), Eq. 1, Equation 1
EQUATION_LABEL_RE = re.compile(r"(?:\((\d+[a-z]?)\)|(?:Eq(?:uation)?\.?\s*(\d+[a-z]?)))")

# ── Unicode math (no LaTeX delimiters) ────────────────────────────────────
# Catches plain-text equations from pymupdf4llm output like:
#   v(t) = M(q)i(t),  I = V/R,  R_ON/R_OFF > 10^6,  sigma = nqu

# A "math token" is a variable-like piece: short identifier, digit, operator, Greek, etc.
# We allow spaces between math tokens but NOT before lowercase words 4+ chars long.
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

UNICODE_EQUATION_RE = re.compile(
    r"(?:^|(?<=\s))"  # start of line or after space
    r"("
    + _MATH_TOKEN
    + r"\s*[=<>\u2264\u2265\u2248\u221d\u00b1]+\s*"  # operator
    + _MATH_RHS_TOKEN  # first RHS token (required)
    + r"(?:\s*"
    + _MATH_OP
    + r"\s*"
    + _MATH_RHS_TOKEN
    + r")*"  # additional operator+token pairs
    + r")"
    r"(?=\s|$|[,;:!?.])",  # end boundary
    re.MULTILINE,
)

# Words that look like equations but are just prose — used to filter false positives
_PROSE_STOPWORDS = frozenset(
    {
        "is",
        "are",
        "was",
        "were",
        "the",
        "and",
        "that",
        "this",
        "which",
        "with",
        "from",
        "for",
        "not",
        "but",
        "its",
        "has",
        "have",
        "had",
        "been",
        "will",
        "can",
        "may",
        "also",
        "than",
        "then",
        "such",
        "each",
        "into",
        "over",
        "both",
        "only",
        "very",
        "when",
        "where",
        "while",
        "about",
        "after",
        "before",
        "other",
        "between",
        "through",
        "during",
        "without",
        "however",
        "because",
        "although",
        "therefore",
        "important",
    }
)

# ── Picture-omitted placeholder ───────────────────────────────────────────
# pymupdf4llm emits this when it cannot render an image/equation

PICTURE_OMITTED_RE = re.compile(
    r"\*?\*?=+>.*?(?:picture|image).*?(?:omitted|removed).*?<+=+\*?\*?",
    re.IGNORECASE,
)

# Context phrases that suggest a nearby picture-omitted is an equation
_EQUATION_CONTEXT_RE = re.compile(
    r"(?:where|equation|given\s+by|defined\s+as|expressed\s+as|formula|"
    r"can\s+be\s+written|is\s+described\s+by|according\s+to|"
    r"Eq\.|Equation|relation)",
    re.IGNORECASE,
)

# ── Named equation patterns ──────────────────────────────────────────────
# "Fick's second law", "the Arrhenius equation", "Ohm's law"

NAMED_EQUATION_RE = re.compile(
    r"(?:the\s+)?"
    r"([A-Z][a-z]{2,}(?:['\u2019]s)?)"  # Name: 3+ letters (excludes "We", "He", etc.)
    r"\s+"
    r"(?:(?:first|second|third|fourth|zeroth)\s+)?"
    r"(law|equation|relation|formula|rule|principle|theorem)",
    # Note: "model" excluded — too many false positives ("We model", "This model")
)

# Common words that look like proper names but aren't equation names
_NAMED_EQUATION_EXCLUDE = frozenset(
    {
        "the",
        "this",
        "that",
        "each",
        "our",
        "their",
        "its",
        "any",
        "new",
        "general",
        "simple",
        "basic",
        "above",
        "following",
        "resulting",
        "governing",
    }
)

# Variable extraction: single Latin letters and common Greek letters in LaTeX
_GREEK_NAMES = (
    "alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|"
    "mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|"
    "Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega"
)
GREEK_VAR_RE = re.compile(rf"\\({_GREEK_NAMES})(?![a-zA-Z])")

# Matches \command sequences (to strip them before extracting Latin vars)
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")

# Single Latin letter (applied after stripping commands)
_LATIN_VAR_RE = re.compile(r"[a-zA-Z]")

# LaTeX commands to exclude from variable extraction
_LATEX_COMMANDS = frozenset(
    {
        "frac",
        "sqrt",
        "sum",
        "prod",
        "int",
        "lim",
        "log",
        "ln",
        "sin",
        "cos",
        "tan",
        "exp",
        "max",
        "min",
        "sup",
        "inf",
        "det",
        "dim",
        "text",
        "mathrm",
        "mathbf",
        "mathit",
        "mathcal",
        "left",
        "right",
        "begin",
        "end",
        "cdot",
        "times",
        "div",
        "pm",
        "mp",
        "leq",
        "geq",
        "neq",
        "approx",
        "equiv",
        "partial",
        "nabla",
        "infty",
        "to",
        "rightarrow",
        "leftarrow",
        "quad",
        "qquad",
        "hbar",
        "over",
    }
)


def _equation_id(latex: str) -> str:
    """Generate a deterministic ID from normalized LaTeX."""
    normalized = re.sub(r"\s+", " ", latex.strip())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _extract_context(full_text: str, match_start: int, match_end: int) -> str:
    """Extract 1 sentence before and 1 sentence after the equation.

    Uses a simple sentence boundary heuristic (period + space + uppercase).
    """
    # Find sentence before: search backwards from match_start for a period
    before_text = full_text[:match_start]
    # Find last sentence boundary before the equation
    sentence_ends = list(re.finditer(r"\.\s+", before_text))
    if len(sentence_ends) >= 2:
        prev_end = sentence_ends[-2].end()
        before_sentence = before_text[prev_end:].strip()
    elif sentence_ends:
        before_sentence = before_text[sentence_ends[-1].end() :].strip()
    else:
        # Take last 200 chars
        before_sentence = before_text[-200:].strip()

    # Find sentence after: search forwards from match_end
    after_text = full_text[match_end:]
    sentence_match = re.search(r"[.!?]\s+", after_text)
    if sentence_match:
        after_sentence = after_text[: sentence_match.end()].strip()
    else:
        after_sentence = after_text[:200].strip()

    context = f"{before_sentence} [...] {after_sentence}".strip()
    return context[:500]  # cap at 500 chars


def _find_label_near(full_text: str, match_start: int, match_end: int) -> str | None:
    """Find an equation label near the equation (within ~100 chars)."""
    search_region = full_text[max(0, match_start - 50) : match_end + 100]
    label_match = EQUATION_LABEL_RE.search(search_region)
    if label_match:
        return label_match.group(1) or label_match.group(2)
    return None


def _extract_variables(latex: str) -> list[str]:
    """Extract variable names from a LaTeX equation string."""
    variables: list[str] = []
    seen: set[str] = set()

    # 1. Extract Greek letter variables (before stripping commands)
    for m in GREEK_VAR_RE.finditer(latex):
        var = f"\\{m.group(1)}"
        if var not in seen:
            seen.add(var)
            variables.append(var)

    # 2. Strip all \command sequences, then extract remaining single letters
    stripped = _LATEX_CMD_RE.sub(" ", latex)
    for m in _LATIN_VAR_RE.finditer(stripped):
        letter = m.group(0)
        if letter not in seen:
            seen.add(letter)
            variables.append(letter)

    return variables


def _find_chunk_for_position(
    md_text: str,
    eq_latex: str,
    match_start: int,
    chunks: list[Chunk],
) -> tuple[str, str]:
    """Find which chunk contains the equation. Returns (chunk_id, section_path)."""
    for chunk in chunks:
        if eq_latex in chunk.content:
            return chunk.id, chunk.section_path
    # Fallback: find chunk whose content overlaps the equation position
    # Use a snippet of surrounding text
    snippet_start = max(0, match_start - 50)
    snippet_end = min(len(md_text), match_start + 50)
    snippet = md_text[snippet_start:snippet_end]
    for chunk in chunks:
        if snippet[:30] in chunk.content or snippet[-30:] in chunk.content:
            return chunk.id, chunk.section_path
    return "", ""


def _is_plausible_equation(text: str) -> bool:
    """Return True if *text* looks like a real equation, not prose.

    Heuristics:
    - Must contain an operator (=, <, >, etc.)
    - Right-hand side must not be only common English words
    - At least one side should have a "mathy" token (single letter, digit,
      parenthesized expression, Greek letter, subscript, etc.)
    """
    # Must have an operator
    if not re.search(r"[=<>\u2264\u2265\u2248\u221d\u00b1]", text):
        return False

    # Split on operator to get LHS / RHS
    parts = re.split(r"\s*[=<>\u2264\u2265\u2248\u221d\u00b1]+\s*", text, maxsplit=1)
    if len(parts) < 2:
        return False

    lhs, rhs = parts[0].strip(), parts[1].strip()

    # Both sides must be non-empty
    if not lhs or not rhs:
        return False

    # Reject if either side is dominated by long English words (4+ chars)
    # Real equations use short variables (1-3 chars), not prose words
    rhs_words = re.findall(r"[A-Za-z]+", rhs.lower())
    if rhs_words and all(w in _PROSE_STOPWORDS for w in rhs_words):
        return False
    # If RHS has 3+ words that are all 4+ chars, it's almost certainly prose
    rhs_long_words = [w for w in rhs_words if len(w) >= 4]
    if len(rhs_long_words) >= 3:
        return False

    lhs_words = re.findall(r"[A-Za-z]+", lhs.lower())
    if lhs_words and all(w in _PROSE_STOPWORDS for w in lhs_words):
        return False
    lhs_long_words = [w for w in lhs_words if len(w) >= 4]
    if len(lhs_long_words) >= 3:
        return False

    # At least one side should have a short token (1-3 chars) — variable-like
    has_var = any(len(w) <= 3 for w in re.findall(r"[A-Za-z]+", lhs + rhs))
    # Or contains digits, Greek, parenthesized sub-expr, subscripts
    has_math_char = bool(
        re.search(r"[\d()\u0394\u03b1-\u03c9\u0391-\u03a9\u2080-\u2089\u00b2\u00b3^_/]", text)
    )

    return has_var or has_math_char


def _extract_unicode_equations(
    md_text: str,
    paper_id: str,
    chunks: list[Chunk],
    seen_ids: set[str],
) -> list[Equation]:
    """Extract plain-text (Unicode) equations that lack LaTeX delimiters."""
    equations: list[Equation] = []
    for m in UNICODE_EQUATION_RE.finditer(md_text):
        text = m.group(1).strip().rstrip(".")
        if not text or len(text) < 3:
            continue
        if not _is_plausible_equation(text):
            continue

        eq_id = _equation_id(text)
        if eq_id in seen_ids:
            continue
        seen_ids.add(eq_id)

        chunk_id, section_path = _find_chunk_for_position(md_text, text, m.start(), chunks)

        equations.append(
            Equation(
                id=eq_id,
                paper_id=paper_id,
                chunk_id=chunk_id,
                latex=text,
                equation_type="inline",
                context=_extract_context(md_text, m.start(), m.end()),
                label=_find_label_near(md_text, m.start(), m.end()),
                variables=json.dumps([]),
                section_path=section_path,
            )
        )
    return equations


def _extract_image_equations(
    md_text: str,
    paper_id: str,
    chunks: list[Chunk],
    seen_ids: set[str],
) -> list[Equation]:
    """Detect picture-omitted placeholders that likely represent equations."""
    equations: list[Equation] = []
    for m in PICTURE_OMITTED_RE.finditer(md_text):
        # Look at surrounding ~300 chars for equation-context clues
        region_start = max(0, m.start() - 300)
        region_end = min(len(md_text), m.end() + 300)
        region = md_text[region_start:region_end]

        if not _EQUATION_CONTEXT_RE.search(region):
            continue

        placeholder = m.group(0).strip()
        eq_id = _equation_id(f"image@{m.start()}")
        if eq_id in seen_ids:
            continue
        seen_ids.add(eq_id)

        chunk_id, section_path = _find_chunk_for_position(md_text, placeholder, m.start(), chunks)
        label = _find_label_near(md_text, m.start(), m.end())

        equations.append(
            Equation(
                id=eq_id,
                paper_id=paper_id,
                chunk_id=chunk_id,
                latex="[image equation]",
                equation_type="image",
                context=_extract_context(md_text, m.start(), m.end()),
                label=label,
                variables=json.dumps([]),
                section_path=section_path,
            )
        )
    return equations


def _extract_named_equations(
    md_text: str,
    paper_id: str,
    chunks: list[Chunk],
    seen_ids: set[str],
) -> list[Equation]:
    """Detect equations referenced by name (e.g. "Fick's second law")."""
    equations: list[Equation] = []
    seen_names: set[str] = set()
    for m in NAMED_EQUATION_RE.finditer(md_text):
        # Skip if the "name" is a common English word, not a proper noun
        name_part = m.group(1).rstrip("'s\u2019").lower()
        if name_part in _NAMED_EQUATION_EXCLUDE:
            continue

        full = m.group(0).strip()
        name_key = full.lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)

        eq_id = _equation_id(f"named:{name_key}")
        if eq_id in seen_ids:
            continue
        seen_ids.add(eq_id)

        chunk_id, section_path = _find_chunk_for_position(md_text, full, m.start(), chunks)

        equations.append(
            Equation(
                id=eq_id,
                paper_id=paper_id,
                chunk_id=chunk_id,
                latex=full,
                equation_type="named",
                context=_extract_context(md_text, m.start(), m.end()),
                label=None,
                variables=json.dumps([]),
                section_path=section_path,
            )
        )
    return equations


def extract_equations(md_text: str, paper_id: str, chunks: list[Chunk]) -> list[Equation]:
    """Extract mathematical and chemical equations from markdown text.

    Detection patterns:
    - Display math: $$...$$ , \\[...\\] , \\begin{equation}...\\end{equation}
    - Inline math: $...$ (single dollar, excludes currency patterns)
    - Chemical equations: A + B -> C patterns
    - Numbered equations: (1), Eq. 1, Equation 1
    - Unicode math: plain-text equations without LaTeX delimiters (pymupdf4llm output)
    - Image equations: picture-omitted placeholders near equation context
    - Named equations: references like "Fick's second law", "Ohm's law"

    For each equation:
    - Extract the LaTeX content (or placeholder for image equations)
    - Classify as mathematical | chemical | inline | image | named
    - Extract surrounding context (1 sentence before, 1 after)
    - Extract equation label if present
    - Identify variable names
    - Link to the chunk it appears in

    Returns:
        List of Equation model instances.
    """
    equations: list[Equation] = []
    seen_ids: set[str] = set()

    # 1. Display math
    for pattern in DISPLAY_MATH_PATTERNS:
        for m in pattern.finditer(md_text):
            latex = m.group(1).strip()
            if not latex:
                continue
            eq_id = _equation_id(latex)
            if eq_id in seen_ids:
                continue
            seen_ids.add(eq_id)

            chunk_id, section_path = _find_chunk_for_position(md_text, latex, m.start(), chunks)
            label = _find_label_near(md_text, m.start(), m.end())
            variables = _extract_variables(latex)

            equations.append(
                Equation(
                    id=eq_id,
                    paper_id=paper_id,
                    chunk_id=chunk_id,
                    latex=latex,
                    equation_type="mathematical",
                    context=_extract_context(md_text, m.start(), m.end()),
                    label=label,
                    variables=json.dumps(variables),
                    section_path=section_path,
                )
            )

    # 2. Inline math
    for m in INLINE_MATH_RE.finditer(md_text):
        latex = m.group(1).strip()
        if not latex:
            continue
        # Skip if this position was already captured as display math
        eq_id = _equation_id(latex)
        if eq_id in seen_ids:
            continue
        seen_ids.add(eq_id)

        chunk_id, section_path = _find_chunk_for_position(md_text, latex, m.start(), chunks)
        variables = _extract_variables(latex)

        equations.append(
            Equation(
                id=eq_id,
                paper_id=paper_id,
                chunk_id=chunk_id,
                latex=latex,
                equation_type="inline",
                context=_extract_context(md_text, m.start(), m.end()),
                label=None,
                variables=json.dumps(variables),
                section_path=section_path,
            )
        )

    # 3. Chemical equations
    for m in CHEM_EQUATION_RE.finditer(md_text):
        full_eq = m.group(0).strip()
        if not full_eq:
            continue
        eq_id = _equation_id(full_eq)
        if eq_id in seen_ids:
            continue
        seen_ids.add(eq_id)

        chunk_id, section_path = _find_chunk_for_position(md_text, full_eq, m.start(), chunks)
        label = _find_label_near(md_text, m.start(), m.end())

        equations.append(
            Equation(
                id=eq_id,
                paper_id=paper_id,
                chunk_id=chunk_id,
                latex=full_eq,
                equation_type="chemical",
                context=_extract_context(md_text, m.start(), m.end()),
                label=label,
                variables=json.dumps([]),
                section_path=section_path,
            )
        )

    # 4. Unicode math (plain-text equations without LaTeX delimiters)
    equations.extend(_extract_unicode_equations(md_text, paper_id, chunks, seen_ids))

    # 5. Image equations (picture-omitted placeholders)
    equations.extend(_extract_image_equations(md_text, paper_id, chunks, seen_ids))

    # 6. Named equations (e.g. "Fick's second law")
    equations.extend(_extract_named_equations(md_text, paper_id, chunks, seen_ids))

    return equations
