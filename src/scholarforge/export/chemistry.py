"""Chemical formula detection and formatting.

Detects chemical formulas in text and formats subscript numbers for
different output targets (Markdown HTML, DOCX, LaTeX).
"""

from __future__ import annotations

import re

# Common chemical element symbols (1- and 2-char).  Used to anchor the
# formula regex so that words like "Figure2" or "Step3" are not treated
# as formulas.
_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb",
    "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Hf", "Ta", "W",
    "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi",
}  # fmt: skip

# Matches a chemical formula: one or more (Element + optional digit) groups.
# Examples: HfO2, TiO2, Al2O3, Si3N4, Fe2O3, SiNx, HfOx, TiN
# A formula must start with an element symbol and contain at least one digit
# to be formatted (otherwise "Si" alone is not touched).
_FORMULA_RE = re.compile(
    r"\b("
    r"(?:[A-Z][a-z]?)(?:\d+)?"  # first element+optional count
    r"(?:[A-Z][a-z]?(?:\d+)?)*"  # additional element+optional count groups
    r")\b"
)

# Words that look like formulas but aren't.
_FALSE_POSITIVES = {
    "Fig",
    "Figure",
    "Table",
    "Eq",
    "Ref",
    "Step",
    "Phase",
    "Section",
    "Chapter",
    "Vol",
    "Part",
    "No",
}


def _is_chemical_formula(token: str) -> bool:
    """Check if a token is a valid chemical formula (not a false positive)."""
    if token in _FALSE_POSITIVES:
        return False
    if len(token) < 2:
        return False
    # Must contain at least one digit (otherwise it's just element symbols)
    if not any(c.isdigit() for c in token):
        return False
    # Must start with a known element symbol
    if token[:2] in _ELEMENTS or token[:1] in _ELEMENTS:
        # Verify all alphabetic parts are valid element symbols
        parts = re.findall(r"[A-Z][a-z]?", token)
        return all(p in _ELEMENTS or p.rstrip("x") in _ELEMENTS for p in parts)
    return False


def format_formulas_markdown(text: str) -> str:
    """Format chemical formulas with HTML subscript tags for Markdown.

    HfO2 -> HfO<sub>2</sub>, Al2O3 -> Al<sub>2</sub>O<sub>3</sub>
    """

    def _sub_digits(match: re.Match) -> str:
        token = match.group(1)
        if not _is_chemical_formula(token):
            return token
        # Subscript all digits that follow element symbols
        return re.sub(r"(\d+)", r"<sub>\1</sub>", token)

    return _FORMULA_RE.sub(_sub_digits, text)


def format_formulas_unicode(text: str) -> str:
    """Format chemical formulas with Unicode subscript digits.

    HfO2 -> HfO₂, Al2O3 -> Al₂O₃
    """
    subscript_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

    def _sub_digits(match: re.Match) -> str:
        token = match.group(1)
        if not _is_chemical_formula(token):
            return token
        result = []
        for char in token:
            if char.isdigit():
                result.append(char.translate(subscript_map))
            else:
                result.append(char)
        return "".join(result)

    return _FORMULA_RE.sub(_sub_digits, text)


def split_formula_runs(token: str) -> list[tuple[str, bool]]:
    """Split a chemical formula into (text, is_subscript) runs for DOCX rendering.

    Example: "Al2O3" -> [("Al", False), ("2", True), ("O", False), ("3", True)]
    Returns the token unchanged (as single non-subscript run) if not a formula.
    """
    if not _is_chemical_formula(token):
        return [(token, False)]

    runs: list[tuple[str, bool]] = []
    current = ""
    in_digit = False

    for char in token:
        is_digit = char.isdigit()
        if is_digit != in_digit and current:
            runs.append((current, in_digit))
            current = ""
        current += char
        in_digit = is_digit

    if current:
        runs.append((current, in_digit))

    return runs
