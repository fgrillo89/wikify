"""Tests for `wikify run ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app
from wikify.cli._helpers import EXIT_VALIDATION

runner = CliRunner()


def _bundle_dir(tmp_path: Path) -> Path:
    return tmp_path / "bundle"


def test_run_init_creates_bundle_layout(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    result = runner.invoke(
        app,
        [
            "run", "init",
            "--bundle", str(bundle),
            "--corpus", str(corpus),
            "--strategy", "baseline",
            "--target-haiku-eq", "1000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "run_id:" in result.output
    assert (bundle / "run" / "state.json").is_file()


def test_run_init_json_envelope(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    result = runner.invoke(
        app,
        [
            "run", "init",
            "--bundle", str(bundle),
            "--corpus", str(corpus),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["run_id"].startswith("run-")
    assert data["state_path"].endswith("state.json")


def test_run_init_rejects_already_initialised_bundle(tmp_path: Path) -> None:
    """A second run init against the same path must refuse, to avoid
    overwriting an existing run/state.json silently.
    """
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    first = runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    assert first.exit_code == 0
    second = runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    assert second.exit_code != 0


def test_run_show_after_init(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(app, ["run", "show", "--run", str(bundle)])
    assert result.exit_code == 0
    assert "run_id:" in result.output
    assert "status:" in result.output


def test_run_close_changes_status(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(
        app,
        ["run", "close", "--run", str(bundle), "--status", "completed"],
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    show = runner.invoke(
        app, ["run", "show", "--run", str(bundle), "--format", "json"]
    )
    assert json.loads(show.output)["status"] == "completed"


def test_run_record_call_appends_cost_event(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(
        app,
        [
            "run", "record-call",
            "--run", str(bundle),
            "--role", "writer",
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
            "--tokens-in", "100",
            "--tokens-out", "25",
            "--stage", "write",
            "--concept-id", "memristor",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    events = json.loads(
        runner.invoke(
            app,
            [
                "run", "list", "events",
                "--run", str(bundle),
                "--type", "call",
                "--format", "json",
            ],
        ).output
    )
    assert events[-1]["concept_id"] == "memristor"
    assert events[-1]["data"]["input_tokens"] == 100


def _init_bundle(tmp_path: Path) -> Path:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    return bundle


def _call_events(bundle: Path) -> list[dict]:
    out = runner.invoke(
        app,
        [
            "run", "list", "events",
            "--run", str(bundle),
            "--type", "call",
            "--tail", "0",
            "--format", "json",
        ],
    )
    return json.loads(out.output)


def test_record_calls_batch_happy(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    lines = [
        json.dumps(
            {
                "role": "writer",
                "model_id": "claude-sonnet-4-6",
                "tier": "M",
                "tokens_in": 100 + i,
                "tokens_out": 25 + i,
                "stage": "write",
                "concept_id": f"c-{i}",
            }
        )
        for i in range(5)
    ]
    stdin = "\n".join(lines) + "\n"
    result = runner.invoke(
        app,
        ["run", "record-calls", "--run", str(bundle), "--from-stdin"],
        input=stdin,
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["ok"] is True
    assert summary["appended"] == 5
    assert summary["rejected"] == 0
    assert summary["errors"] == []
    events = _call_events(bundle)
    assert len(events) == 5
    # Order preserved.
    assert [e["concept_id"] for e in events] == [f"c-{i}" for i in range(5)]
    assert events[0]["data"]["input_tokens"] == 100
    assert events[-1]["data"]["output_tokens"] == 29
    assert events[0]["data"]["role"] == "writer"


def test_record_calls_mixed_validity(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    good = [
        json.dumps(
            {
                "role": "writer",
                "model_id": "claude-sonnet-4-6",
                "tier": "M",
                "tokens_in": 10,
                "tokens_out": 5,
                "stage": "write",
            }
        )
        for _ in range(3)
    ]
    bad_missing = json.dumps(
        {
            "role": "writer",
            "model_id": "claude-sonnet-4-6",
            "tier": "M",
            "tokens_in": 10,
            # missing tokens_out and stage
        }
    )
    bad_json = "{not-json"
    stdin = "\n".join([good[0], bad_missing, good[1], bad_json, good[2]]) + "\n"
    result = runner.invoke(
        app,
        ["run", "record-calls", "--run", str(bundle), "--from-stdin"],
        input=stdin,
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["appended"] == 3
    assert summary["rejected"] == 2
    assert len(summary["errors"]) == 2
    assert any("line 2" in e for e in summary["errors"])
    assert any("line 4" in e for e in summary["errors"])
    events = _call_events(bundle)
    assert len(events) == 3


def test_record_calls_fail_fast(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    bad_first = "{not-json"
    good = json.dumps(
        {
            "role": "writer",
            "model_id": "claude-sonnet-4-6",
            "tier": "M",
            "tokens_in": 10,
            "tokens_out": 5,
            "stage": "write",
        }
    )
    stdin = "\n".join([bad_first, good, good]) + "\n"
    result = runner.invoke(
        app,
        [
            "run", "record-calls",
            "--run", str(bundle),
            "--from-stdin",
            "--fail-fast",
        ],
        input=stdin,
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["appended"] == 0
    assert summary["rejected"] == 1
    assert len(summary["errors"]) == 1
    events = _call_events(bundle)
    assert events == []


def test_record_calls_rejects_non_object_lines(tmp_path: Path) -> None:
    """A bare JSON string or array is valid JSON but not a call record."""
    bundle = _init_bundle(tmp_path)
    bare_string = '"just-a-string"'
    bare_array = "[1, 2, 3]"
    good = json.dumps(
        {
            "role": "writer",
            "model_id": "claude-sonnet-4-6",
            "tier": "M",
            "tokens_in": 10,
            "tokens_out": 5,
            "stage": "write",
        }
    )
    stdin = "\n".join([bare_string, bare_array, good]) + "\n"
    result = runner.invoke(
        app,
        [
            "run", "record-calls",
            "--run", str(bundle),
            "--from-stdin",
        ],
        input=stdin,
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["appended"] == 1
    assert summary["rejected"] == 2
    assert len(summary["errors"]) == 2
    events = _call_events(bundle)
    assert len(events) == 1


def test_run_lock_then_unlock(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    lock = runner.invoke(
        app, ["run", "lock", "--run", str(bundle), "--owner", "a"]
    )
    assert lock.exit_code == 0
    assert "locked by a" in lock.output

    # Second lock by a different owner returns exit 2.
    lock2 = runner.invoke(
        app, ["run", "lock", "--run", str(bundle), "--owner", "b"]
    )
    assert lock2.exit_code == 2

    unlock = runner.invoke(app, ["run", "unlock", "--run", str(bundle)])
    assert unlock.exit_code == 0


def test_run_list_events_tail(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    runner.invoke(
        app,
        ["run", "close", "--run", str(bundle), "--status", "completed"],
    )
    result = runner.invoke(
        app, ["run", "list", "events", "--run", str(bundle), "--tail", "5"]
    )
    assert result.exit_code == 0
    assert "stage_changed" in result.output
    assert "run_closed" in result.output


def test_run_show_no_bundle_context(tmp_path: Path, monkeypatch) -> None:
    """When no bundle is resolved, exit with a clear error envelope."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run", "show"])
    assert result.exit_code == 1


def test_run_close_warns_when_no_call_events(tmp_path: Path) -> None:
    """``run close`` must emit a WARNING to stderr when the event ledger
    contains no ``call`` events. The close must still succeed (exit 0).
    Regression for eval silently producing empty cost curves.
    """
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(
        app,
        ["run", "close", "--run", str(bundle), "--status", "completed"],
    )
    assert result.exit_code == 0, result.output
    assert "WARNING" in result.output
    assert "no agent call telemetry" in result.output


def test_run_close_no_warning_when_call_events_exist(tmp_path: Path) -> None:
    """``run close`` must NOT emit a warning when at least one call event
    exists on the timeline before close is invoked.
    """
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    runner.invoke(
        app,
        [
            "run", "record-call",
            "--run", str(bundle),
            "--role", "vetter",
            "--model-id", "claude-haiku-4-5",
            "--tier", "S",
            "--tokens-in", "5000",
            "--tokens-out", "300",
            "--stage", "evidence",
        ],
    )
    result = runner.invoke(
        app,
        ["run", "close", "--run", str(bundle), "--status", "completed"],
    )
    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output


# ---------------------------------------------------------------------------
# record-event: stdin / --data / validation tests
# ---------------------------------------------------------------------------


def _list_events_by_type(bundle: Path, type_: str) -> list[dict]:
    out = runner.invoke(
        app,
        [
            "run", "list", "events",
            "--run", str(bundle),
            "--type", type_,
            "--tail", "0",
            "--format", "json",
        ],
    )
    return json.loads(out.output)


def test_record_event_stdin_payload(tmp_path: Path) -> None:
    """When --from-stdin is passed and stdin carries a JSON object, use stdin."""
    bundle = _init_bundle(tmp_path)
    payload = json.dumps({"round": 1, "note": "seed"})
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "round_started",
            "--from-stdin",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    events = _list_events_by_type(bundle, "round_started")
    assert len(events) == 1
    assert events[0]["data"]["round"] == 1
    assert events[0]["data"]["note"] == "seed"


def test_record_event_data_flag_takes_precedence_over_stdin(tmp_path: Path) -> None:
    """When both --data and --from-stdin are supplied, --data wins and a
    WARNING is written to stderr (CliRunner merges stderr into output)."""
    bundle = _init_bundle(tmp_path)
    flag_payload = json.dumps({"round": 2, "source": "flag"})
    stdin_payload = json.dumps({"round": 99, "source": "stdin"})
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "round_started",
            "--data", flag_payload,
            "--from-stdin",
            "--format", "json",
        ],
        input=stdin_payload,
    )
    assert result.exit_code == 0, result.output
    # CliRunner merges stderr into output; extract the JSON line explicitly.
    assert "WARNING" in result.output
    assert "stdin was ignored" in result.output
    json_line = next(
        line for line in result.output.splitlines() if line.startswith("{")
    )
    data = json.loads(json_line)
    assert data["ok"] is True
    events = _list_events_by_type(bundle, "round_started")
    assert len(events) == 1
    assert events[0]["data"]["round"] == 2
    assert events[0]["data"]["source"] == "flag"


def test_record_event_missing_round_rejected(tmp_path: Path) -> None:
    """round_started without a 'round' field must exit non-zero."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "round_started",
            "--data", '{"note": "no round here"}',
        ],
    )
    assert result.exit_code != 0


def test_record_event_non_int_round_rejected(tmp_path: Path) -> None:
    """round_completed with round as a string must exit non-zero."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "round_completed",
            "--data", '{"round": "three"}',
        ],
    )
    assert result.exit_code != 0


def test_record_event_bool_round_rejected(tmp_path: Path) -> None:
    """round as a bool (bool is int subclass) must be rejected."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "round_started",
            "--data", '{"round": true}',
        ],
    )
    assert result.exit_code != 0


def test_record_event_pattern_dispatched_requires_round(tmp_path: Path) -> None:
    """pattern_dispatched without round is rejected."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "pattern_dispatched",
            "--data", '{"pattern": "P1"}',
        ],
    )
    assert result.exit_code != 0


def test_record_event_type_without_round_requirement_accepted(tmp_path: Path) -> None:
    """page_committed does not require 'round'; empty payload is fine."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "page_committed",
            "--data", '{"slug": "some-page"}',
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True


def test_record_event_stdin_non_object_rejected(tmp_path: Path) -> None:
    """--from-stdin with a JSON array (not object) must exit non-zero."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "page_committed",
            "--from-stdin",
        ],
        input='["not", "an", "object"]',
    )
    assert result.exit_code != 0


def test_record_event_no_stdin_flag_does_not_block(tmp_path: Path) -> None:
    """Without --from-stdin, record-event must not read stdin and must
    succeed with an empty payload (for event types that do not require round)."""
    bundle = _init_bundle(tmp_path)
    # No --from-stdin, no --data, no input= — must not block or hang.
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "page_committed",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True


def test_record_event_negative_round_rejected(tmp_path: Path) -> None:
    """A negative round value must be rejected even though it is an int."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "round_started",
            "--data", '{"round": -1}',
        ],
    )
    assert result.exit_code != 0


def test_record_event_evidence_added_no_round_accepted(tmp_path: Path) -> None:
    """evidence_added does not require a round field; omitting it is fine."""
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "run", "record-event",
            "--run", str(bundle),
            "--type", "evidence_added",
            "--data", '{"chunk_id": "abc123"}',
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True


# -------------------------------------------------------------- run metrics / stats


def _commit_article(bundle: Path, slug: str, title: str) -> None:
    """Write a minimal committed article page so list_committed_pages sees it."""
    art_dir = bundle / "wiki" / "articles"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / f"{slug}.md").write_text(
        f"---\nid: {title}\nkind: article\ntitle: {title}\n---\n\n# {title}\n\nBody.\n",
        encoding="utf-8",
    )


def test_run_metrics_appends_stats_line(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    _commit_article(bundle, "ald", "ALD")
    result = runner.invoke(
        app, ["run", "metrics", "--run", str(bundle), "--round", "0"]
    )
    assert result.exit_code == 0, result.output
    rec = json.loads(result.output)
    expected_keys = {
        "round", "n_committed_pages", "n_articles", "n_people", "band_counts",
        "chunk_coverage_ratio", "addressable_coverage_ratio", "n_data_points",
        "n_data_artifacts", "budget_spent_haiku_eq", "M1", "M3",
    }
    assert expected_keys <= set(rec)
    assert rec["round"] == 0
    assert rec["n_committed_pages"] == 1
    assert rec["n_articles"] == 1
    # No corpus supplied -> coverage + M1 are null, not fabricated.
    assert rec["chunk_coverage_ratio"] is None
    assert rec["M1"] is None
    # M3 is corpus-free (single page -> modularity 0.0).
    assert rec["M3"] == 0.0
    # No DATA wave ran -> zero data counts, and claims.db is NOT conjured
    # onto disk by the metrics call (opening a DataStore would create it).
    assert rec["n_data_points"] == 0
    assert rec["n_data_artifacts"] == 0
    assert not (bundle / "claims.db").exists()

    # A second round appends a second line.
    runner.invoke(app, ["run", "metrics", "--run", str(bundle), "--round", "1"])
    lines = [
        ln for ln in (bundle / "derived" / "stats.jsonl")
        .read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) == 2
    assert [json.loads(ln)["round"] for ln in lines] == [0, 1]


def test_run_stats_json_and_csv(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    _commit_article(bundle, "ald", "ALD")
    for rnd in ("0", "1"):
        runner.invoke(app, ["run", "metrics", "--run", str(bundle), "--round", rnd])

    js = runner.invoke(app, ["run", "stats", "--run", str(bundle), "--format", "json"])
    assert js.exit_code == 0, js.output
    series = json.loads(js.output)
    assert isinstance(series, list)
    assert [r["round"] for r in series] == [0, 1]

    csv = runner.invoke(app, ["run", "stats", "--run", str(bundle), "--format", "csv"])
    assert csv.exit_code == 0, csv.output
    rows = csv.output.strip().splitlines()
    assert rows[0] == "round,pages,chunk_cov,addr_cov,budget,M1,M3,n_artifacts"
    assert len(rows) == 3  # header + 2 rounds
    assert rows[1].split(",")[0] == "0"


def test_run_stats_dedupes_latest_per_round(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    # Record round 0 twice; readers keep one record (latest wins).
    runner.invoke(app, ["run", "metrics", "--run", str(bundle), "--round", "0"])
    runner.invoke(app, ["run", "metrics", "--run", str(bundle), "--round", "0"])
    js = runner.invoke(app, ["run", "stats", "--run", str(bundle), "--format", "json"])
    series = json.loads(js.output)
    assert [r["round"] for r in series] == [0]


def test_run_stats_plot_keeps_series(tmp_path: Path) -> None:
    """``--plot`` writes the svg AND still emits the series (csv rows here);
    the plot path is reported on a trailing JSON status line, not swallowed."""
    bundle = _init_bundle(tmp_path)
    _commit_article(bundle, "ald", "ALD")
    for rnd in ("0", "1"):
        runner.invoke(app, ["run", "metrics", "--run", str(bundle), "--round", rnd])
    out = tmp_path / "chart.svg"
    result = runner.invoke(
        app,
        ["run", "stats", "--run", str(bundle), "--format", "csv", "--plot", str(out)],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    # Series (header + 2 rounds) still present.
    assert lines[0] == "round,pages,chunk_cov,addr_cov,budget,M1,M3,n_artifacts"
    assert lines[1].split(",")[0] == "0"
    assert lines[2].split(",")[0] == "1"
    # Trailing status line carries the plot path.
    status = json.loads(lines[-1])
    assert status["plot"] == str(out)
    assert status["n_rounds"] == 2
    svg = out.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert "<polyline" in svg


def test_run_stats_plot_json_keeps_series(tmp_path: Path) -> None:
    """``--plot`` with the default json format emits the record list on the
    first line and the plot status on the second."""
    bundle = _init_bundle(tmp_path)
    _commit_article(bundle, "ald", "ALD")
    runner.invoke(app, ["run", "metrics", "--run", str(bundle), "--round", "0"])
    out = tmp_path / "chart.svg"
    result = runner.invoke(
        app, ["run", "stats", "--run", str(bundle), "--plot", str(out)]
    )
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    series = json.loads(lines[0])
    assert [r["round"] for r in series] == [0]
    assert json.loads(lines[-1])["plot"] == str(out)
    assert out.read_text(encoding="utf-8").startswith("<svg")


def test_run_stats_invalid_format_errors(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app, ["run", "stats", "--run", str(bundle), "--format", "xml"]
    )
    assert result.exit_code == EXIT_VALIDATION
    assert "bad_format" in result.output


def test_run_stats_reconstructs_from_round_completed(tmp_path: Path) -> None:
    """No stats.jsonl -> the series is rebuilt from round_completed events."""
    bundle = _init_bundle(tmp_path)
    runner.invoke(
        app,
        [
            "run", "record-event", "--run", str(bundle),
            "--type", "round_completed",
            "--data", '{"round": 2, "budget_used": 50, "n_committed_pages": 3}',
        ],
    )
    js = runner.invoke(app, ["run", "stats", "--run", str(bundle), "--format", "json"])
    series = json.loads(js.output)
    assert len(series) == 1
    assert series[0]["round"] == 2
    assert series[0]["budget_spent_haiku_eq"] == 50
    assert series[0]["n_committed_pages"] == 3
