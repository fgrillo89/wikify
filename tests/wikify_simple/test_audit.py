"""Per-bundle ``_audit.md`` writer (graphify item 6e)."""

from pathlib import Path

from wikify_simple.eval.audit import write_audit
from wikify_simple.eval.bundle import Bundle, Page
from wikify_simple.eval.bundle import Evidence as BundleEvidence


def _page(
    tmp_path: Path,
    pid: str,
    title: str,
    chunks: list[tuple[str, str]],
    *,
    confidences: list[dict] | None = None,
) -> Page:
    return Page(
        id=pid,
        kind="article",
        title=title,
        aliases=[],
        links=[],
        body_clean=f"{title} body.",
        evidence=[
            BundleEvidence(marker=f"e{i + 1}", chunk_id=ck, doc_id=doc, quote="q")
            for i, (ck, doc) in enumerate(chunks)
        ],
        path=tmp_path / f"{pid}.md",
        provenance={
            "confidence_scores": confidences or [],
            "confidence_mean": (
                sum(c["score"] for c in confidences) / len(confidences) if confidences else 1.0
            ),
        },
    )


def test_write_audit_emits_all_sections(tmp_path):
    pages = [
        _page(
            tmp_path,
            "p-a",
            "Photocatalysis",
            [("c1", "d1"), ("c2", "d2")],
            confidences=[
                {"label": "extracted", "score": 1.0},
                {"label": "ambiguous", "score": 0.3},
            ],
        ),
        _page(
            tmp_path,
            "p-b",
            "Memristor",
            [("c3", "d1"), ("c4", "d2")],
            confidences=[
                {"label": "extracted", "score": 0.9},
                {"label": "inferred", "score": 0.4},
            ],
        ),
        _page(tmp_path, "p-c", "TiO2", [("c5", "d1")]),
        _page(tmp_path, "p-d", "ALD", [("c6", "d2")]),
        _page(tmp_path, "p-e", "Spectroscopy", [("c7", "d3")]),
    ]
    bundle = Bundle(name="testbundle", root=tmp_path, pages=pages)
    metrics = {
        "M3_g_evidence": {"modularity": 0.42},
        "M3_g_links": {"modularity": 0.10},
    }

    out = write_audit(bundle, metrics)
    assert out.exists()
    text = out.read_text(encoding="utf-8")

    assert "# Audit" in text
    assert "## Overall" in text
    assert "g_evidence Q" in text
    assert "g_links Q" in text
    assert "Q gap" in text
    assert "## Top hub pages" in text
    assert "## Top communities" in text
    assert "## Low-confidence claims" in text
    # the ambiguous + low-score evidence on Photocatalysis must be flagged
    assert "Photocatalysis" in text
    # Memristor's score=0.4 inferred entry must also be flagged
    assert "Memristor" in text
