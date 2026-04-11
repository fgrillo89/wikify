"""Tests for the layered writer prompt: style + field + artifact + persona."""

from pathlib import Path

import pytest

from wikify_simple.contracts.schema import WriteEvidenceRef, WriteRequest
from wikify_simple.distill.write.persona import (
    build_persona_prompt,
    generate_corpus_persona,
    load_corpus_persona,
)
from wikify_simple.models import Document
from wikify_simple.paths import CorpusPaths
from wikify_simple.prompts import (
    available_artifact_templates,
    available_field_guides,
    compose_writer_prompt,
    load_artifact_template,
    load_field_guide,
    load_style_guide,
)


def test_load_style_guide_returns_full_file() -> None:
    text = load_style_guide()
    assert "Academic Writing Style Guide" in text
    assert "Banned" in text
    assert len(text) > 500


def test_load_field_guide_materials_science() -> None:
    text = load_field_guide("materials_science")
    assert "Materials Science" in text
    assert "XRD" in text or "SEM" in text or "characterization" in text.lower()


def test_load_field_guide_unknown_raises() -> None:
    with pytest.raises(KeyError):
        load_field_guide("not_a_real_field")


def test_available_field_guides_includes_eight() -> None:
    fields = available_field_guides()
    assert "generic" in fields
    assert "materials_science" in fields
    assert len(fields) >= 8


# --- Phase 6E: wiki_article template ---


def test_load_artifact_template_wiki_article() -> None:
    text = load_artifact_template("wiki_article")
    # Lead section rule
    assert "bold" in text.lower() or "**bold**" in text or "**Full Name**" in text or "**" in text
    assert "Lead" in text or "lead" in text
    # Required-sections language
    assert "at least 2" in text
    assert "before" in text
    assert "References" in text
    # Appendix order
    assert "See also" in text
    # Banned phrases present in the template
    assert "in this corpus" in text
    assert "in this article" in text
    assert "as discussed above" in text


def test_wiki_article_template_has_no_old_six_section_framing() -> None:
    text = load_artifact_template("wiki_article")
    # Old template had a loose "sections are guidance" escape hatch
    assert "sections are guidance" not in text.lower()
    # Old template had a "Definition" section that was part of the six-section layout
    assert "## Definition" not in text
    assert "## Open Questions" not in text


def test_wiki_article_template_has_lead_example() -> None:
    text = load_artifact_template("wiki_article")
    # A concrete worked example is required per the plan
    assert "ALD" in text or "Atomic layer deposition" in text or "Photocatalysis" in text


def test_wiki_article_template_references_required_last() -> None:
    text = load_artifact_template("wiki_article")
    refs_idx = text.rfind("## References")
    see_also_idx = text.rfind("## See also")
    assert refs_idx > 0, "## References must appear in the template"
    assert see_also_idx < refs_idx, "## See also must appear before ## References"


# --- Phase 6E: wiki_person template ---


def test_load_artifact_template_wiki_person() -> None:
    text = load_artifact_template("wiki_person")
    # Lead pattern for full author
    assert "year-range" in text or "year_range" in text or "(year-range)" in text
    # Lead pattern for mentioned person
    assert "is credited with" in text
    # Required section
    assert "## Research" in text or "## Contributions" in text
    # Publications rule
    assert "Publications" in text
    assert "author_context" in text
    # References required last
    assert "## References" in text


def test_wiki_person_template_has_no_deterministic_tier_framing() -> None:
    text = load_artifact_template("wiki_person")
    # Phase 6B retires the deterministic skeleton; old template had two-tier framing
    assert "Tier 1" not in text
    assert "Tier 2" not in text
    # Old template had these as deterministic outputs; new template forbids them
    assert "Publications in this corpus" not in text
    assert "Cited works in this corpus" not in text
    assert "build_author_pages" not in text


def test_wiki_person_template_banned_phrases_listed() -> None:
    text = load_artifact_template("wiki_person")
    # All corpus-meta banned phrases must appear in the Banned Phrases section
    assert "appears in this corpus" in text
    assert "mentioned in this corpus only through citations" in text
    assert "in this corpus" in text
    assert "this corpus contains" in text


def test_wiki_person_template_references_required_last() -> None:
    text = load_artifact_template("wiki_person")
    refs_idx = text.rfind("## References")
    assert refs_idx > 0, "## References must appear in the template"
    # Nothing substantive after References except the format block
    after = text[refs_idx:]
    # The only content after ## References should be the format/hard-minimums block
    assert "## Education" not in after
    assert "## Career" not in after


# --- Phase 6E: style_guide banned phrases ---


def test_style_guide_has_banned_phrases_section() -> None:
    text = load_style_guide()
    assert "Banned Phrases" in text or "Banned phrases" in text
    assert "in this corpus" in text
    assert "appears in this corpus" in text
    assert "mentioned in this corpus only through citations" in text
    assert "this corpus contains" in text
    assert "in this article" in text
    assert "as discussed above" in text


# --- Phase 6E: wiki_concept is gone ---


def test_wiki_concept_template_does_not_exist() -> None:
    with pytest.raises(KeyError):
        load_artifact_template("wiki_concept")


def test_available_artifact_templates_has_article_not_concept() -> None:
    arts = available_artifact_templates()
    assert "wiki_article" in arts
    assert "wiki_person" in arts
    assert "wiki_concept" not in arts


# --- Registry load sanity ---


def test_load_artifact_template_unknown_raises() -> None:
    with pytest.raises(KeyError):
        load_artifact_template("not_a_real_artifact")


# --- Compose prompt (updated for wiki_article) ---


def test_compose_writer_prompt_orders_layers() -> None:
    style = load_style_guide()
    field = load_field_guide("materials_science")
    artifact = load_artifact_template("wiki_article")
    persona = "You are a senior expert in atomic layer deposition."
    composed = compose_writer_prompt(
        style=style,
        field=field,
        artifact=artifact,
        persona=persona,
        page_kind="article",
    )
    # Order: persona -> style -> field -> artifact -> composer footer
    assert composed.index("Author Persona") < composed.index("Academic Writing Style Guide")
    assert composed.index("Academic Writing Style Guide") < composed.index(
        "Field-Specific Writing Guide"
    )
    assert composed.index("Field-Specific Writing Guide") < composed.index("Output Template")
    assert composed.index("Output Template") < composed.index("Composition")
    assert "atomic layer deposition" in composed
    assert 'kind="article"' in composed
    assert len(composed) > 2000


def test_compose_writer_prompt_uses_generic_persona_when_empty() -> None:
    composed = compose_writer_prompt(
        style="STYLE",
        field="FIELD",
        artifact="ARTIFACT",
        persona=None,
        page_kind="article",
    )
    assert "senior domain expert" in composed


def test_write_request_accepts_layered_fields() -> None:
    req = WriteRequest(
        page_id="p1",
        page_kind="article",
        title="Atomic Layer Deposition",
        aliases=[],
        skeleton="",
        evidence=[WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="quote text", locator="")],
        prompt_template="wikify_simple/write",
        model_id="haiku",
        tier="S",
        style_guide="STYLE",
        field_guide="FIELD",
        artifact_template="ARTIFACT",
        corpus_persona="PERSONA",
    )
    assert req.style_guide == "STYLE"
    assert req.field_guide == "FIELD"
    assert req.artifact_template == "ARTIFACT"
    assert req.corpus_persona == "PERSONA"


def test_write_request_layered_fields_default_to_empty() -> None:
    req = WriteRequest(
        page_id="p1",
        page_kind="article",
        title="X",
        aliases=[],
        skeleton="",
        evidence=[WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="quote text", locator="")],
        prompt_template="wikify_simple/write",
        model_id="haiku",
        tier="S",
    )
    assert req.style_guide == ""
    assert req.field_guide == ""
    assert req.artifact_template == ""
    assert req.corpus_persona == ""


def _make_doc(i: int) -> Document:
    return Document(
        id=f"doc{i}",
        source_path=f"/tmp/doc{i}.pdf",
        kind="pdf",
        title=f"Document {i}",
        metadata={},
        markdown_path="",
        image_dir="",
        abstract=f"Abstract for document {i} discussing topic.",
    )


def test_generate_corpus_persona_stub(tmp_path: Path) -> None:
    corpus = CorpusPaths(root=tmp_path)
    docs = [_make_doc(i) for i in range(3)]
    text = generate_corpus_persona(corpus=corpus, sample_docs=docs, complete=None, field="generic")
    assert text
    assert corpus.persona_path.exists()
    assert load_corpus_persona(corpus) == text.strip()


def test_generate_corpus_persona_with_callable(tmp_path: Path) -> None:
    corpus = CorpusPaths(root=tmp_path)
    docs = [_make_doc(i) for i in range(2)]
    captured: dict[str, str] = {}

    def fake_complete(prompt: str) -> str:
        captured["prompt"] = prompt
        return "You are a senior expert. " * 20

    text = generate_corpus_persona(
        corpus=corpus, sample_docs=docs, complete=fake_complete, field="materials_science"
    )
    assert "senior expert" in text
    assert "materials_science" in captured["prompt"]
    assert "Document 0" in captured["prompt"]


def test_build_persona_prompt_handles_empty_docs() -> None:
    text = build_persona_prompt([], field="generic")
    assert "no documents in corpus" in text
