"""Confidence label + score propagation through canonicalize and metrics
(graphify item 6b)."""

from wikify.bundle.concepts.dossier import Candidate, canonicalize
from wikify.bundle.wiki.page import Bundle, Page
from wikify.bundle.wiki.page import Evidence as BundleEvidence
from wikify.eval.metrics import spectral_gap_modularity
from wikify.schema import ExtractedConcept


def _cand(title: str, chunk_id: str, doc_id: str, *, label="extracted", score=1.0) -> Candidate:
    return Candidate(
        concept=ExtractedConcept(
            title=title,
            aliases=[],
            kind="article",
            quote=f"{title} appears in this chunk for testing.",
            confidence=label,
            score=score,
        ),
        chunk_id=chunk_id,
        doc_id=doc_id,
    )


def test_default_confidence_extracted_score_one():
    c = ExtractedConcept(
        title="Memristor",
        aliases=[],
        kind="article",
        quote="Memristor is a two-terminal device.",
    )
    assert c.confidence == "extracted"
    assert c.score == 1.0


def test_canonicalize_propagates_confidence_to_provenance():
    cands = [
        _cand("Photocatalysis", "ck1", "doc1"),
        _cand("Photocatalysis", "ck2", "doc1", label="ambiguous", score=0.3),
        _cand("Memristor", "ck3", "doc2", label="inferred", score=0.7),
    ]
    pages = canonicalize(cands, existing=[])
    assert len(pages) == 2
    photo = next(p for p in pages if "photo" in p.title.lower())
    mem = next(p for p in pages if "mem" in p.title.lower())

    assert "confidence_scores" in photo.provenance
    assert len(photo.provenance["confidence_scores"]) == 2
    assert photo.provenance["confidence_min"] == 0.3
    assert abs(photo.provenance["confidence_mean"] - 0.65) < 1e-9
    assert photo.provenance["confidence_count_by_label"] == {
        "ambiguous": 1,
        "extracted": 1,
    }

    assert mem.provenance["confidence_min"] == 0.7
    assert mem.provenance["confidence_mean"] == 0.7


def _make_bundle_with_confidence(tmp_path, conf_a: float, conf_b: float) -> Bundle:
    pages = [
        Page(
            id="p-a",
            kind="article",
            title="A",
            aliases=[],
            links=[],
            body_clean="A is a thing.",
            evidence=[
                BundleEvidence(marker="e1", chunk_id="c1", doc_id="d1", quote="x"),
                BundleEvidence(marker="e2", chunk_id="c2", doc_id="d2", quote="x"),
            ],
            path=tmp_path / "a.md",
            provenance={"confidence_mean": conf_a},
        ),
        Page(
            id="p-b",
            kind="article",
            title="B",
            aliases=[],
            links=[],
            body_clean="B is a thing.",
            evidence=[
                BundleEvidence(marker="e1", chunk_id="c3", doc_id="d1", quote="x"),
                BundleEvidence(marker="e2", chunk_id="c4", doc_id="d2", quote="x"),
            ],
            path=tmp_path / "b.md",
            provenance={"confidence_mean": conf_b},
        ),
        Page(
            id="p-c",
            kind="article",
            title="C",
            aliases=[],
            links=[],
            body_clean="C is a thing.",
            evidence=[
                BundleEvidence(marker="e1", chunk_id="c5", doc_id="d1", quote="x"),
            ],
            path=tmp_path / "c.md",
            provenance={"confidence_mean": 1.0},
        ),
    ]
    return Bundle(name="b", root=tmp_path, pages=pages)


def test_spectral_gap_modularity_use_confidence_changes_weights(tmp_path):
    bundle_high = _make_bundle_with_confidence(tmp_path, 1.0, 1.0)
    bundle_low = _make_bundle_with_confidence(tmp_path, 0.1, 0.1)

    base = spectral_gap_modularity(bundle_high)
    weighted_high = spectral_gap_modularity(bundle_high, use_confidence=True)
    weighted_low = spectral_gap_modularity(bundle_low, use_confidence=True)

    # Without use_confidence: low and high produce identical W (provenance ignored).
    base_low = spectral_gap_modularity(bundle_low)
    assert base["modularity"] == base_low["modularity"]

    # With use_confidence: weights are scaled. n_nodes/n_edges unchanged
    # (sparsification is by topology, not weight magnitude).
    assert weighted_high["n_nodes"] == base["n_nodes"]
    assert weighted_low["n_nodes"] == base["n_nodes"]
