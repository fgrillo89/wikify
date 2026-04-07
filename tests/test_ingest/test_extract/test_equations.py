"""Tests for equation extraction pipeline."""

from __future__ import annotations

import json

from wikify.ingest.extract.equations import (
    CHEM_EQUATION_RE,
    DISPLAY_MATH_PATTERNS,
    INLINE_MATH_RE,
    NAMED_EQUATION_RE,
    PICTURE_OMITTED_RE,
    UNICODE_EQUATION_RE,
    _extract_variables,
    _is_plausible_equation,
    extract_equations,
)
from wikify.core.store.models import Chunk


def _make_chunk(
    chunk_id: str, content: str, paper_id: str = "paper1", section_path: str = ""
) -> Chunk:
    return Chunk(
        id=chunk_id,
        paper_id=paper_id,
        content=content,
        section_path=section_path,
        token_count=len(content.split()),
        chunk_index=0,
    )


class TestDisplayMathDetection:
    def test_double_dollar(self):
        text = "Newton's second law: $$F = ma$$ is fundamental."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        math_eqs = [e for e in eqs if e.equation_type == "mathematical"]
        assert len(math_eqs) == 1
        assert math_eqs[0].latex == "F = ma"
        # Also detects "Newton's second law" as a named equation
        named = [e for e in eqs if e.equation_type == "named"]
        assert len(named) == 1

    def test_bracket_notation(self):
        text = r"The energy is given by \[E = mc^2\] in special relativity."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        math_eqs = [e for e in eqs if e.equation_type == "mathematical"]
        assert len(math_eqs) >= 1
        assert any("E = mc^2" in eq.latex for eq in math_eqs)

    def test_begin_equation(self):
        text = r"Consider \begin{equation}V = IR\end{equation} for Ohm's law."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        math_eqs = [e for e in eqs if e.equation_type == "mathematical"]
        assert len(math_eqs) >= 1
        assert any("V = IR" in eq.latex for eq in math_eqs)

    def test_begin_align(self):
        text = r"""Here we have:
\begin{align}
x &= y + z \\
a &= b + c
\end{align}
which shows the relation."""
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        math_eqs = [e for e in eqs if e.equation_type == "mathematical"]
        assert len(math_eqs) >= 1

    def test_multiline_display_math(self):
        text = """The result is:
$$
\\frac{d}{dx} e^x = e^x
$$
as expected."""
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        assert len(eqs) >= 1
        assert any("frac" in eq.latex for eq in eqs)


class TestInlineMathDetection:
    def test_inline_math(self):
        text = "The variable $x$ represents position."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        inline = [e for e in eqs if e.equation_type == "inline"]
        assert len(inline) == 1
        assert inline[0].latex == "x"

    def test_no_false_positive_currency(self):
        """Currency like $100 or $5.00 should not be detected."""
        text = "The cost was $100 and the fee was $5.00 per item."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        assert len(eqs) == 0

    def test_inline_expression(self):
        text = "We use $E = hf$ for the photon energy."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        inline = [e for e in eqs if e.equation_type == "inline"]
        assert len(inline) == 1
        assert "E = hf" in inline[0].latex


class TestChemicalEquationDetection:
    def test_simple_arrow(self):
        text = "The reaction is Al2O3 + H2O -> Al(OH)3 in water."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        chem = [e for e in eqs if e.equation_type == "chemical"]
        assert len(chem) == 1

    def test_unicode_arrow(self):
        text = "TMA reacts: Al(CH3)3 + H2O \u2192 Al2O3 + CH4 at high temperature."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        chem = [e for e in eqs if e.equation_type == "chemical"]
        assert len(chem) == 1

    def test_classification_is_chemical(self):
        text = "In ALD: TiCl4 + H2O -> TiO2 + HCl is the key reaction."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        chem = [e for e in eqs if e.equation_type == "chemical"]
        assert len(chem) >= 1
        assert chem[0].equation_type == "chemical"


class TestEquationLabels:
    def test_parenthetical_label(self):
        text = "Newton's law $$F = ma$$ (1) governs motion."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        assert len(eqs) >= 1
        labeled = [e for e in eqs if e.label is not None]
        assert len(labeled) >= 1
        assert labeled[0].label == "1"

    def test_eq_dot_label(self):
        text = "See Eq. 3 for $$\\nabla \\cdot E = \\rho / \\epsilon_0$$ details."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        labeled = [e for e in eqs if e.label is not None]
        assert len(labeled) >= 1
        assert labeled[0].label == "3"


class TestContextExtraction:
    def test_context_includes_surrounding_text(self):
        text = (
            "Physics is the study of nature. "
            "The most famous equation is $$E = mc^2$$ which relates energy and mass. "
            "It was proposed by Einstein."
        )
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        assert len(eqs) >= 1
        ctx = eqs[0].context
        # Context should include some surrounding text
        assert len(ctx) > 10


class TestChunkLinking:
    def test_links_to_correct_chunk(self):
        chunk1 = _make_chunk("c1", "Introduction to the topic.", section_path="intro")
        chunk2 = _make_chunk("c2", "The equation $$F = ma$$ is key.", section_path="methods")
        text = "Introduction to the topic.\n\nThe equation $$F = ma$$ is key."
        eqs = extract_equations(text, "paper1", [chunk1, chunk2])
        assert len(eqs) >= 1
        assert eqs[0].chunk_id == "c2"
        assert eqs[0].section_path == "methods"


class TestVariableExtraction:
    def test_single_latin_variables(self):
        variables = _extract_variables("F = ma")
        assert "F" in variables
        assert "m" in variables
        assert "a" in variables

    def test_greek_variables(self):
        variables = _extract_variables(r"\alpha + \beta = \gamma")
        assert r"\alpha" in variables
        assert r"\beta" in variables
        assert r"\gamma" in variables

    def test_no_latex_commands_as_variables(self):
        variables = _extract_variables(r"\frac{x}{y}")
        assert "x" in variables
        assert "y" in variables
        # "frac" should not appear
        assert "frac" not in variables and r"\frac" not in variables

    def test_variables_stored_as_json(self):
        text = "We have $$F = ma$$ in dynamics."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        assert len(eqs) >= 1
        variables = json.loads(eqs[0].variables)
        assert isinstance(variables, list)
        assert "F" in variables


class TestDeduplication:
    def test_same_equation_not_duplicated(self):
        text = "First $$F = ma$$ and again $$F = ma$$ repeated."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        math_eqs = [e for e in eqs if e.latex == "F = ma"]
        assert len(math_eqs) == 1


class TestRegexPatterns:
    """Direct tests on the compiled regex patterns."""

    def test_display_dollar(self):
        assert DISPLAY_MATH_PATTERNS[0].search("$$x + y$$")

    def test_display_bracket(self):
        assert DISPLAY_MATH_PATTERNS[1].search(r"\[x + y\]")

    def test_inline_math_re(self):
        m = INLINE_MATH_RE.search("the value $x$ is")
        assert m is not None
        assert m.group(1) == "x"

    def test_inline_no_currency(self):
        assert INLINE_MATH_RE.search("costs $100") is None

    def test_chem_equation_re(self):
        m = CHEM_EQUATION_RE.search("H2O + CO2 -> H2CO3")
        assert m is not None

    def test_unicode_equation_re(self):
        m = UNICODE_EQUATION_RE.search("The current I = V/R in the circuit")
        assert m is not None

    def test_picture_omitted_re(self):
        m = PICTURE_OMITTED_RE.search("**==> picture intentionally omitted <==**")
        assert m is not None

    def test_named_equation_re(self):
        m = NAMED_EQUATION_RE.search("We use Fick's second law to model diffusion.")
        assert m is not None
        assert m.group(1) == "Fick's"
        assert m.group(2) == "law"


class TestUnicodeEquationDetection:
    def test_simple_ohms_law(self):
        text = "The current is given by I = V/R in steady state."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        inline = [e for e in eqs if e.equation_type == "inline"]
        assert len(inline) >= 1
        assert any("I = V/R" in eq.latex for eq in inline)

    def test_function_notation(self):
        text = "The memristor model defines v(t) = M(q)i(t) for the device."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        inline = [e for e in eqs if e.equation_type == "inline"]
        assert len(inline) >= 1

    def test_greek_equation(self):
        text = "Conductivity is \u03c3 = nq\u03bc in the material."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        inline = [e for e in eqs if e.equation_type == "inline"]
        assert len(inline) >= 1

    def test_no_false_positive_prose(self):
        """Regular English sentences with '=' should not be detected."""
        text = "This is important for the process."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        assert len(eqs) == 0

    def test_no_false_positive_assignment(self):
        """Prose-like 'X = the thing' should be rejected."""
        text = "The result = very important for this study."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        # Should not pick up prose
        inline = [e for e in eqs if e.equation_type == "inline"]
        assert len(inline) == 0


class TestImageEquationDetection:
    def test_picture_omitted_with_equation_context(self):
        text = (
            "The resistance is given by the equation:\n"
            "**==> picture intentionally omitted <==**\n"
            "where R is the resistance."
        )
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        image_eqs = [e for e in eqs if e.equation_type == "image"]
        assert len(image_eqs) == 1
        assert image_eqs[0].latex == "[image equation]"

    def test_picture_omitted_without_equation_context(self):
        """A picture placeholder not near equation context should be ignored."""
        text = (
            "Figure 3 shows the surface morphology.\n"
            "**==> picture intentionally omitted <==**\n"
            "The grains are clearly visible."
        )
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        image_eqs = [e for e in eqs if e.equation_type == "image"]
        assert len(image_eqs) == 0


class TestNamedEquationDetection:
    def test_ficks_second_law(self):
        text = "We model diffusion using Fick's second law for concentration gradients."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        named = [e for e in eqs if e.equation_type == "named"]
        assert len(named) == 1
        assert "Fick" in named[0].latex
        assert "law" in named[0].latex

    def test_ohms_law(self):
        text = "According to Ohm's law, the voltage drop is proportional."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        named = [e for e in eqs if e.equation_type == "named"]
        assert len(named) == 1

    def test_arrhenius_equation(self):
        text = "The rate follows the Arrhenius equation at elevated temperatures."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        named = [e for e in eqs if e.equation_type == "named"]
        assert len(named) == 1
        assert "Arrhenius" in named[0].latex

    def test_named_deduplication(self):
        """Same named equation mentioned twice should appear only once."""
        text = "Fick's law governs diffusion. We again apply Fick's law in the next section."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        named = [e for e in eqs if e.equation_type == "named"]
        assert len(named) == 1


class TestCrossMethodDeduplication:
    def test_latex_and_unicode_same_equation(self):
        """If an equation appears in both $...$ and plain text, extract only once."""
        text = "We have $I = V/R$ and also I = V/R in the text."
        chunks = [_make_chunk("c1", text)]
        eqs = extract_equations(text, "paper1", chunks)
        # The latex version should be found; the unicode duplicate should be skipped
        ivr = [e for e in eqs if "I = V/R" in e.latex]
        assert len(ivr) == 1


class TestPlausibleEquation:
    def test_valid_equation(self):
        assert _is_plausible_equation("I = V/R") is True

    def test_prose_rejected(self):
        assert _is_plausible_equation("This = important") is False

    def test_no_operator(self):
        assert _is_plausible_equation("just some text") is False

    def test_greek_equation(self):
        assert _is_plausible_equation("\u03c3 = nq\u03bc") is True
