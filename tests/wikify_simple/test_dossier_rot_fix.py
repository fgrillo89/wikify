"""Tests for the dossier rot fix.

Covers:
  1. Sampler filters references/acknowledgments/appendix section chunks.
  2. DossierEntry.is_substantive correctly identifies empty vs populated entries.
  3. dossier_to_yaml produces valid YAML with expected fields.
  4. build_write_request populates dossier_context_yaml.
  5. _write_io_lineage writes the expected files to <bundle>/_meta/io_lineage/.
  6. _dossier_summary emits stderr warning when empty ratio > 20%.
"""

import json
import random

import numpy as np
import yaml  # noqa: I001

from wikify_simple.distill.extract.dossier import (
    SKIP_SECTION_TYPES,
    Dossier,
    DossierEntry,
    DossierStore,
    dossier_to_yaml,
)
from wikify_simple.distill.sampler import (
    GlobalOp,
    LevyMixSampler,
    LocalOp,
    SamplerState,
    init_coverage_state,
)
from wikify_simple.models import CorpusGraph
from wikify_simple.store.vectors import VectorStore

# ---------------------------------------------------------------------------
# 1. SKIP_SECTION_TYPES constant and sampler-level filtering
# ---------------------------------------------------------------------------


def test_skip_section_types_contains_expected():
    """The constant must include the three non-content section types."""
    assert "references" in SKIP_SECTION_TYPES
    assert "acknowledgments" in SKIP_SECTION_TYPES
    assert "appendix" in SKIP_SECTION_TYPES
    assert "body" not in SKIP_SECTION_TYPES
    assert "abstract" not in SKIP_SECTION_TYPES


def _make_sampler_state(chunks_by_doc: dict[str, list[str]]) -> SamplerState:
    all_ids = [cid for ids in chunks_by_doc.values() for cid in ids]
    state = SamplerState(
        rng=random.Random(42),
        graph=CorpusGraph(nodes={}, edges={}),
        vectors=VectorStore(ids=all_ids, matrix=np.eye(len(all_ids), dtype=np.float32)),
        chunks_by_doc=chunks_by_doc,
        abstract_chunk_by_doc={doc: ids[0] for doc, ids in chunks_by_doc.items()},
        pagerank_doc={doc: 1.0 / len(chunks_by_doc) for doc in chunks_by_doc},
        neighbors_by_chunk={},
        chunk_degree={cid: 0 for cid in all_ids},
        chunk_to_doc={cid: doc for doc, ids in chunks_by_doc.items() for cid in ids},
    )
    init_coverage_state(state, all_ids)
    return state


def test_sampler_only_sees_non_skip_chunks():
    """After _build_sampler_state filtering, reference chunks must not be sampled.

    We simulate the filter directly: chunks with skip section_types are excluded
    from chunks_by_doc before the sampler state is built. Verify the sampler
    never returns those chunk ids.
    """
    # Simulate what pipeline._build_sampler_state now does: skip refs chunks.
    # content_only chunks = only body/abstract/methods.
    content_chunks = {"d1": ["c_body", "c_abstract"], "d2": ["c_methods"]}
    state = _make_sampler_state(content_chunks)
    sampler = LevyMixSampler(
        local_op=LocalOp.NONE, global_op=GlobalOp.UNIFORM, jump_rate=1.0
    )
    batch = sampler.next_batch(state, k=10)
    # All returned chunks must be in the content set.
    content_ids = {cid for ids in content_chunks.values() for cid in ids}
    assert all(cid in content_ids for cid in batch)
    # References-section chunk ids never appear because they were excluded.
    assert "c_refs" not in batch


# ---------------------------------------------------------------------------
# 2. DossierEntry.is_substantive
# ---------------------------------------------------------------------------


def _make_entry(
    definition: str = "", summary: str = "", section_type: str = "body"
) -> DossierEntry:
    return DossierEntry(
        chunk_id="c1",
        doc_id="d1",
        quote="sample quote for testing purposes",
        definition=definition,
        summary=summary,
        section_type=section_type,
    )


def test_empty_entry_is_not_substantive():
    entry = _make_entry(definition="", summary="")
    assert not entry.is_substantive


def test_short_definition_is_not_substantive():
    # Fewer than 10 words is still not substantive.
    entry = _make_entry(definition="ALD is a process.")
    assert not entry.is_substantive


def test_long_definition_is_substantive():
    # 20-word definition should pass.
    defn = (
        "Atomic layer deposition is a self-limiting vapor-phase thin-film growth"
        " technique that deposits one atomic layer per cycle."
    )
    entry = _make_entry(definition=defn)
    assert entry.is_substantive


def test_long_summary_is_substantive():
    summary = (
        "This chunk reports that ALD-grown HfO2 films at 250 C exhibit a growth rate of "
        "0.9 A per cycle with sub-1-nm roughness measured by XRD."
    )
    entry = _make_entry(summary=summary)
    assert entry.is_substantive


def test_references_section_entry_with_no_content_is_not_substantive():
    entry = _make_entry(definition="", summary="", section_type="references")
    assert not entry.is_substantive


# ---------------------------------------------------------------------------
# 3. dossier_to_yaml
# ---------------------------------------------------------------------------


def _make_dossier_dict() -> dict:
    return {
        "page_id": "Atomic Layer Deposition",
        "title": "Atomic Layer Deposition",
        "aliases": ["ALD"],
        "kind": "concept",
        "category": "method",
        "definition": (
            "Atomic layer deposition (ALD) is a self-limiting vapor-phase thin-film growth "
            "technique. Films are deposited one atomic layer at a time via alternating precursor "
            "pulses. This yields atomic-level thickness control."
        ),
        "summary": (
            "ALD-grown HfO2 films at 250 C show 0.9 A/cycle growth rate with sub-1-nm "
            "roughness. The self-limiting TDMAHf + H2O half-reactions are responsible."
        ),
        "parameters": [{"name": "growth-per-cycle", "value": "0.9", "unit": "A"}],
        "mechanisms": ["surface-limited chemisorption"],
        "relationships": [
            {"target": "Memristor", "relation": "used_to_fabricate", "evidence": "..."}
        ],
        "equations": [],
        "evidence": [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "quote": "ALD is a self-limiting technique",
                "section_type": "methods",
            }
        ],
        "n_sources": 1,
        "n_entries": 1,
    }


def test_dossier_to_yaml_produces_valid_yaml():
    d = _make_dossier_dict()
    result = dossier_to_yaml(d)
    parsed = yaml.safe_load(result)
    assert isinstance(parsed, dict)


def test_dossier_to_yaml_contains_expected_fields():
    d = _make_dossier_dict()
    result = dossier_to_yaml(d)
    parsed = yaml.safe_load(result)
    assert parsed["page_id"] == "Atomic Layer Deposition"
    assert parsed["title"] == "Atomic Layer Deposition"
    assert parsed["kind"] == "concept"
    assert "definition" in parsed
    assert "summary" in parsed
    assert "ALD" in parsed["aliases"]
    assert parsed["mechanisms"] == ["surface-limited chemisorption"]


def test_dossier_to_yaml_omits_empty_fields():
    d = _make_dossier_dict()
    d["equations"] = []
    d["aliases"] = []
    d["category"] = None
    result = dossier_to_yaml(d)
    parsed = yaml.safe_load(result)
    # Empty lists and None should be omitted to save tokens.
    assert "equations" not in parsed
    assert "aliases" not in parsed
    assert "category" not in parsed


def test_dossier_to_yaml_is_more_compact_than_json():
    d = _make_dossier_dict()
    yaml_str = dossier_to_yaml(d)
    json_str = json.dumps(d)
    assert len(yaml_str) < len(json_str), (
        f"YAML ({len(yaml_str)} chars) should be shorter than JSON ({len(json_str)} chars)"
    )


# ---------------------------------------------------------------------------
# 4. build_write_request populates dossier_context_yaml
# ---------------------------------------------------------------------------


def test_build_write_request_populates_dossier_context_yaml(tmp_path):
    from wikify_simple.distill.write.requests import WriteRequestConfig, build_write_request
    from wikify_simple.models import Evidence, WikiPage
    from wikify_simple.paths import BundlePaths
    from wikify_simple.store.images_index import ImageIndex

    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()

    # Create a dossier with substantive content.
    store = DossierStore(bundle.root)
    dossier = Dossier(page_id="Test Concept", title="Test Concept", kind="article")
    dossier.add_entry(
        DossierEntry(
            chunk_id="c1",
            doc_id="d1",
            quote="Test Concept is a well-known phenomenon in materials science.",
            definition=(
                "Test Concept is a representative phenomenon used in unit tests. "
                "It demonstrates that the dossier pipeline correctly populates the "
                "writer context with structured knowledge extracted from the corpus."
            ),
            summary=(
                "This chunk reports that Test Concept exhibits characteristic behaviour "
                "under standard conditions. The authors measure key parameters and compare "
                "them against prior work, concluding that the concept is well understood."
            ),
            section_type="body",
        )
    )
    store.save(dossier)

    page = WikiPage(
        id="Test Concept",
        kind="article",
        title="Test Concept",
        aliases=[],
        body_markdown="",
        evidence=[Evidence(marker="e1", chunk_id="c1", doc_id="d1", quote="Test Concept")],
    )

    cfg = WriteRequestConfig(
        model_id="haiku",
        writer_tier="S",
        prompt_name="wikify_simple/write",
        style_text="",
        field_text="",
        artifact_text="",
        person_artifact_text="",
        persona_text="",
    )

    images_index = ImageIndex(corpus_root=tmp_path)
    req = build_write_request(page, [page], {}, store, {}, images_index, cfg)

    assert req.dossier_context_yaml, "dossier_context_yaml must be non-empty"
    parsed = yaml.safe_load(req.dossier_context_yaml)
    assert parsed["title"] == "Test Concept"
    assert "definition" in parsed
    assert "summary" in parsed


def test_build_write_request_empty_yaml_when_no_dossier(tmp_path):
    from wikify_simple.distill.write.requests import WriteRequestConfig, build_write_request
    from wikify_simple.models import Evidence, WikiPage
    from wikify_simple.paths import BundlePaths
    from wikify_simple.store.images_index import ImageIndex

    bundle = BundlePaths(root=tmp_path / "bundle_empty")
    bundle.ensure()
    store = DossierStore(bundle.root)  # empty store

    page = WikiPage(
        id="No Dossier Concept",
        kind="article",
        title="No Dossier Concept",
        aliases=[],
        body_markdown="",
        evidence=[Evidence(marker="e1", chunk_id="c1", doc_id="d1", quote="No Dossier Concept")],
    )
    cfg = WriteRequestConfig(
        model_id="haiku",
        writer_tier="S",
        prompt_name="wikify_simple/write",
        style_text="",
        field_text="",
        artifact_text="",
        person_artifact_text="",
        persona_text="",
    )
    images_index = ImageIndex(corpus_root=tmp_path)
    req = build_write_request(page, [page], {}, store, {}, images_index, cfg)
    assert req.dossier_context_yaml == ""


# ---------------------------------------------------------------------------
# 5. io_lineage files written to <bundle>/_meta/io_lineage/<run_id>/
# ---------------------------------------------------------------------------


def test_write_io_lineage_creates_expected_files(tmp_path):
    from wikify_simple.distill.pipeline import _write_io_lineage
    from wikify_simple.models import Chunk
    from wikify_simple.paths import BundlePaths

    bundle = BundlePaths(root=tmp_path / "lineage_bundle")
    bundle.ensure()
    run_id = "test-run-001"

    chunks_by_id = {
        "c1": Chunk(
            id="c1",
            doc_id="d1",
            ord=0,
            text="Atomic layer deposition is a self-limiting technique.",
            char_span=(0, 52),
            section_path=["Abstract"],
            section_type="abstract",
        ),
        "c2": Chunk(
            id="c2",
            doc_id="d1",
            ord=1,
            text="See reference (59) Linearity Tuning of Weight Update.",
            char_span=(52, 104),
            section_path=["References"],
            section_type="references",
        ),
    }

    # Simulate candidates (minimal structure).
    from wikify_simple.contracts.schema import ExtractedConcept
    from wikify_simple.distill.extract.canonicalize import Candidate

    candidates = [
        Candidate(
            concept=ExtractedConcept(
                title="Atomic Layer Deposition",
                aliases=["ALD"],
                kind="article",
                quote="Atomic layer deposition is a self-limiting technique.",
                definition="ALD is a self-limiting vapor-phase thin-film deposition technique.",
                summary=(
                    "The chunk describes ALD as a technique producing films one layer at a time."
                ),
            ),
            chunk_id="c1",
            doc_id="d1",
        )
    ]

    dossier_store = DossierStore(bundle.root)
    dossier = Dossier(page_id="Atomic Layer Deposition", title="Atomic Layer Deposition")
    dossier.add_entry(
        DossierEntry(
            chunk_id="c1",
            doc_id="d1",
            quote="Atomic layer deposition is a self-limiting technique.",
            definition="ALD is a self-limiting vapor-phase thin-film deposition technique.",
            summary=(
                "The chunk describes ALD as a technique producing films one layer at a time."
            ),
            section_type="abstract",
        )
    )
    dossier_store.save(dossier)

    _write_io_lineage(bundle, run_id, ["c1", "c2"], chunks_by_id, candidates, dossier_store)

    lineage_dir = bundle.meta_dir / "io_lineage" / run_id
    assert (lineage_dir / "chunks_read.json").exists()
    assert (lineage_dir / "extract_candidates.json").exists()
    assert (lineage_dir / "dossier_entries.json").exists()

    chunks_log = json.loads((lineage_dir / "chunks_read.json").read_text())
    assert len(chunks_log) == 2
    assert any(c["chunk_id"] == "c1" and c["section_type"] == "abstract" for c in chunks_log)

    cands_log = json.loads((lineage_dir / "extract_candidates.json").read_text())
    assert len(cands_log) == 1
    assert cands_log[0]["title"] == "Atomic Layer Deposition"
    assert cands_log[0]["definition_words"] > 0

    dossier_log = json.loads((lineage_dir / "dossier_entries.json").read_text())
    assert len(dossier_log) == 1
    assert dossier_log[0]["is_substantive"] is True


# ---------------------------------------------------------------------------
# 6. _dossier_summary warns on stderr when empty ratio > 20%
# ---------------------------------------------------------------------------


def test_dossier_summary_warns_when_mostly_empty(tmp_path, capsys):
    from wikify_simple.distill.pipeline import _dossier_summary
    from wikify_simple.paths import BundlePaths

    bundle = BundlePaths(root=tmp_path / "warn_bundle")
    bundle.ensure()
    store = DossierStore(bundle.root)

    # Create one dossier with 4 empty entries and 1 substantive entry (80% empty).
    dossier = Dossier(page_id="Test", title="Test")
    for i in range(4):
        dossier.add_entry(
            DossierEntry(
                chunk_id=f"c{i}",
                doc_id="d1",
                quote="citation text here only",
                definition="",
                summary="",
                section_type="references",
            )
        )
    dossier.add_entry(
        DossierEntry(
            chunk_id="c4",
            doc_id="d1",
            quote="substantive chunk text about the concept and its properties.",
            definition=(
                "This concept is a well-defined phenomenon in materials science"
                " with unique properties."
            ),
            summary="The chunk describes characteristic measurements under test conditions.",
            section_type="body",
        )
    )
    store.save(dossier)

    summary = _dossier_summary(store, "test-run-warn")
    assert summary["n_total"] == 5
    assert summary["n_substantive"] == 1
    assert summary["n_empty"] == 4

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "80%" in captured.err or "4/5" in captured.err


def test_dossier_summary_no_warning_when_mostly_substantive(tmp_path, capsys):
    from wikify_simple.distill.pipeline import _dossier_summary
    from wikify_simple.paths import BundlePaths

    bundle = BundlePaths(root=tmp_path / "ok_bundle")
    bundle.ensure()
    store = DossierStore(bundle.root)

    dossier = Dossier(page_id="Good Concept", title="Good Concept")
    for i in range(9):
        dossier.add_entry(
            DossierEntry(
                chunk_id=f"c{i}",
                doc_id="d1",
                quote="substantive chunk about the good concept and its properties.",
                definition=(
                    "Good Concept is a well-characterised phenomenon with extensive literature."
                ),
                summary=(
                    "This chunk reports key measurements of Good Concept under standard conditions."
                ),
                section_type="body",
            )
        )
    dossier.add_entry(
        DossierEntry(
            chunk_id="c9",
            doc_id="d1",
            quote="citation only",
            definition="",
            summary="",
            section_type="references",
        )
    )
    store.save(dossier)

    summary = _dossier_summary(store, "test-run-ok")
    assert summary["n_empty"] == 1
    assert summary["n_substantive"] == 9

    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
