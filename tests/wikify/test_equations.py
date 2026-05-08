"""Unit tests for ``wikify.ingest.equations``.

Covers each of the six detector branches (display, inline, chemical,
unicode, image-equation, named) plus a few realistic regression cases
from the mvp20 corpus and the prose-vs-equation false-positive filter.
"""

from wikify.ingest.equations import extract_equations


def _types(eqs):
    return sorted({e["type"] for e in eqs})


def _ids(eqs):
    return [e["id"] for e in eqs]


def test_empty_returns_empty():
    assert extract_equations("") == []


def test_display_math_dollar_dollar():
    md = "Some text. $$E = mc^2$$ Another sentence."
    eqs = extract_equations(md)
    assert any(e["type"] == "display" for e in eqs)
    display = [e for e in eqs if e["type"] == "display"]
    assert display[0]["latex"] == "E = mc^2"


def test_display_math_bracket_form():
    md = r"Intro. \[F = ma\] Conclusion."
    eqs = extract_equations(md)
    assert any(e["type"] == "display" and "F = ma" in e["latex"] for e in eqs)


def test_display_math_equation_environment():
    md = r"""
    The relation is
    \begin{equation}
    v(t) = M(q)\,i(t)
    \end{equation}
    where M is the memristance.
    """
    eqs = extract_equations(md)
    assert any(e["type"] == "display" and "M(q)" in e["latex"] for e in eqs)


def test_inline_math_excludes_currency():
    md = "It costs $100, but the equation $a + b = c$ holds."
    eqs = extract_equations(md)
    inline = [e for e in eqs if e["type"] == "inline"]
    assert any("a + b = c" in e["latex"] for e in inline)
    # currency must NOT match
    assert not any("100" in e["latex"] for e in inline)


def test_chemical_equation_arrow():
    # The species pattern expects an element symbol (uppercase first letter
    # followed by an optional lowercase letter), so the leading stoichiometry
    # coefficient must be split with a space: "H2 + O2 -> H2O" matches,
    # "2H2" alone does not because it starts with a digit.
    md = "The reaction proceeds as H2 + O2 -> H2O at 25 C."
    eqs = extract_equations(md)
    chemical = [e for e in eqs if e["type"] == "chemical"]
    assert chemical, f"expected a chemical equation, got {eqs}"
    assert "->" in chemical[0]["latex"] or "→" in chemical[0]["latex"]


def test_unicode_math_simple():
    md = "We measured R = V/I across the bilayer."
    eqs = extract_equations(md)
    # The plain-text equation R = V/I should be picked up
    assert any(e["type"] == "unicode" for e in eqs), f"got {eqs}"


def test_unicode_math_rejects_prose():
    # "the result is good" has nothing to extract
    md = "The result is good and the system is stable."
    eqs = extract_equations(md)
    # Prose with no operator should produce no unicode-math hits.
    assert not any(e["type"] == "unicode" for e in eqs)


def test_named_equation():
    md = "We invoke Ohm's law to compute the current."
    eqs = extract_equations(md)
    named = [e for e in eqs if e["type"] == "named"]
    assert any("Ohm" in e["latex"] for e in named)


def test_named_equation_rejects_common_words():
    # "We model" must not produce a named equation match because "We"
    # is in the exclude list.
    md = "We model the device with a simple law of friction."
    eqs = extract_equations(md)
    # "law of friction" doesn't match (no proper noun before "law"),
    # and "We model" is excluded — both ways the result is empty.
    assert not any("we" in e["latex"].lower() for e in eqs)


def test_picture_omitted_with_equation_context():
    md = (
        "The current can be written as **==> picture [120 x 30] omitted <==** "
        "where the constants are determined empirically."
    )
    eqs = extract_equations(md)
    image_eqs = [e for e in eqs if e["type"] == "image"]
    assert image_eqs, f"expected an image-equation hit; got {eqs}"
    assert image_eqs[0]["latex"] == "[image equation]"


def test_picture_omitted_without_equation_context_skipped():
    md = "Here is a graphic **==> picture [120 x 30] omitted <==** describing the device geometry."
    eqs = extract_equations(md)
    # No "where" / "equation" / "given by" nearby → not flagged.
    assert not any(e["type"] == "image" for e in eqs)


def test_equations_have_stable_ids():
    md = "Two formulas: $a = b$ and $a = b$ again."
    eqs = extract_equations(md)
    inline = [e for e in eqs if e["type"] == "inline"]
    # Same content → same id → deduplicated.
    assert len({e["id"] for e in inline}) == len(inline) == 1


def test_char_offset_is_source_order():
    md = "First $x = 1$. Then $y = 2$. Finally $z = 3$."
    eqs = extract_equations(md)
    inline = [e for e in eqs if e["type"] == "inline"]
    offsets = [e["char_offset"] for e in inline]
    assert offsets == sorted(offsets)


def test_context_includes_neighbouring_sentences():
    md = (
        "We define memristance as a state-dependent resistance. "
        "The relation is $v = M(q) i$, "
        "which generalizes Ohm's law to time-varying systems."
    )
    eqs = extract_equations(md)
    target = next(e for e in eqs if "M(q)" in e["latex"])
    # Context should include at least one of the surrounding sentences.
    # The before-sentence walks back to the previous period; the after-
    # sentence walks forward to the next sentence-ender.
    assert "Ohm" in target["context"] or "memristance" in target["context"], (
        f"unexpected context: {target['context']!r}"
    )


def test_full_extract_returns_offset_sorted():
    md = (
        "Begin. Ohm's law gives $V = I R$. "
        "$$E = mc^2$$ "
        "Then H2 + O2 -> H2O. "
        "End."
    )
    eqs = extract_equations(md)
    offsets = [e["char_offset"] for e in eqs]
    assert offsets == sorted(offsets)
    types = _types(eqs)
    # We expect at least named (Ohm), inline ($V = I R$), display ($$E = mc^2$$),
    # and chemical (H2 + O2 -> H2O).
    assert "named" in types
    assert "display" in types
    assert "chemical" in types


# ---------------------------------------------------------------- junk filter


def test_display_math_rejects_pure_digit_latex():
    """Bibliography numbers Marker emits as ``$$10$$`` are not equations."""
    md = "See refs $$10$$ and $$11$$ for details."
    eqs = extract_equations(md)
    assert eqs == [], (
        f"pure-digit display math should be rejected; got {[e['latex'] for e in eqs]}"
    )


def test_display_math_rejects_single_paren():
    md = "Output $$($$ ranges from 1 to 5."
    eqs = extract_equations(md)
    assert eqs == [], f"single paren in $$..$$ should be rejected; got {eqs}"


def test_inline_math_rejects_short_punctuation():
    md = "Performance was $.$ percent better."
    eqs = extract_equations(md)
    assert eqs == [], f"single-punctuation inline should be rejected; got {eqs}"


def test_real_display_math_still_passes():
    md = r"The energy is $$E = mc^2$$ where $c$ is the speed of light."
    eqs = extract_equations(md)
    assert any(
        e["type"] == "display" and "E = mc" in e["latex"]
        for e in eqs
    ), [e["latex"] for e in eqs]


def test_real_inline_math_still_passes():
    md = r"We define $\alpha = 0.05$ as the threshold."
    eqs = extract_equations(md)
    assert any(
        e["type"] == "inline" and "alpha" in e["latex"]
        for e in eqs
    ), [e["latex"] for e in eqs]


def test_named_equations_still_pass_even_without_math_signal():
    """Named equations are matched by phrase, not by math syntax."""
    md = "We applied Ohm's law to compute resistance."
    eqs = extract_equations(md)
    assert any(e["type"] == "named" for e in eqs), eqs
