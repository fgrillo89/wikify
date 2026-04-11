"""Tests for ingest.coupling.compute_coupling."""

from wikify_simple.ingest.coupling import compute_coupling
from wikify_simple.models import Document


def _doc(id_: str, cites_raw: list[str]) -> Document:
    return Document(
        id=id_,
        source_path=f"/tmp/{id_}.pdf",
        kind="pdf",
        title=id_,
        metadata={},
        markdown_path="",
        image_dir="",
        citations=[{"ord": i, "raw_text": t} for i, t in enumerate(cites_raw)],
    )


def test_coupling_above_threshold() -> None:
    shared = [
        "Smith 2000 Foundational Work",
        "Jones 2001 Follow Up",
        "Brown 2002 Deep Analysis",
        "Green 2003 Extension Study",
    ]
    a = _doc("A", shared + ["Unique A Ref"])
    b = _doc("B", shared + ["Unique B Ref"])
    c = _doc("C", ["Totally Different Paper"])
    result = compute_coupling([a, b, c], min_strength=3)
    assert "B" in result["A"]
    assert "A" in result["B"]
    assert result["C"] == []


def test_coupling_below_threshold_dropped() -> None:
    a = _doc("A", ["Ref X", "Ref Y"])
    b = _doc("B", ["Ref X", "Ref Y"])  # only 2 shared
    result = compute_coupling([a, b], min_strength=3)
    assert result["A"] == []
    assert result["B"] == []
