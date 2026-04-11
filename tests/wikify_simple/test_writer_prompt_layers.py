"""Tests for Phase 5B: content-hashed prompt layer delivery.

Covers:
- Two sequential build_write_request calls in the same process yield identical layer hashes.
- _meta/prompt_layers/<hash>.md files exist after pipeline.run.
- The hash function is content-based (a one-character change changes the hash).
"""

import json
from pathlib import Path

import pytest

from wikify_simple.contracts.schema import WriteEvidenceRef, WriteRequest
from wikify_simple.distill.write.requests import WriteRequestConfig, build_write_request
from wikify_simple.models import Evidence, WikiPage
from wikify_simple.paths import BundlePaths
from wikify_simple.prompts.registry import _content_hash, compose_writer_prompt_layer_hashes
from wikify_simple.store.images_index import ImageIndex


# --- helpers -----------------------------------------------------------------


def _make_page(page_id: str = "Atomic Layer Deposition", kind: str = "article") -> WikiPage:
    return WikiPage(
        id=page_id,
        kind=kind,
        title=page_id,
        aliases=[],
        body_markdown="",
        evidence=[Evidence(chunk_id="c1", doc_id="d1", quote="quote", locator="")],
        links=[],
        provenance={},
    )


def _make_cfg(style: str = "STYLE", field: str = "FIELD", artifact: str = "ARTIFACT") -> WriteRequestConfig:
    return WriteRequestConfig(
        model_id="haiku",
        writer_tier="M",
        prompt_name="wikify_simple/write",
        style_text=style,
        field_text=field,
        artifact_text=artifact,
        person_artifact_text="PERSON_ARTIFACT",
        persona_text="PERSONA",
        style_guide_hash=_content_hash(style),
        field_guide_hash=_content_hash(field),
        artifact_template_hash=_content_hash(artifact),
        person_artifact_hash=_content_hash("PERSON_ARTIFACT"),
        corpus_persona_hash=_content_hash("PERSONA"),
    )


def _make_images_index(tmp_path: Path) -> ImageIndex:
    idx_path = tmp_path / "images.json"
    idx_path.write_text(json.dumps({"images": []}), encoding="utf-8")
    return ImageIndex.load_from_path(idx_path)


# --- 5B.2: layer hashes are stable across calls ------------------------------


def test_layer_hashes_stable_across_sequential_calls(tmp_path: Path) -> None:
    """Two build_write_request calls in the same process produce the same hashes."""
    images = _make_images_index(tmp_path)
    page = _make_page()
    cfg = _make_cfg()
    from wikify_simple.distill.extract.dossier import DossierStore

    ds = DossierStore(tmp_path)
    req1 = build_write_request(page, [page], {}, ds, {}, images, cfg)
    req2 = build_write_request(page, [page], {}, ds, {}, images, cfg)
    assert req1.style_guide_hash == req2.style_guide_hash
    assert req1.field_guide_hash == req2.field_guide_hash
    assert req1.artifact_template_hash == req2.artifact_template_hash
    assert req1.corpus_persona_hash == req2.corpus_persona_hash


def test_layer_hashes_populated_on_build_write_request(tmp_path: Path) -> None:
    """build_write_request sets all four hash fields when cfg carries them."""
    images = _make_images_index(tmp_path)
    page = _make_page()
    cfg = _make_cfg()
    from wikify_simple.distill.extract.dossier import DossierStore

    ds = DossierStore(tmp_path)
    req = build_write_request(page, [page], {}, ds, {}, images, cfg)
    assert req.style_guide_hash is not None
    assert req.field_guide_hash is not None
    assert req.artifact_template_hash is not None
    assert req.corpus_persona_hash is not None
    assert len(req.style_guide_hash) == 16
    assert all(c in "0123456789abcdef" for c in req.style_guide_hash)


# --- 5B.2: hash function is content-based ------------------------------------


def test_content_hash_changes_with_content() -> None:
    """A one-character change to the input changes the hash."""
    h1 = _content_hash("hello world")
    h2 = _content_hash("hello world!")
    assert h1 != h2


def test_content_hash_stable_for_same_input() -> None:
    h1 = _content_hash("stable content")
    h2 = _content_hash("stable content")
    assert h1 == h2


def test_content_hash_is_16_hex_chars() -> None:
    h = _content_hash("any text")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_compose_writer_prompt_layer_hashes_returns_three_keys() -> None:
    hashes = compose_writer_prompt_layer_hashes("materials_science", "wiki_concept")
    assert set(hashes) == {"style_guide", "field_guide", "artifact_template"}
    for v in hashes.values():
        assert len(v) == 16


def test_compose_writer_prompt_layer_hashes_stable() -> None:
    """Same inputs -> same hashes across two calls."""
    h1 = compose_writer_prompt_layer_hashes("materials_science", "wiki_concept")
    h2 = compose_writer_prompt_layer_hashes("materials_science", "wiki_concept")
    assert h1 == h2


def test_compose_writer_prompt_layer_hashes_differ_by_field() -> None:
    h_ms = compose_writer_prompt_layer_hashes("materials_science", "wiki_concept")
    h_gen = compose_writer_prompt_layer_hashes("generic", "wiki_concept")
    assert h_ms["field_guide"] != h_gen["field_guide"]
    # style_guide and artifact_template are the same file regardless of field
    assert h_ms["style_guide"] == h_gen["style_guide"]
    assert h_ms["artifact_template"] == h_gen["artifact_template"]


# --- 5B.2: BundlePaths.prompt_layers_dir ------------------------------------


def test_bundle_paths_prompt_layers_dir(tmp_path: Path) -> None:
    from wikify_simple.paths import BundlePaths

    bundle = BundlePaths(root=tmp_path / "bundle")
    assert bundle.prompt_layers_dir == tmp_path / "bundle" / "_meta" / "prompt_layers"


# --- 5B.2: pipeline writes layer files ---------------------------------------


def test_pipeline_writes_prompt_layer_files(tmp_path: Path) -> None:
    """After pipeline.run, _meta/prompt_layers/<hash>.md files must exist."""
    import random

    from wikify_simple.distill.pipeline import _write_prompt_layer_files
    from wikify_simple.paths import BundlePaths

    bundle = BundlePaths(root=tmp_path / "bundle")
    layers = {
        _content_hash("style content"): "style content",
        _content_hash("field content"): "field content",
    }
    _write_prompt_layer_files(bundle, layers)

    for h, text in layers.items():
        path = bundle.prompt_layers_dir / f"{h}.md"
        assert path.exists(), f"expected {path} to exist"
        assert path.read_text(encoding="utf-8") == text


def test_pipeline_write_prompt_layer_files_idempotent(tmp_path: Path) -> None:
    """Calling _write_prompt_layer_files twice does not overwrite existing files."""
    from wikify_simple.distill.pipeline import _write_prompt_layer_files
    from wikify_simple.paths import BundlePaths

    bundle = BundlePaths(root=tmp_path / "bundle")
    h = _content_hash("content")
    _write_prompt_layer_files(bundle, {h: "content"})
    path = bundle.prompt_layers_dir / f"{h}.md"
    mtime1 = path.stat().st_mtime

    _write_prompt_layer_files(bundle, {h: "content"})
    mtime2 = path.stat().st_mtime
    assert mtime1 == mtime2, "file was rewritten on second call"


# --- 5B.3: neighbor_titles removed from WriteRequest -------------------------


def test_write_request_has_no_neighbor_titles_field() -> None:
    """WriteRequest must not accept neighbor_titles (field was dropped in 5B.3)."""
    import pydantic

    with pytest.raises((TypeError, pydantic.ValidationError)):
        WriteRequest(
            page_id="p1",
            page_kind="article",
            title="X",
            aliases=[],
            skeleton="",
            evidence=[WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="q", locator="")],
            neighbor_titles=["a", "b"],  # must be rejected
            prompt_template="wikify_simple/write",
            model_id="haiku",
            tier="S",
        )


def test_write_request_neighbor_summaries_accepted() -> None:
    """neighbor_summaries is the single neighborhood field."""
    req = WriteRequest(
        page_id="p1",
        page_kind="article",
        title="X",
        aliases=[],
        skeleton="",
        evidence=[WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="q", locator="")],
        prompt_template="wikify_simple/write",
        model_id="haiku",
        tier="S",
        neighbor_summaries=[{"title": "Y", "lead": "Lead text."}],
    )
    assert req.neighbor_summaries[0]["title"] == "Y"
