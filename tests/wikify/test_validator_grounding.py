"""F19 regression: validator grounding tolerates dossier-style rendering noise.

The dossier the writer reads collapses whitespace, turns OCR control chars into
spaces, and strips inline numeric citation markers. A quote copied verbatim
from that readable view must validate against the raw ``chunk_text`` without
forcing the writer into an extra ``draft check`` pass — while a genuinely
fabricated quote must still be rejected.
"""

from wikify.bundle.draft.validator import _ground_norm, _quote_is_grounded


def test_exact_substring_still_grounds():
    assert _quote_is_grounded("self-limiting growth", "ALD uses self-limiting growth steps.")


def test_whitespace_and_ocr_spaces_tolerated():
    # Raw chunk has OCR-doubled spaces + a control char where a sign was.
    raw = "endurance \x01 of 10 6 cycles was measured"
    quote = "endurance of 10 6 cycles was measured"
    assert _quote_is_grounded(quote, raw)


def test_inline_citation_markers_stripped_both_sides():
    # Dossier shows the sentence without the inline "[1-3]" citation; the raw
    # chunk_text keeps it. The clean quote must still ground.
    raw = "Memristors exhibit pinched hysteresis [1-3] under bias."
    quote = "Memristors exhibit pinched hysteresis under bias."
    assert _quote_is_grounded(quote, raw)


def test_case_insensitive_after_norm():
    raw = "The Atomic Layer Deposition Cycle proceeds via two half-reactions."
    quote = "atomic layer deposition cycle proceeds via two half-reactions"
    assert _quote_is_grounded(quote, raw)


def test_fabricated_quote_still_rejected():
    raw = "ALD grows conformal oxide films one monolayer per cycle."
    # Words that never appear in the source must not ground.
    assert not _quote_is_grounded("ALD enables 5 nm copper interconnects", raw)


def test_empty_inputs_not_grounded():
    assert not _quote_is_grounded("", "something")
    assert not _quote_is_grounded("something", "")


def test_ground_norm_idempotent_and_noise_free():
    assert _ground_norm("A  b\x01[12] c ") == "a b c"
