"""Tests for ``wikify.bundle.work.maturity`` — composite maturity scoring."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle
from wikify.bundle.run.events import Event, append_event
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence
from wikify.bundle.work.maturity import (
    STENCILS,
    _detect_kinds,
    _diversity_bonus,
    _link_neighbours_chunk_sets,
    compute_maturity,
)
from wikify.cli import app

runner = CliRunner()


def _bundle(tmp_path: Path) -> Bundle:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "run").mkdir()
    b = Bundle(root=root)
    init_run(b, corpus_path="data/corpora/foo")
    return b


def _ev(chunk_id: str, doc_id: str, quote: str) -> EvidenceRecord:
    return EvidenceRecord(
        chunk_id=chunk_id, doc_id=doc_id, quote=quote, status="active"
    )


def test_detect_kinds_picks_up_definition_and_mechanism() -> None:
    records = [
        _ev("c1", "d1", "ALD is a thin-film deposition technique."),
        _ev("c2", "d1", "The reaction proceeds via self-limiting half-reactions."),
        _ev("c3", "d2", "ALD is used in semiconductor manufacturing."),
    ]
    kinds = _detect_kinds(records)
    assert "definition" in kinds
    assert "mechanism" in kinds
    assert "application" in kinds


def test_diversity_bonus_zero_when_single_doc() -> None:
    records = [_ev(f"c{i}", "d1", "q") for i in range(5)]
    assert _diversity_bonus(records) == 0.0


def test_diversity_bonus_positive_when_spread() -> None:
    records = [_ev(f"c{i}", f"d{i % 3}", "q") for i in range(6)]
    bonus = _diversity_bonus(records)
    assert bonus > 0.5  # 3 docs, even split -> HHI 1/3, bonus 2/3


def test_article_fails_gates_with_thin_evidence(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Memristor", kind="article")
    append_evidence(bundle, "memristor", [
        _ev("c1", "d1", "A memristor is a two-terminal device."),
    ])
    report = compute_maturity(bundle, "memristor")
    assert report.gates_passed is False
    # Has evidence but no round events -> growth_stalled gate fails -> stalled.
    assert report.band == "stalled"
    assert report.score == 0.0
    assert report.gates["has_definition_evidence"] is True
    assert report.gates["n_chunks_ge_8"] is False


def test_article_passes_gates_with_rich_evidence(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD", kind="article")
    # 9 chunks across 5 docs covering definition, mechanism, application.
    quotes = [
        "ALD is a thin-film deposition method.",  # definition
        "The reaction involves self-limiting half-reactions.",  # mechanism
        "ALD is used for gate dielectrics in transistors.",  # application
        "A typical cycle alternates two precursor pulses.",  # mechanism
        "Films are deposited at sub-nanometer thickness.",  # generic
        "It is applied to coat porous substrates.",  # application
        "ALD enables conformal coatings.",  # application
        "Growth proceeds via surface reactions.",  # mechanism
        "ALD is a chemical vapor process.",  # definition-like
    ]
    docs = ["d1", "d1", "d2", "d2", "d3", "d3", "d4", "d5", "d5"]
    append_evidence(
        bundle, "ald",
        [_ev(f"c{i}", docs[i], q) for i, q in enumerate(quotes)],
    )
    report = compute_maturity(bundle, "ald")
    assert report.gates_passed is True
    assert report.score > 0.0
    assert report.kind_stencil == "article-method"
    assert "definition" in report.kinds_present
    assert "mechanism" in report.kinds_present
    assert "application" in report.kinds_present


def test_stencil_override_changes_required_kinds(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Field Surveys", kind="article")
    # 8 chunks across 4 docs, no clear mechanism content but lots of variants
    quotes = [
        "Surveys are a kind of literature review.",  # variant + definition
        "Different variants of ALD include thermal and plasma.",  # variant
        "Used for benchmarking deposition methods.",  # application
        "There are types of surveys: systematic and ad hoc.",  # variant
        "Reviews are a class of academic article.",  # variant
        "Surveys are applied across many fields.",  # application
        "Used in industrial settings.",  # application
        "Such reviews are categorized by method.",  # variant
    ]
    docs = ["d1", "d2", "d3", "d4", "d1", "d2", "d3", "d4"]
    append_evidence(
        bundle, "field-surveys",
        [_ev(f"c{i}", docs[i], q) for i, q in enumerate(quotes)],
    )
    report_method = compute_maturity(
        bundle, "field-surveys", kind_stencil="article-method"
    )
    report_survey = compute_maturity(
        bundle, "field-surveys", kind_stencil="article-survey"
    )
    # article-survey needs {definition, variant, application} which all match.
    assert (
        report_survey.components["kinds_coverage"]
        >= report_method.components["kinds_coverage"]
    )


def test_growth_stalled_true_when_no_round_events(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="X", kind="article")
    append_evidence(bundle, "x", [_ev("c1", "d1", "x is a thing.")])
    report = compute_maturity(bundle, "x", current_round=0)
    assert report.growth_stalled is True


def test_growth_stalled_false_when_recent_evidence(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Y", kind="article")
    state = json.loads(bundle.state_path.read_text(encoding="utf-8"))
    run_id = state["run_id"]
    # Round 1 starts, evidence added in round 1.
    append_event(bundle, Event(
        run_id=run_id, type="round_started", actor="editor",
        data={"round": 1},
    ))
    append_event(bundle, Event(
        run_id=run_id, type="evidence_added", actor="explorer",
        concept_id="y", data={"n": 3},
    ))
    # Current round is 1 (just started).
    report = compute_maturity(bundle, "y", current_round=1)
    assert report.growth_stalled is False


def test_growth_stalled_true_after_two_rounds_no_evidence(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Z", kind="article")
    state = json.loads(bundle.state_path.read_text(encoding="utf-8"))
    run_id = state["run_id"]
    append_event(bundle, Event(
        run_id=run_id, type="round_started", actor="editor",
        data={"round": 1},
    ))
    append_event(bundle, Event(
        run_id=run_id, type="evidence_added", actor="explorer",
        concept_id="z", data={"n": 2},
    ))
    append_event(bundle, Event(
        run_id=run_id, type="round_started", actor="editor",
        data={"round": 2},
    ))
    append_event(bundle, Event(
        run_id=run_id, type="round_started", actor="editor",
        data={"round": 3},
    ))
    # No new evidence in rounds 2 or 3.
    report = compute_maturity(bundle, "z", current_round=3)
    assert report.growth_stalled is True


def test_person_uses_separate_rule(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(
        bundle, page_id="Suntola Tuomo", kind="person",
        aliases=["author:suntola_t"],
    )
    append_evidence(bundle, "suntola-tuomo", [
        _ev("c1", "d1", "Suntola proposed the ALD method in 1977."),
        _ev("c2", "d2", "Suntola and colleagues developed early ALD reactors."),
        _ev("c3", "d3", "Together with Antson, Suntola introduced ALE."),
    ])
    report = compute_maturity(bundle, "suntola-tuomo")
    assert report.kind == "person"
    assert report.gates_passed is True
    assert report.components["has_temporal_anchor"] > 0
    assert report.components["has_collaboration_evidence"] > 0


def test_person_contribution_gate_counts_present_tense(tmp_path: Path) -> None:
    # First-person / present-tense method statements must count as
    # contributions alongside past-tense forms (the gate regex matches
    # inflections, not just exact past-tense verbs).
    bundle = _bundle(tmp_path)
    create_concept(
        bundle, page_id="Jane Doe", kind="person",
        aliases=["author:doe_j"],
    )
    append_evidence(bundle, "jane-doe", [
        _ev("c1", "d1", "Here we demonstrate a flexible memristor."),
        _ev("c2", "d2", "We propose a new device architecture."),
        _ev("c3", "d3", "The method introduces a self-limiting growth step."),
    ])
    report = compute_maturity(bundle, "jane-doe")
    assert report.gates["n_quoted_contribution_chunks_ge_3"] is True
    assert report.gates_passed is True


def test_person_fails_without_author_metadata(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Anon", kind="person")  # no author: alias
    append_evidence(bundle, "anon", [
        _ev(f"c{i}", f"d{i}", "Anon developed something in 1999.")
        for i in range(3)
    ])
    report = compute_maturity(bundle, "anon")
    assert report.gates["author_metadata_present"] is False
    assert report.gates_passed is False


def test_stencils_define_three_kinds_each() -> None:
    for name, kinds in STENCILS.items():
        if name == "person":
            continue
        assert len(kinds) == 3


def test_band_returns_new_for_empty_concept(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Empty", kind="article")
    report = compute_maturity(bundle, "empty")
    assert report.band == "new"
    assert report.gates_passed is False


def test_band_returns_stalled_when_evidence_but_gates_fail(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Thin", kind="article")
    append_evidence(bundle, "thin", [_ev("c1", "d1", "Thin is a thing.")])
    report = compute_maturity(bundle, "thin")
    assert report.gates_passed is False
    assert report.growth_stalled is True
    assert report.band == "stalled"


def test_band_returns_growing_when_evidence_and_not_stalled(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Active", kind="article")
    append_evidence(bundle, "active", [_ev("c1", "d1", "Active is a thing.")])
    state = json.loads(bundle.state_path.read_text(encoding="utf-8"))
    run_id = state["run_id"]
    append_event(bundle, Event(
        run_id=run_id, type="round_started", actor="editor",
        data={"round": 1},
    ))
    append_event(bundle, Event(
        run_id=run_id, type="evidence_added", actor="explorer",
        concept_id="active", data={"n": 1},
    ))
    report = compute_maturity(bundle, "active", current_round=1)
    assert report.growth_stalled is False
    assert report.band == "growing"


def test_link_neighbours_chunk_sets_uses_correct_dst_type(
    tmp_path: Path,
) -> None:
    """Regression: dst_type literal must match wiki.db schema ('wiki_page')."""
    import sqlite3

    bundle = _bundle(tmp_path)
    db = bundle.sqlite_path
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE wiki_pages ("
        " page_id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL,"
        " title TEXT, kind TEXT, body TEXT, frontmatter_json TEXT,"
        " created_at TEXT, updated_at TEXT);"
        "CREATE TABLE wiki_evidence ("
        " page_id TEXT NOT NULL, marker TEXT NOT NULL,"
        " chunk_id TEXT, doc_id TEXT, quote TEXT,"
        " PRIMARY KEY (page_id, marker));"
        "CREATE TABLE wiki_edges ("
        " src_id TEXT NOT NULL, kind TEXT NOT NULL,"
        " dst_type TEXT NOT NULL, dst_id TEXT NOT NULL,"
        " meta_json TEXT, PRIMARY KEY (src_id, kind, dst_type, dst_id));"
    )
    con.execute(
        "INSERT INTO wiki_pages VALUES ('Memristor','memristor','M','article','','','', '')"
    )
    con.execute(
        "INSERT INTO wiki_pages VALUES "
        "('Resistive Switching','resistive-switching','R','article','','','', '')"
    )
    con.execute(
        "INSERT INTO wiki_edges VALUES "
        "('Memristor','links_to','wiki_page','Resistive Switching',NULL)"
    )
    con.executemany(
        "INSERT INTO wiki_evidence VALUES (?,?,?,?,?)",
        [
            ("Resistive Switching", "e1", "c1", "d1", "q"),
            ("Resistive Switching", "e2", "c2", "d1", "q"),
        ],
    )
    con.commit()
    con.close()
    neighbours = _link_neighbours_chunk_sets(bundle, "Memristor")
    assert neighbours == [{"c1", "c2"}], (
        "Expected one neighbour's chunk set; "
        "empty result means dst_type filter is broken."
    )


def test_cli_maturity_text_and_json(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="Memristor", kind="article")
    append_evidence(bundle, "memristor", [
        _ev("c1", "d1", "A memristor is a two-terminal device."),
    ])
    res = runner.invoke(
        app,
        ["work", "maturity", "memristor", "--run", str(bundle.root)],
    )
    assert res.exit_code == 0, res.output
    assert "memristor" in res.output

    res = runner.invoke(
        app,
        [
            "work", "maturity",
            "--all",
            "--run", str(bundle.root),
            "--format", "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload["threshold"] == 0.70
    assert len(payload["items"]) == 1


def test_terminal_status_drops_concept_from_roster(tmp_path: Path) -> None:
    """A card with a terminal status (merged/parked/dropped) reports that
    status as its band so the WRITE/GROW waves skip it, regardless of how
    much evidence it carries."""
    from wikify.bundle.work.card import create_concept, load_card, save_card

    b = _bundle(tmp_path)
    slug, _ = create_concept(b, page_id="Memristance", kind="article")
    # Give it enough evidence that it would otherwise be ready/growing.
    append_evidence(
        b, slug,
        [_ev(f"c{i}", f"d{i % 5}", "memristance is a property") for i in range(10)],
    )
    for status in ("merged", "parked", "dropped"):
        card = load_card(b, slug)
        card.front["status"] = status
        save_card(b, slug, card)
        report = compute_maturity(b, slug, current_round=3)
        assert report.band == status
        assert report.gates_passed is False
        assert report.score == 0.0
