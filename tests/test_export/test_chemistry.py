"""Tests for scholarforge.export.chemistry."""

from __future__ import annotations

import pytest

from scholarforge.export.chemistry import (
    _is_chemical_formula,
    format_formulas_markdown,
    format_formulas_unicode,
    split_formula_runs,
)

# ── _is_chemical_formula ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "token",
    ["HfO2", "Al2O3", "Si3N4", "Fe2O3", "TiO2"],
)
def test_is_chemical_formula_true_for_valid_formulas(token: str):
    assert _is_chemical_formula(token) is True


def test_is_chemical_formula_false_for_figure():
    assert _is_chemical_formula("Figure") is False


def test_is_chemical_formula_false_for_single_element_no_digits():
    assert _is_chemical_formula("Si") is False


def test_is_chemical_formula_false_for_plain_word():
    assert _is_chemical_formula("hello") is False


# ── format_formulas_markdown ─────────────────────────────────────────────────


def test_format_formulas_markdown_hfo2_in_sentence():
    result = format_formulas_markdown("HfO2 films")
    assert result == "HfO<sub>2</sub> films"


def test_format_formulas_markdown_al2o3_standalone():
    result = format_formulas_markdown("Al2O3")
    assert result == "Al<sub>2</sub>O<sub>3</sub>"


def test_format_formulas_markdown_no_change_for_figure_2():
    result = format_formulas_markdown("Figure 2")
    assert result == "Figure 2"


# ── format_formulas_unicode ───────────────────────────────────────────────────


def test_format_formulas_unicode_hfo2():
    result = format_formulas_unicode("HfO2")
    assert result == "HfO₂"


def test_format_formulas_unicode_al2o3():
    result = format_formulas_unicode("Al2O3")
    assert result == "Al₂O₃"


# ── split_formula_runs ────────────────────────────────────────────────────────


def test_split_formula_runs_hfo2():
    runs = split_formula_runs("HfO2")
    assert runs == [("HfO", False), ("2", True)]


def test_split_formula_runs_plain_word():
    runs = split_formula_runs("hello")
    assert runs == [("hello", False)]


def test_split_formula_runs_al2o3_alternates():
    runs = split_formula_runs("Al2O3")
    assert runs == [("Al", False), ("2", True), ("O", False), ("3", True)]
