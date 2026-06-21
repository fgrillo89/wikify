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

from .models import _NUMBER_RE, DataPoint, parse_leading_number

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip().lower())


def _numbers(s: str) -> set[str]:
    """Numeric tokens in *s* as a set of comparison keys.

    Uses the same exponent-/thousands-aware regex as ``parse_leading_number``
    and reduces each token to its float-normalized form, so ``2.5e-3``,
    ``0.0025``, ``1.10`` and ``1.1`` all collapse to one key and match a
    target parsed the same way.
    """
    out: set[str] = set()
    for m in _NUMBER_RE.finditer(s or ""):
        tok = m.group(0)
        cleaned = tok.replace("−", "-").replace(",", "").replace(" ", "")
        out.add(cleaned)
        try:
            val = float(cleaned)
        except ValueError:
            continue
        out.add(repr(val))
        if val == int(val):
            out.add(str(int(val)))
    return out


def quote_in_source(quote: str, source: str) -> bool:
    """True if *quote* appears in *source* (exact or whitespace-collapsed)."""
    if not quote or not source:
        return False
    if quote in source:
        return True
    return _norm(quote) in _norm(source)


def number_supported(value: str, quote: str, source: str) -> bool:
    """True if the reported number appears in BOTH the quote and the source.

    Floats are compared in normalized form so "1.10" and "1.1" agree. When
    the value carries no number (categorical / qualitative), fall back to
    requiring the value text itself inside the quote.
    """
    target = parse_leading_number(value)
    if target is None:
        return _norm(value) in _norm(quote) if value else False
    target_keys = {repr(target)}
    # Integer-valued floats also written without a decimal point.
    if target == int(target):
        target_keys.add(str(int(target)))
    q_nums = _numbers(quote)
    s_nums = _numbers(source)
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
