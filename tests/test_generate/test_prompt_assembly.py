"""Tests verifying correct prompt assembly for all writing configurations."""

from __future__ import annotations

import pytest

from scholarforge.generate.artifact_types.registry import ARTIFACT_TYPES, get_artifact_type
from scholarforge.generate.field_guide import detect_field, load_field_guide

# ── Auto-mock the database so no real DB is needed ──────────────────────────


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """Patch out DB call so all tests run without a populated database."""
    monkeypatch.setattr(
        "scholarforge.generate.persona._get_top_topics",
        lambda limit=5: [],
    )


# ── Helpers / Constants ──────────────────────────────────────────────────────

ARTIFACT_TYPE_MARKERS: dict[str, str] = {
    "lit_review": "Synthesize",
    "research_article": "IMRaD",
    "grant_proposal": "Specific Aims",
    "technical_report": "Recommendations",
    "master_thesis": "Methodology",
    "phd_thesis": "Theoretical Framework",
    "research_paper_undergrad": "undergraduate",
}

FIELD_TRIGGERS: dict[str, str] = {
    "materials_science": "ALD thin film deposition nanoparticle",
    "computer_science": "neural network transformer deep learning",
    "biology": "CRISPR gene expression protein",
    "medicine": "clinical trial patient diagnosis",
    "mathematics": "theorem proof conjecture",
    "physics": "quantum mechanics Hamiltonian",
    "social_sciences": "survey participants behavioral economics",
}

FIELD_MARKERS: dict[str, str] = {
    "materials_science": "Novoselov",
    "computer_science": "ablation",
    "biology": "nomenclature",
    "medicine": "CONSORT",
    "mathematics": "theorem-proof",
    "physics": "Dimensional analysis",
    "social_sciences": "effect size",
}


# ── Test 1: Base style guide always present ──────────────────────────────────


def test_base_style_guide_contains_banned_words():
    from scholarforge.generate.persona import build_persona

    prompt = build_persona(user_prompt="test")
    assert "Banned Words" in prompt, "Expected 'Banned Words' section in base style guide"


def test_base_style_guide_contains_nominalizations():
    from scholarforge.generate.persona import build_persona

    prompt = build_persona(user_prompt="test")
    assert "nominalizations" in prompt, "Expected 'nominalizations' in base style guide"


def test_base_style_guide_contains_em_dashes():
    from scholarforge.generate.persona import build_persona

    prompt = build_persona(user_prompt="test")
    assert "em-dashes" in prompt or "em-dash" in prompt, (
        "Expected em-dash reference in base style guide"
    )


def test_base_style_guide_contains_known_new_contract():
    from scholarforge.generate.persona import build_persona

    prompt = build_persona(user_prompt="test")
    assert "Known-new contract" in prompt, "Expected 'Known-new contract' in base style guide"


# ── Test 2: Each artifact type injects its rules ─────────────────────────────


@pytest.mark.parametrize("type_id,marker", list(ARTIFACT_TYPE_MARKERS.items()))
def test_artifact_type_marker_in_prompt(type_id: str, marker: str):
    from scholarforge.generate.persona import build_persona

    prompt = build_persona(artifact_type_id=type_id, user_prompt="test")
    assert marker in prompt, f"Artifact type '{type_id}' prompt missing expected marker '{marker}'"


# ── Test 3: Field detection works for each field ─────────────────────────────


@pytest.mark.parametrize("expected_field,trigger", list(FIELD_TRIGGERS.items()))
def test_field_detection(expected_field: str, trigger: str):
    detected = detect_field(trigger, [])
    assert detected == expected_field, (
        f"Expected field '{expected_field}' for trigger '{trigger}', got '{detected}'"
    )


# ── Test 4: Field guide content injected into prompt ─────────────────────────


@pytest.mark.parametrize("field,trigger", list(FIELD_TRIGGERS.items()))
def test_field_guide_injected_into_prompt(field: str, trigger: str):
    from scholarforge.generate.persona import build_persona

    marker = FIELD_MARKERS[field]
    prompt = build_persona(user_prompt=trigger)
    assert marker in prompt, (
        f"Field '{field}' guide marker '{marker}' not found in prompt for trigger '{trigger}'"
    )


# ── Test 5: Generic fallback when no field matches ───────────────────────────


def test_detect_field_generic_fallback():
    result = detect_field("random unrelated topic xyz", [])
    assert result == "generic", f"Expected 'generic', got '{result}'"


def test_build_persona_generic_still_has_style_guide():
    from scholarforge.generate.persona import build_persona

    prompt = build_persona(user_prompt="random xyz")
    assert "Banned Words" in prompt, (
        "Generic prompt should still contain base style guide with 'Banned Words'"
    )


# ── Test 6: build_generation_prompt includes agent instructions ───────────────


def test_generation_prompt_contains_deep_read():
    from scholarforge.agent.defaults import build_generation_prompt

    prompt = build_generation_prompt(field_hint="ALD memristors")
    assert "deep_read" in prompt, "Expected 'deep_read' tool instruction in generation prompt"


def test_generation_prompt_contains_citation_markers():
    from scholarforge.agent.defaults import build_generation_prompt

    prompt = build_generation_prompt(field_hint="ALD memristors")
    assert "[REF:" in prompt, "Expected '[REF:' citation instruction in generation prompt"


def test_generation_prompt_contains_list_papers():
    from scholarforge.agent.defaults import build_generation_prompt

    prompt = build_generation_prompt(field_hint="ALD memristors")
    assert "list_papers" in prompt, (
        "Expected 'list_papers' exploration instruction in generation prompt"
    )


# ── Test 7: All artifact type .md files exist and are non-empty ───────────────


@pytest.mark.parametrize("type_id", list(ARTIFACT_TYPES.keys()))
def test_artifact_type_instructions_non_empty(type_id: str):
    artifact = get_artifact_type(type_id)
    instructions = artifact.instructions
    assert instructions, f"Artifact type '{type_id}' has empty instructions"
    assert len(instructions) >= 100, (
        f"Artifact type '{type_id}' instructions too short ({len(instructions)} chars)"
    )


# ── Test 8: All field guide .md files exist and are non-empty ─────────────────


@pytest.mark.parametrize("field", list(FIELD_TRIGGERS.keys()))
def test_field_guide_non_empty(field: str):
    guide = load_field_guide(field)
    assert guide, f"Field guide for '{field}' is empty or missing"
    assert len(guide) >= 100, f"Field guide for '{field}' too short ({len(guide)} chars)"


# ── Test 9: Journal profile affects the prompt ────────────────────────────────


def test_journal_profile_in_prompt():
    from scholarforge.export.journal_profile import load_journal_profile
    from scholarforge.generate.persona import build_persona

    journal_profile = load_journal_profile("Advanced Functional Materials")
    prompt = build_persona(journal_profile=journal_profile)
    assert "Advanced Functional Materials" in prompt, (
        "Expected journal name 'Advanced Functional Materials' in prompt"
    )


def test_journal_profile_name_via_build_generation_prompt():
    from scholarforge.agent.defaults import build_generation_prompt

    prompt = build_generation_prompt(journal="Advanced Functional Materials")
    assert "Advanced Functional Materials" in prompt, (
        "Expected 'Advanced Functional Materials' in prompt from build_generation_prompt"
    )


# ── Test 10: Combined — ALD lit review for AFM ────────────────────────────────


def test_combined_ald_lit_review_afm():
    """Real-world scenario: ALD lit review for Advanced Functional Materials."""
    from scholarforge.agent.defaults import build_generation_prompt

    prompt = build_generation_prompt(
        artifact_type_id="lit_review",
        journal="Advanced Functional Materials",
        field_hint="ALD memristors for neuromorphic computing",
    )

    # Base style guide present
    assert "Banned Words" in prompt, "Missing base style guide 'Banned Words'"

    # Artifact type rules present
    assert "Synthesize" in prompt, "Missing lit_review marker 'Synthesize'"

    # Field guide present (materials_science marker)
    assert "Novoselov" in prompt, "Missing materials_science field guide marker 'Novoselov'"

    # Journal name present
    assert "Advanced Functional Materials" in prompt, (
        "Missing journal name 'Advanced Functional Materials'"
    )

    # Agent instructions present
    assert "deep_read" in prompt, "Missing agent instruction 'deep_read'"
