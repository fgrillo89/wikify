"""Tier-aware mechanical verification of data points.

The single most effective anti-hallucination control for numeric extraction
is a verbatim grounding quote that can be located in the source text. This
module enforces that as a hard gate:

- T1 / T2 (text, table, caption sources): the grounding quote must appear in
  the source text AND carry the reported number. Match tiers: exact substring
  -> whitespace-collapsed -> numeric-token containment. Pass -> ``verified``;
  fail -> ``rejected``.
- T3 (figure plot digitization): there is no verbatim number to locate, so
  the point is flagged ``figure_digitized`` (kept, never silently trusted).

The source text is supplied by the caller (chunk text and/or asset caption),
so this module has no corpus dependency and is trivially testable.
"""

from __future__ import annotations

import re

from ..grounding import is_grounded, normalize_grounding_text
from .models import (
    _BARE_FRACTION_RE,
    _NUMBER_RE,
    DataPoint,
    collapse_number_spacing,
    collapse_spaced_thousands,
    parse_leading_number,
)

# A fraction written inline in prose ("an exponent of 1/2", "3 / 4 of the
# spread"). Matched in the SOURCE text so the quotient it denotes is offered
# as a comparison key alongside its two literal integers.
# The sign is part of the match: ``parse_leading_number`` reads ``-1/2`` as
# -0.5 (unicode minus too), so a source side blind to the sign could never
# offer the matching key - and negative fractional exponents are the canonical
# shape in this literature.
_FRACTION_IN_TEXT_RE = re.compile(
    r"(?<![\d.])([-+−]?)\s*(\d+)\s*/\s*(\d+)(?![\d.])"
)

# Single-number value types — a scalar/bound must reduce to ONE number, so a
# leading run of space-separated bare numbers signals OCR mangling.
_SINGLE_NUMBER_TYPES = frozenset({"scalar", "upper_bound", "lower_bound"})


def _leading_numeric_tokens(value: str) -> list[str]:
    """The leading run of whitespace-separated bare-number tokens."""
    tokens: list[str] = []
    for tok in (value or "").split():
        if _NUMBER_RE.fullmatch(tok):
            tokens.append(tok)
        else:
            break
    return tokens


def _is_grouped_thousands(tokens: list[str]) -> bool:
    """True if *tokens* are a space-separated thousands grouping — a 1-3 digit
    lead followed by all-3-digit groups (``1 000``, ``10 000``, ``1 234 567``),
    the final group optionally carrying a decimal (``1 000.5``). This is a
    legitimate locale form, NOT OCR mangling."""
    if len(tokens) < 2:
        return False
    head = tokens[0].lstrip("+-−")  # drop a sign incl. unicode minus
    if not (head.isdigit() and 1 <= len(head) <= 3):
        return False
    for tok in tokens[1:-1]:
        if not (tok.isdigit() and len(tok) == 3):
            return False
    intpart, _, frac = tokens[-1].partition(".")
    if not (intpart.isdigit() and len(intpart) == 3):
        return False
    return not frac or frac.isdigit()


def is_ocr_mangled_scalar(value: str) -> bool:
    """True when a single-number value begins with 2+ space-separated bare
    numbers that are NOT a thousands grouping — e.g. OCR turning ``1x10^5``
    into ``1 10 5``. The leading-number parse is then unreliable (it keeps the
    first token ``1`` and verifies against any source containing a ``1``), so
    the point cannot be trusted. Genuine locale grouping (``1 000``, ``10 000``)
    is allowed through; unit digits (``cm2``) are not bare numbers and a range
    like ``10 to 20`` breaks the run at ``to``, so neither is flagged."""
    tokens = _leading_numeric_tokens(value)
    if len(tokens) < 2:
        return False
    return not _is_grouped_thousands(tokens)


# A collapsed reading is admitted only for magnitudes a spaced decimal
# plausibly denotes. The shape is genuinely ambiguous — "0 . 549" is a mangled
# coefficient, "2005 . 12" is a sentence boundary — and nothing in the token
# stream separates them. What DOES separate them is scale: OCR-spaced decimals
# in this literature are coefficients and exponents, so their integer part is
# short. A 4-digit integer part is a year, a count, or an identifier, and
# joining it to the next sentence's leading number invents a value that was
# never printed. Bounding the integer part removes that shape. It does NOT
# remove the whole class: a 1-3 digit left-hand number at a sentence or
# section boundary (``Section 4 . 2``) still joins. The union with the literal
# reading keeps the real numbers intact either way, so the residue is a
# narrow over-acceptance, not a lost value.
_SPACED_DECIMAL_MAX_INT_DIGITS = 3


def _plausible_spaced_decimal(token: str) -> bool:
    """Whether a token produced ONLY by the decimal join may be admitted.

    Callers must have already excluded tokens the literal or thousands-grouped
    readings produce; this judges the ambiguous residue alone.
    """
    intpart = token.lstrip("+-−").split(".", 1)[0]
    return len(intpart) <= _SPACED_DECIMAL_MAX_INT_DIGITS


def _numbers(s: str, *, allow_fractions: bool = False) -> set[str]:
    """Numeric tokens in *s* as a set of comparison keys.

    Uses the same exponent-/thousands-aware regex as ``parse_leading_number``
    and reduces each token to its float-normalized form, so ``2.5e-3``,
    ``0.0025``, ``1.10`` and ``1.1`` all collapse to one key and match a
    target parsed the same way.

    Every key added here WIDENS the gate, so each alternate reading has to earn
    its place:

    - The space-collapsed reading is unioned with the literal one rather than
      replacing it, because collapsing is a guess and replacing would erase
      real numbers ("runs to 2005. 12 of the 30 stocks").
    - Prose uses the STRICTER collapse (whitespace required before the period)
      AND admits a collapsed token only when its integer part is short enough
      to be a real coefficient. Both guards are needed: the first declines
      ``2005. 12``, the second declines ``2005 . 12``, and without them a
      fabricated ``2005.12`` verifies against "runs to 2005 . 12 of the 30
      stocks". The knowingly uncovered case is ``0. 549`` — a right-spaced
      decimal, which is indistinguishable from a sentence boundary and is
      therefore not normalized. ``0 . 549`` and ``79 .13`` are.
    - Fraction quotients are added ONLY when the caller says the target is
      itself a bare fraction (``allow_fractions``). Offering ``0.5`` for every
      ``a/b`` in the text would let a claim of 0.5 verify against an unrelated
      ``10/20``.
    """
    raw = s or ""
    out: set[str] = set()

    def _tokens(text: str) -> set[str]:
        return {
            m.group(0).replace("−", "-").replace(",", "").replace(" ", "")
            for m in _NUMBER_RE.finditer(text)
        }

    # Three readings, and only the third is bounded. Thousands grouping is
    # unambiguous even when it carries a fraction (``10 000.5`` -> ``10000.5``,
    # which _SPACED_THOUSANDS_RE joins on purpose), so it must NOT be judged by
    # the decimal bound - doing so rejects ordinary market magnitudes outright.
    # Only tokens that appear solely after the DECIMAL join are the ambiguous
    # ones, and those are bounded by magnitude.
    literal = _tokens(raw)
    grouped = _tokens(collapse_spaced_thousands(raw))
    joined = _tokens(collapse_number_spacing(raw, in_prose=True))
    for tok in literal | grouped:
        out.add(tok)
    for tok in joined - literal - grouped:
        if _plausible_spaced_decimal(tok):
            out.add(tok)

    for cleaned in list(out):
        try:
            val = float(cleaned)
        except ValueError:
            continue
        out.add(repr(val))
        if val == int(val):
            out.add(str(int(val)))
    if allow_fractions:
        # A fraction is how a paper states an exponent ("we set the exponent to
        # 1/2"), and ``parse_leading_number`` reads a bare fraction as its
        # quotient, so the source must offer the same key or the claim could
        # never verify against the text it came from.
        for m in _FRACTION_IN_TEXT_RE.finditer(raw):
            sign, num, den = m.group(1), float(m.group(2)), float(m.group(3))
            if den:
                q = num / den
                out.add(repr(-q if sign in {"-", "−"} else q))
    return out


def quote_in_source(quote: str, source: str) -> bool:
    """True if *quote* appears in *source*, using the grounding normalizer
    shared with the draft validator (exact substring, else whitespace /
    control-char / inline-citation-marker normalized)."""
    return is_grounded(quote, source)


def number_supported(value: str, quote: str, source: str) -> bool:
    """True if the reported number appears in BOTH the quote and the source.

    Floats are compared in normalized form so "1.10" and "1.1" agree. When
    the value carries no number (categorical / qualitative), fall back to
    requiring the value text itself inside the quote.
    """
    target = parse_leading_number(value)
    if target is None:
        if not value:
            return False
        return normalize_grounding_text(value) in normalize_grounding_text(quote)
    target_keys = {repr(target)}
    # Integer-valued floats also written without a decimal point.
    if target == int(target):
        target_keys.add(str(int(target)))
    # The fraction reading is warranted only when the VALUE is a bare fraction,
    # which is the sole case ``parse_leading_number`` reads as a quotient.
    as_fraction = bool(_BARE_FRACTION_RE.match(value or ""))
    q_nums = _numbers(quote, allow_fractions=as_fraction)
    s_nums = _numbers(source, allow_fractions=as_fraction)
    return bool(target_keys & q_nums) and bool(target_keys & s_nums)


def verify_point(point: DataPoint, *, chunk_text: str = "", caption: str = "") -> DataPoint:
    """Apply the hard gate to one point, mutating its assurance fields.

    Sets ``quote_verified`` and ``verification_status`` in place and returns
    the point for chaining.
    """
    if point.extraction_tier == "T3" or point.source_kind == "figure":
        # No verbatim numeric span to verify against a plot image.
        point.quote_verified = False
        point.verification_status = "figure_digitized"
        return point

    # F8: an OCR-mangled single-number value (e.g. "1 10 5 ohm cm" for 1e5)
    # parses to its first token and would verify against any source containing
    # that token. Reject rather than store a semantically-wrong-but-verified
    # number.
    if point.value_type in _SINGLE_NUMBER_TYPES and is_ocr_mangled_scalar(
        point.value_original or point.value_text
    ):
        point.quote_verified = False
        point.verification_status = "rejected"
        return point

    source = "\n".join(s for s in (chunk_text, caption) if s)
    located = quote_in_source(point.grounding_quote, source)
    supported = number_supported(
        point.value_original or point.value_text, point.grounding_quote, source
    )
    if located and supported:
        point.quote_verified = True
        point.verification_status = "verified"
    else:
        point.quote_verified = False
        point.verification_status = "rejected"
    return point


def verify_points(
    points: list[DataPoint],
    *,
    source_for: "callable[[DataPoint], tuple[str, str]] | None" = None,
) -> dict:
    """Verify a batch. ``source_for`` maps a point to ``(chunk_text, caption)``.

    Returns counts by resulting status and the verified subset.
    """
    counts: dict[str, int] = {}
    for p in points:
        chunk_text, caption = ("", "")
        if source_for is not None:
            chunk_text, caption = source_for(p)
        verify_point(p, chunk_text=chunk_text, caption=caption)
        counts[p.verification_status] = counts.get(p.verification_status, 0) + 1
    return {
        "counts": counts,
        "verified": [p for p in points if p.verification_status == "verified"],
    }
