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
    assert "Banned Words" in text
    assert len(text) > 3000


def test_load_field_guide_materials_science() -> None:
    text = load_field_guide("materials_science")
    assert "Materials Science" in text
    assert "ALD" in text or "thin-film" in text or "atomic layer" in text.lower()


def test_load_field_guide_unknown_raises() -> None:
    with pytest.raises(KeyError):
        load_field_guide("not_a_real_field")


def test_available_field_guides_includes_eight() -> None:
    fields = available_field_guides()
    assert "generic" in fields
    assert "materials_science" in fields
    assert len(fields) >= 8


def test_load_artifact_template_wiki_concept() -> None:
    text = load_artifact_template("wiki_concept")
    assert "## Definition" in text
    assert "## Background" in text
    assert "## Mechanism / Process" in text
    assert "## Applications" in text
    assert "## Open Questions" in text
    assert "## References" in text


def test_load_artifact_template_wiki_person() -> None:
    text = load_artifact_template("wiki_person")
    assert "Person" in text or "person" in text
    assert "Publications in this corpus" in text


def test_load_artifact_template_unknown_raises() -> None:
    with pytest.raises(KeyError):
        load_artifact_template("not_a_real_artifact")


def test_available_artifact_templates_includes_both() -> None:
    arts = available_artifact_templates()
    assert "wiki_concept" in arts
    assert "wiki_person" in arts


def test_compose_writer_prompt_orders_layers() -> None:
    style = load_style_guide()
    field = load_field_guide("materials_science")
    artifact = load_artifact_template("wiki_concept")
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
    assert len(composed) > 6000


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
        neighbor_titles=[],
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
        neighbor_titles=[],
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
