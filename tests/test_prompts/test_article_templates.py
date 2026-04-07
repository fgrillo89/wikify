"""Tests for prompts/article_templates.py -- Adaptive article templates."""

from __future__ import annotations

from wikify.papers.prompts.article_templates import (
    WRITING_RULES,
    get_article_template,
)


def test_material_template():
    """Material template includes Properties and Synthesis sections."""
    template = get_article_template("material", "HfO2")
    assert "## Properties" in template
    assert "## Synthesis" in template
    assert "## Applications" in template
    assert "HfO2" in template


def test_technique_template():
    """Technique template includes Mechanism and Process Parameters."""
    template = get_article_template("technique", "ALD")
    assert "## Mechanism" in template
    assert "## Process Parameters" in template
    assert "## Advantages" in template


def test_phenomenon_template():
    """Phenomenon template includes Physical Mechanism and Signatures."""
    template = get_article_template("phenomenon", "Resistive Switching")
    assert "## Physical Mechanism" in template
    assert "## Experimental Signatures" in template


def test_method_template():
    """Method template includes Procedure and Inputs/Outputs."""
    template = get_article_template("method", "XRD")
    assert "## Procedure" in template
    assert "## Inputs" in template


def test_theory_template():
    """Theory template includes Predictions and Experimental Support."""
    template = get_article_template("theory", "Drift-Diffusion Model")
    assert "## Predictions" in template
    assert "## Experimental Support" in template


def test_dataset_template():
    """Dataset template includes Contents and Usage."""
    template = get_article_template("dataset", "MNIST")
    assert "## Contents" in template
    assert "## Usage" in template


def test_generic_fallback():
    """Unknown type falls back to generic template."""
    template = get_article_template("", "Something")
    assert "## What Is Known" in template
    assert "## Open Questions" in template


def test_template_with_parameters():
    """Parameters are included as a table when provided."""
    params = [
        {"name": "growth rate", "value": "1.0", "unit": "A/cycle", "conditions": "250C"},
    ]
    template = get_article_template("material", "HfO2", parameters=params)
    assert "## Parameters" in template
    assert "growth rate" in template
    assert "1.0" in template


def test_template_with_evidence():
    """Evidence quotes are included when provided."""
    evidence = [
        {"paper_display": "Yang 2011", "quote": "ALD achieves atomic control"},
    ]
    template = get_article_template("material", "HfO2", evidence=evidence)
    assert "[REF:Yang 2011]" in template
    assert "ALD achieves atomic control" in template


def test_template_without_params_or_evidence():
    """No params/evidence sections when data is empty."""
    template = get_article_template("material", "HfO2")
    assert "## Parameters" not in template
    assert "[REF:" not in template


def test_writing_rules():
    """Writing rules include key constraints."""
    assert "em-dash" in WRITING_RULES
    assert "wikilinks" in WRITING_RULES
    assert "one concept per sentence" in WRITING_RULES.lower()
