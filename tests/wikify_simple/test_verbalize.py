"""Verbalization: request/response fields + pipeline log append."""

from pathlib import Path

from wikify_simple.schema import (
    ExtractedConcept,
    ExtractRequest,
    ExtractResponse,
    OrchAction,
    OrchState,
    WriteEvidenceRef,
    WriteRequest,
    WriteResponse,
)
from wikify_simple.distill.pipeline import _append_verbalize
from wikify_simple.paths import BundlePaths


def _a_body(h2: list[str]) -> str:
    lead = (
        "Atomic Layer Deposition (ALD) is a self-limiting thin-film growth "
        "technique that enables atomic-scale thickness control. It has "
        "become the method of record for high-k gate dielectrics in modern "
        "CMOS and for conformal films in high-aspect-ratio topographies[^e1]. "
        "The technique relies on alternating, self-saturating surface "
        "reactions between a metal precursor and a co-reactant[^e2]. "
        "Because each half-cycle is self-limiting, thickness scales linearly "
        "with cycle count and is insensitive to modest flux variations."
    )
    sections = [f"## {label}\n\n{lead}\n\n{lead}" for label in h2]
    return (
        "# Atomic Layer Deposition\n\n"
        + lead
        + "\n\n"
        + "\n\n".join(sections)
        + "\n\n## References\n\n"
        '[^e1]: doc1_c1 (doc1) > "quote one"\n'
        '[^e2]: doc1_c2 (doc1) > "quote two"\n'
    )


def test_extract_request_carries_verbalize_flag() -> None:
    req = ExtractRequest(
        chunk_id="c1",
        chunk_text="some text about memristive switching",
        canonical_titles=[],
        prompt_template="wikify_simple/extract",
        model_id="haiku",
        tier="S",
        verbalize=True,
    )
    assert req.verbalize is True


def test_extract_request_verbalize_defaults_false() -> None:
    req = ExtractRequest(
        chunk_id="c1",
        chunk_text="x",
        canonical_titles=[],
        prompt_template="p",
        model_id="m",
        tier="S",
    )
    assert req.verbalize is False


def test_extract_response_reasoning_defaults_empty() -> None:
    resp = ExtractResponse(chunk_id="c1", concepts=[], tokens_in=0, tokens_out=0)
    assert resp.reasoning == ""


def test_extract_response_accepts_reasoning() -> None:
    resp = ExtractResponse(
        chunk_id="c1",
        concepts=[
            ExtractedConcept(
                title="Memristor",
                aliases=[],
                kind="article",
                quote="memristive switching",
            )
        ],
        tokens_in=10,
        tokens_out=5,
        reasoning="Kept the memristor concept; skipped the citations in the last paragraph.",
    )
    assert "memristor" in resp.reasoning.lower()


def test_write_request_carries_verbalize_flag() -> None:
    req = WriteRequest(
        page_id="Atomic Layer Deposition",
        page_kind="article",
        title="Atomic Layer Deposition",
        aliases=[],
        skeleton="",
        evidence=[WriteEvidenceRef(chunk_id="c1", doc_id="d1", quote="q")],
        prompt_template="wikify_simple/write",
        model_id="sonnet",
        tier="M",
        verbalize=True,
    )
    assert req.verbalize is True


def test_write_response_accepts_reasoning() -> None:
    body = _a_body(["Background", "Mechanism"])
    resp = WriteResponse(
        page_id="Atomic Layer Deposition",
        page_kind="article",
        body_markdown=body,
        used_markers=["e1", "e2"],
        tokens_in=800,
        tokens_out=1200,
        reasoning=(
            "Opened with self-limiting growth per the evidence; "
            "deferred the ALD-vs-CVD contrast."
        ),
    )
    assert resp.reasoning.startswith("Opened")


def test_orch_state_and_action_verbalize_roundtrip() -> None:
    state = OrchState(run_id="r1", n_pages=0, n_candidates=0, verbalize=True)
    assert state.verbalize is True
    action = OrchAction(
        name="pick_chunks",
        args={"chunk_ids": ["c1", "c2"], "reason": "target gap"},
        reasoning="Top gap chunk was c1; c2 is the nearest semantic neighbor.",
    )
    assert "gap" in action.reasoning.lower()


def test_append_verbalize_writes_jsonl_when_reasoning_non_empty(tmp_path: Path) -> None:
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    _append_verbalize(bundle, "r1", "write", "Atomic Layer Deposition", "picked mechanism section")
    lines = bundle.verbalize_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "picked mechanism" in lines[0]
    assert '"role": "write"' in lines[0]
    assert '"rid": "Atomic Layer Deposition"' in lines[0]
    assert '"run_id": "r1"' in lines[0]


def test_append_verbalize_no_file_when_reasoning_empty(tmp_path: Path) -> None:
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    _append_verbalize(bundle, "r1", "extract", "c1", "")
    assert not bundle.verbalize_log_path.exists()


def test_append_verbalize_appends_multiple_lines(tmp_path: Path) -> None:
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    _append_verbalize(bundle, "r1", "extract", "c1", "first reason")
    _append_verbalize(bundle, "r1", "extract", "c2", "second reason")
    _append_verbalize(bundle, "r1", "write", "Memristor", "third reason")
    lines = bundle.verbalize_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert "first reason" in lines[0]
    assert "second reason" in lines[1]
    assert "third reason" in lines[2]
