"""Deterministic efficiency-proxy benchmark for the wikify-investigate loop.

Runs IDENTICALLY against any code state (pre-#96 BASE or post-#96 HEAD) and
reports the five deterministic proxies the efficiency work targets. Needs no
LLM: every probe drives a public wikify function on a fixed synthetic fixture
(the same fixtures the PR #96 regression tests use) and counts a structural
quantity. Reproducible — the spread across repeated runs is zero.

The five proxies (per the goal spec):
  A  writer ``draft check`` re-validation iterations forced by quote noise
  B1 canonical-id SQLite lookups per chunk envelope item (MCP data path)
  B2 data points silently rejected per short ``chunk:<hex>`` handle (harvest)
  C  editor SENSE CLI round-trips per round
  D  P5 residual chunks ranked at chunk vs doc level
  E  evidence-less concept stubs created by ``work tend``

Usage:  uv run python scripts/effq_bench.py --out result.json [--skip-ingest]

``--skip-ingest`` omits proxy D (the only probe that ingests a tiny corpus),
for environments where the ingest pipeline is unavailable.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import traceback
from pathlib import Path


def _safe(fn):
    try:
        return {"ok": True, **fn()}
    except Exception as exc:  # noqa: BLE001 - report, never abort the suite
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc().splitlines()[-3:]}


# --- C: editor SENSE CLI round-trips per round -----------------------------
def probe_sense_roundtrips() -> dict:
    import wikify.cli.run as runcli
    has_sense = hasattr(runcli, "cmd_sense")
    # Pre-#96 the editor needs five reads per SENSE: run show + work list +
    # work maturity --all + work coverage + data coverage. Post-#96 `run sense`
    # collapses them into one.
    return {"run_sense_present": has_sense,
            "sense_cli_roundtrips_per_round": 1 if has_sense else 5}


# --- A: writer draft-check re-validation iterations ------------------------
def probe_validator_iterations() -> dict:
    # Quotes as a writer copies them from the readable dossier, paired with the
    # raw chunk_text carrying the rendering noise the dossier strips.
    cases = [
        ("self-limiting growth", "ALD uses self-limiting growth steps.", True),
        ("endurance of 10 6 cycles was measured",
         "endurance \x01 of 10 6 cycles was measured", True),
        ("Memristors exhibit pinched hysteresis under bias.",
         "Memristors exhibit pinched hysteresis [1-3] under bias.", True),
        ("atomic layer deposition cycle proceeds via two half-reactions",
         "The Atomic Layer Deposition Cycle proceeds via two half-reactions.", True),
        ("ALD enables 5 nm copper interconnects",
         "ALD grows conformal oxide films one monolayer per cycle.", False),
    ]
    try:
        from wikify.bundle.draft.validator import _quote_is_grounded as ground
        mode = "normalized"
    except ImportError:
        # Pre-#96 grounding decision: strict verbatim substring (validator.py
        # `if body_quote not in chunk_text`).
        def ground(q: str, s: str) -> bool:
            return bool(q) and bool(s) and (q in s)
        mode = "strict_substring"
    forced = sum(1 for q, s, should in cases if should and not ground(q, s))
    fab_rejected = all(not ground(q, s) for q, s, should in cases if not should)
    return {"validator_mode": mode,
            "groundable_quotes": sum(1 for *_, sh in cases if sh),
            "forced_revalidation_iterations": forced,
            "fabricated_correctly_rejected": fab_rejected}


# --- B1: canonical-id exposure on the MCP chunk envelope --------------------
def probe_canonical_id() -> dict:
    from wikify.mcp.envelope import chunk_item
    from wikify.models import Chunk
    canon = "Atomic-Layer-Deposition_2301ec7574d8__c0007_ab12cd34"
    chunk = Chunk(id=canon, doc_id="Atomic-Layer-Deposition_2301ec7574d8", ord=7,
                  text="ALD proceeds via self-limiting half-reactions.",
                  char_span=(0, 46), section_path=["Process"], section_type="body")
    item = chunk_item(chunk)
    present = item.get("canonical_id") == canon
    return {"envelope_exposes_canonical_id": present,
            "sqlite_lookups_per_chunk_envelope": 0 if present else 1}


# --- B2: short-handle resolution on the data harvest path -------------------
def probe_harvest_resolution() -> dict:
    from wikify.api import Corpus
    from wikify.corpus import queries as q
    from wikify.data import harvest
    short = "chunk:62f9c659"
    canon = "In-Memory-Computing_88ba30b3ca12__c0004_62f9c659"

    class _FC:
        def __init__(self, text: str, doc_id: str) -> None:
            self.text, self.doc_id = text, doc_id

    reads: list[list[str]] = []

    def fake_read(corpus, ids):
        reads.append(list(ids))
        if list(ids) == [canon]:
            return [_FC("ON/OFF ratio of 10^5 measured.", "canon_doc")]
        return []

    orig_read = harvest.read_chunks_by_id
    orig_resolve = getattr(q, "resolve_chunk_id", None)
    harvest.read_chunks_by_id = fake_read
    if hasattr(q, "resolve_chunk_id"):
        q.resolve_chunk_id = lambda corpus, s: canon
    try:
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td) / "corpus"
            cdir.mkdir()
            text, _asset, _doc = harvest.source_text_for(
                Corpus(root=cdir), doc_id="d", chunk_id=short)
    finally:
        harvest.read_chunks_by_id = orig_read
        if orig_resolve is not None:
            q.resolve_chunk_id = orig_resolve
    grounded = bool(text)
    return {"short_handle_data_point_grounded": grounded,
            "data_points_rejected_per_short_handle": 0 if grounded else 1,
            "read_attempts": len(reads)}


# --- E: evidence-less concept stubs created by work tend -------------------
def probe_tend_stubs() -> dict:
    from wikify.api import Bundle
    from wikify.bundle.work.inbox import append_inbox
    from wikify.bundle.work.tend import tend_bundle
    # Eight single-chunk gap suggestions — matches the profiling run's eight
    # empty `new` cards that kept the SEED wave firing on phantom concepts.
    titles = ["Reservoir Computing", "Spiking Neural Network", "Synaptic Plasticity",
              "Conductive Filament", "Resistive Switching", "Charge Trapping",
              "Forming Voltage", "Compliance Current"]
    with tempfile.TemporaryDirectory() as td:
        bdir = Path(td) / "bundle"
        (bdir / "run").mkdir(parents=True)
        bundle = Bundle(root=bdir)
        for i, title in enumerate(titles):
            append_inbox(bundle, "concept_suggestions",
                         {"title": title, "origin": "gap_explorer", "chunk_id": f"c{i}"})
        created = tend_bundle(bundle)["concepts_created"]
    return {"gap_suggestions_submitted": len(titles),
            "evidence_less_stubs_created": created}


# --- F: deterministic seen-chunks dedup surface (judge path) ---------------
def probe_judge_dedup() -> dict:
    # Wiring the Haiku judge path: a cheap deterministic call returns the
    # already-judged (active) chunk set so the explorer skips re-judging it
    # across rounds, instead of relying on the LLM to read evidence.jsonl.
    try:
        from wikify.bundle.work.evidence import seen_chunk_ids
    except ImportError:
        return {"seen_chunks_surface_present": False,
                "seen_chunks_returned": 0, "archived_excluded": None}
    from wikify.api import Bundle
    from wikify.bundle.work.card import create_concept
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence
    with tempfile.TemporaryDirectory() as td:
        bdir = Path(td) / "bundle"
        (bdir / "run").mkdir(parents=True)
        bundle = Bundle(root=bdir)
        create_concept(bundle, page_id="ALD", slug="ald")
        append_evidence(bundle, "ald", [
            EvidenceRecord(chunk_id="c1", doc_id="d1", status="active"),
            EvidenceRecord(chunk_id="c2", doc_id="d1", status="active"),
            EvidenceRecord(chunk_id="c3", doc_id="d1", status="archived"),
        ])
        seen = seen_chunk_ids(bundle, "ald")
    return {"seen_chunks_surface_present": True,
            "seen_chunks_returned": len(seen),
            "archived_excluded": "c3" not in seen}


# --- G: grounding-decision parity across the two gates ---------------------
def probe_grounding_parity() -> dict:
    # F19 root fix: the draft validator and the data-harvest verifier must
    # ground a quote identically. Before unification the validator stripped
    # control chars + citation markers while the data gate only collapsed
    # whitespace, so a dossier-copied quote grounded at one gate and was
    # rejected at the other.
    from wikify.bundle.draft.validator import _quote_is_grounded as vground
    from wikify.data.verify import quote_in_source as dground
    cases = [
        ("endurance of 10 6 cycles was measured",
         "endurance \x01 of 10 6 cycles was measured", True),
        ("Memristors exhibit pinched hysteresis under bias.",
         "Memristors exhibit pinched hysteresis [1-3] under bias.", True),
        ("self-limiting growth", "ALD uses self-limiting growth steps.", True),
        ("ALD enables 5 nm copper interconnects",
         "ALD grows conformal oxide films one monolayer per cycle.", False),
    ]
    disagreements = sum(1 for q, s, _ in cases if vground(q, s) != dground(q, s))
    fab_rejected_both = all(not vground(q, s) and not dground(q, s)
                            for q, s, sh in cases if not sh)
    return {"gate_disagreements": disagreements,
            "fabricated_rejected_by_both": fab_rejected_both}


# --- H: OCR-mangled number gate (F8) ---------------------------------------
def probe_ocr_number_gate() -> dict:
    from wikify.data.models import DataPoint
    from wikify.data.verify import verify_point
    mangled = DataPoint(
        subject="film", property="resistivity",
        value_text="1 10 5 ohm cm", value_original="1 10 5 ohm cm",
        doc_id="d", grounding_quote="resistivity of 1 10 5 ohm cm",
        value_type="scalar").finalize()
    verify_point(mangled, chunk_text="we measured resistivity of 1 10 5 ohm cm here")
    legit = DataPoint(
        subject="film", property="gpc", value_text="1.1",
        value_original="1.1 A", doc_id="d",
        grounding_quote="GPC was 1.1 A", value_type="scalar").finalize()
    verify_point(legit, chunk_text="the GPC was 1.1 A in this process")
    return {"ocr_mangled_scalar_verified": mangled.verification_status == "verified",
            "legit_scalar_verified": legit.verification_status == "verified"}


# --- I: empty-body evidence dropped at draft build (F18) -------------------
def probe_empty_body_evidence() -> dict:
    try:
        from wikify.bundle.draft.builder import _drop_empty_body_evidence
    except ImportError:
        return {"empty_body_filter_present": False, "kept": None, "dropped": None}

    class _Rec:
        def __init__(self, cid): self.chunk_id = cid
        doc_id = "d"

    class _Chunk:
        def __init__(self, text): self.text = text
    active = [_Rec("c1"), _Rec("c2"), _Rec("c3"), _Rec("c4")]
    fetched = {"c1": _Chunk("real prose body"), "c2": _Chunk("   "),
               "c4": _Chunk("more prose")}  # c2 whitespace, c3 unresolved
    usable, dropped = _drop_empty_body_evidence(active, fetched)
    return {"empty_body_filter_present": True,
            "kept": len(usable), "dropped": dropped}


# --- D: P5 chunk-vs-doc ranking granularity --------------------------------
def probe_pagerank_granularity() -> dict:
    from wikify.corpus import queries
    from wikify.corpus.chunks import all_chunks
    from wikify.ingest.pipeline import ingest_corpus
    filler = " ".join(["word"] * 30)
    docs = [
        ("a.md", "Alpha",
         "Atomic layer deposition of HfO2 yields uniform films via self-limiting reactions."),
        ("b.md", "Beta",
         "Memristors exhibit resistive switching through conductive filament formation."),
        ("c.md", "Gamma",
         "Neuromorphic computing emulates synaptic plasticity with analog devices."),
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "sources"
        src.mkdir()
        for name, title, body in docs:
            (src / name).write_text(f"# {title}\n\n{body} {filler}\n", encoding="utf-8")
        corpus = ingest_corpus(src, root / "corpus", max_workers=1)
        result = queries.find(corpus, query="", by="chunk", rank="pagerank", top_k=5)
        n_chunks = len(list(all_chunks(corpus)))
    is_chunk = result.get("kind") == "chunks"
    chunk_rows = sum(1 for r in result.get("rows", [])
                     if r.get("id") and "doc_id" in r) if is_chunk else 0
    return {"p5_rank_kind": result.get("kind"),
            "p5_ranks_at_chunk_level": is_chunk,
            "p5_chunk_level_items": chunk_rows,
            "corpus_n_chunks": n_chunks}


PROBES = {
    "C_sense_roundtrips": probe_sense_roundtrips,
    "A_validator_iterations": probe_validator_iterations,
    "B1_canonical_id": probe_canonical_id,
    "B2_harvest_resolution": probe_harvest_resolution,
    "E_tend_stubs": probe_tend_stubs,
    "F_judge_dedup": probe_judge_dedup,
    "G_grounding_parity": probe_grounding_parity,
    "H_ocr_number_gate": probe_ocr_number_gate,
    "I_empty_body_evidence": probe_empty_body_evidence,
    "D_pagerank_granularity": probe_pagerank_granularity,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-ingest", action="store_true",
                    help="omit proxy D (the corpus-ingest probe)")
    args = ap.parse_args()

    import wikify  # noqa: F401 - resolve package + expose version below

    results: dict[str, dict] = {}
    t0 = time.time()
    for name, fn in PROBES.items():
        if name == "D_pagerank_granularity" and args.skip_ingest:
            results[name] = {"ok": False, "error": "skipped (--skip-ingest)"}
            continue
        results[name] = _safe(fn)
    elapsed = round(time.time() - t0, 3)

    sense = results["C_sense_roundtrips"]
    out = {
        "code_state": "HEAD" if sense.get("run_sense_present") else "BASE",
        "wikify_path": getattr(wikify, "__file__", "?"),
        "harness_wall_seconds": elapsed,
        "proxies": results,
    }
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
