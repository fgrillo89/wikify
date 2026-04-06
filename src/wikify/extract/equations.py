"""Extract mathematical and chemical equations from markdown text."""

from __future__ import annotations

import hashlib
import json
import re

from wikify.store.models import Chunk, Equation

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


def extract_equations(md_text: str, paper_id: str, chunks: list[Chunk]) -> list[Equation]:
    """Extract mathematical and chemical equations from markdown text.

    Detection patterns:
    - Display math: $$...$$ , \\[...\\] , \\begin{equation}...\\end{equation}
    - Inline math: $...$ (single dollar, excludes currency patterns)
    - Chemical equations: A + B -> C patterns
    - Numbered equations: (1), Eq. 1, Equation 1

    For each equation:
    - Extract the LaTeX content
    - Classify as mathematical | chemical | inline
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

    return equations
