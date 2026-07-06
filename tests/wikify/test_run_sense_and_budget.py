"""Phase-2 efficiency: ``run sense`` snapshot + derived budget spend (F11).

``run sense`` collapses the editor's five per-round reads into one call.
Spend is DERIVED from the call-event ledger at read time (not stored), so the
STOP-CHECK budget bound is always faithful and can never drift to a stale 0.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle
from wikify.bundle.run.cost import haiku_eq_for
from wikify.bundle.work.card import create_concept
from wikify.cli import app

runner = CliRunner()


def _init_bundle(tmp_path: Path, corpus: Path, target: int = 1_000_000) -> Path:
    bundle = tmp_path / "bundle"
    res = runner.invoke(app, [
        "run", "init", "--bundle", str(bundle), "--corpus", str(corpus),
        "--strategy", "investigate", "--target-haiku-eq", str(target),
    ])
    assert res.exit_code == 0, res.output
    return bundle


def _make_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(corpus / "wikify.db"))
    con.execute(
        "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER)"
    )
    for i, (cid, did) in enumerate([("c1", "d1"), ("c2", "d1"), ("c3", "d2")]):
        con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                    (cid, did, i, f"text {cid}", "abstract", 0))
    con.commit()
    con.close()
    return corpus


# --------------------------------------------------------------- budget (F11)

def _shown_spent(bundle: Path) -> int:
    res = runner.invoke(app, [
        "run", "show", "--run", str(bundle), "--full", "--format", "json",
    ])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)["budget"]["spent_haiku_eq"]


def test_record_call_reflected_in_derived_spend(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    bundle = _init_bundle(tmp_path, corpus)
    assert _shown_spent(bundle) == 0  # no calls yet
    res = runner.invoke(app, [
        "run", "record-call", "--run", str(bundle),
        "--role", "writer", "--model-id", "claude-sonnet-4-6", "--tier", "M",
        "--tokens-in", "1000", "--tokens-out", "100",
    ])
    assert res.exit_code == 0, res.output
    expected = int(round(haiku_eq_for("M", 1000, 100)))
    assert expected > 0
    assert _shown_spent(bundle) == expected


def test_record_calls_batch_reflected_in_derived_spend(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    bundle = _init_bundle(tmp_path, corpus)
    lines = "\n".join(json.dumps(r) for r in [
        {"role": "explorer", "model_id": "m", "tier": "M",
         "tokens_in": 2000, "tokens_out": 200, "stage": "explore"},
        {"role": "writer", "model_id": "m", "tier": "S",
         "tokens_in": 500, "tokens_out": 50, "stage": "write"},
    ])
    res = runner.invoke(
        app, ["run", "record-calls", "--run", str(bundle), "--from-stdin"],
        input=lines,
    )
    assert res.exit_code == 0, res.output
    expected = int(round(haiku_eq_for("M", 2000, 200) + haiku_eq_for("S", 500, 50)))
    assert _shown_spent(bundle) == expected


# --------------------------------------------------------------- run sense

def test_run_sense_snapshot_shape(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    bundle = _init_bundle(tmp_path, corpus, target=500_000)
    b = Bundle.open(bundle)
    create_concept(b, page_id="Memristor")
    runner.invoke(app, [
        "run", "record-call", "--run", str(bundle),
        "--role", "writer", "--model-id", "m", "--tier", "M",
        "--tokens-in", "1000", "--tokens-out", "100",
    ])

    res = runner.invoke(app, [
        "run", "sense", "--run", str(bundle), "--corpus", str(corpus), "--round", "2",
    ])
    assert res.exit_code == 0, res.output
    snap = json.loads(res.output)

    assert snap["ok"] is True
    assert snap["round"] == 2
    # Budget reflects the recorded call and computes remaining.
    assert snap["budget"]["spent_haiku_eq"] == int(round(haiku_eq_for("M", 1000, 100)))
    assert snap["budget"]["remaining_haiku_eq"] == 500_000 - snap["budget"]["spent_haiku_eq"]
    # Roster + bands present; the one concept is uncommitted.
    assert any(c["slug"] == "memristor" for c in snap["concepts"])
    assert snap["bands"].get("new", 0) >= 1
    # Coverage + data + committed-page sections present.
    assert snap["coverage"]["n_total"] == 3
    assert "n_points" in snap["data"]
    assert isinstance(snap["committed_pages"], list)


def test_run_sense_flags_committed_concepts(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    bundle = _init_bundle(tmp_path, corpus)
    b = Bundle.open(bundle)
    create_concept(b, page_id="Memristor")
    # Simulate a committed article page on disk.
    b.wiki_articles_dir.mkdir(parents=True, exist_ok=True)
    (b.wiki_articles_dir / "memristor.md").write_text(
        "---\nid: Memristor\nkind: article\n---\n\n# Memristor\n", encoding="utf-8"
    )
    res = runner.invoke(app, [
        "run", "sense", "--run", str(bundle), "--corpus", str(corpus),
    ])
    assert res.exit_code == 0, res.output
    snap = json.loads(res.output)
    mem = next(c for c in snap["concepts"] if c["slug"] == "memristor")
    assert mem["committed"] is True
    assert mem["band"] == "committed"
    assert any(p["slug"] == "memristor" for p in snap["committed_pages"])


def test_run_sense_surfaces_sizing_and_seed_starvation(tmp_path: Path) -> None:
    """A roster below the SEED floor must report ``seed_should_fire`` so a
    resuming editor cannot mistake a starved roster for a saturated one.
    Regression guard for the SEED/PERSON under-dispatch bug.
    """
    corpus = _make_corpus(tmp_path)
    bundle = _init_bundle(tmp_path, corpus)
    b = Bundle.open(bundle)
    create_concept(b, page_id="Memristor")

    res = runner.invoke(app, [
        "run", "sense", "--run", str(bundle), "--corpus", str(corpus),
    ])
    assert res.exit_code == 0, res.output
    snap = json.loads(res.output)

    # Deterministic sizing knobs are surfaced (not left for the editor to
    # re-derive from prose).
    sizing = snap["sizing"]
    assert sizing["n_docs"] >= 1
    for k in ("target_min", "expected_pages", "expected_people", "wave_size"):
        assert isinstance(sizing[k], int)

    # One concept, far below target_min -> the roster is starved, not saturated.
    waves = snap["waves"]
    assert snap["roster"]["active_concepts"] == 1
    assert waves["seed_should_fire"] is True
    assert waves["roster_saturated"] is False
    assert waves["seed_deficit"] == sizing["target_min"] - 1
    # No people committed and the topical roster is tiny -> person gate shut.
    assert snap["roster"]["n_people"] == 0
    assert waves["person_gate_open"] is False
