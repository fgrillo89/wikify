"""Happy-path skill-CLI walk for the baseline workflow (structural smoke test).

Exercises the deterministic CLI families the `run-baseline.md` skill
drives: `session init/update/close`, `kg evidence`, `draft write-request`,
`validate write --session`, `bundle commit-page --validation`. The
write-subagent step is simulated by writing a canned structurally-valid
WriteResponse to scratch. Grounding passes because the synthesised body
quote is drawn from a real chunk_text substring.

What this test covers:
- Every documented artifact is produced: session.json, scratch draft,
  validation verdict, page markdown, _index.json, _wiki_graph.json,
  _run.json.
- The full `planned -> drafted -> validated -> committed` page-status
  transition per `reference/atoms.md`.
- The new commit-page precondition: page must be `validated` in
  session.pages before promotion.

What this test does NOT cover (scoped follow-up work):
- The extract stage (`stages.extract`) — this test injects one canned
  page id via `session update --patch`, bypassing the extract +
  canonicalisation pipeline.
- `stages.*.status` transitions to `done` — the CLI does not yet mutate
  the stages map automatically; that is a separate roadmap item.
- Retry / escalation / `failed` status arm per `run-baseline.md:116`.
- `_run.json` field-set parity with legacy `run_baseline()`
  (seed_doc_ids, split_initial, skipped_thin_pages, write_rejections).
- `_calls.jsonl` parity via a CostMeter rehydrated from subagent records.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wikify.cli import app
from wikify.ingest.pipeline import ingest_corpus
from wikify.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"
runner = CliRunner()


@pytest.fixture(scope="module")
def tiny_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    corpus_root = tmp_path_factory.mktemp("corpus-e2e")
    ingest_corpus(FIXTURE, corpus_root)
    return corpus_root


def _synthesise_valid_response(page_id: str, draft_path: Path, out_path: Path) -> None:
    """Build a structurally-valid, grounding-passing WriteResponse from the draft.

    Picks a real substring from draft.evidence_v2[0].chunk_text so that
    the body's `[^e1]:` reference definition carries a quote that is an
    actual substring of the source chunk — which is what the grounding
    check enforces.
    """
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    first_evidence = draft["evidence_v2"][0]
    chunk_text = first_evidence["chunk_text"]
    chunk_id = first_evidence["chunk_id"]
    doc_id = first_evidence["doc_id"]
    # Pick a safe quote: first 40 characters of chunk text, collapsed whitespace.
    quote = " ".join(chunk_text.split())[:40]

    filler = (
        "Atomic layer deposition (ALD) is a self-limiting vapor-phase technique. "
        "Films grow one atomic layer per cycle through alternating precursor pulses. "
    ) * 15
    body = (
        f"**{page_id}** is a self-limiting vapor-phase technique.[^e1]\n\n"
        f"{filler}\n\n"
        "## Mechanism\n\n"
        f"{filler}\n\n"
        "## Applications\n\n"
        f"{filler}\n\n"
        "## References\n\n"
        f'[^e1]: {chunk_id} ({doc_id}) > "{quote}"\n'
    )
    out_path.write_text(
        json.dumps(
            {
                "page_id": page_id,
                "page_kind": "article",
                "body_markdown": body,
                "used_markers": ["e1"],
                "tokens_in": 100,
                "tokens_out": 50,
            }
        ),
        encoding="utf-8",
    )


def test_baseline_skill_path_runs_cli_sequence(
    tmp_path: Path, tiny_corpus: Path
) -> None:
    bundle = tmp_path / "bundle"
    bundle_paths = BundlePaths(bundle)

    # 1. session init
    init = runner.invoke(
        app,
        [
            "session",
            "init",
            "--bundle",
            str(bundle),
            "--corpus",
            str(tiny_corpus),
            "--strategy",
            "baseline",
            "--budget-target",
            "500000",
        ],
    )
    assert init.exit_code == 0, init.output
    session_path = Path(json.loads(init.output)["session_path"])

    # 2. session update: seed one planned page. In a real run, an extract
    # pass populates this; the integration test uses a fixed id so the
    # downstream CLIs have a well-defined target.
    page_id = "Atomic Layer Deposition"
    upd = runner.invoke(
        app,
        [
            "session",
            "update",
            "--session",
            str(session_path),
            "--patch",
            json.dumps({"pages": [{"page_id": page_id, "status": "planned"}]}),
        ],
    )
    assert upd.exit_code == 0, upd.output

    # 3. kg evidence
    ev = runner.invoke(
        app,
        [
            "kg",
            "evidence",
            "--session",
            str(session_path),
            "--page-id",
            page_id,
            "--top-k",
            "3",
        ],
    )
    assert ev.exit_code == 0, ev.output
    chunk_ids = json.loads(ev.output)["chunk_ids"]
    assert chunk_ids, "expected evidence chunks from the tiny fixture"

    # 4. draft write-request
    draft = runner.invoke(
        app,
        [
            "draft",
            "write-request",
            "--session",
            str(session_path),
            "--page-id",
            page_id,
            "--chunk-ids",
            json.dumps(chunk_ids),
        ],
    )
    assert draft.exit_code == 0, draft.output
    draft_path = Path(json.loads(draft.output)["draft_path"])
    assert draft_path.exists()

    # 5. simulate the write subagent: a canned valid WriteResponse that
    # cites a real substring of the first evidence chunk, so the
    # grounding check passes.
    response_path = bundle_paths.scratch_dir / f"response-{page_id}.json"
    _synthesise_valid_response(page_id, draft_path, response_path)

    # 6. validate write — with --session so the page transitions
    # planned/drafted -> validated per atoms.md.
    val = runner.invoke(
        app,
        [
            "validate",
            "write",
            "--draft",
            str(draft_path),
            "--response",
            str(response_path),
            "--session",
            str(session_path),
        ],
    )
    val_payload = json.loads(val.output)
    verdict_path = Path(val_payload["validation_path"])
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert val.exit_code == 0, verdict
    assert verdict["ok"] is True, verdict
    assert verdict["structural_checks"]["pydantic"] == "pass"
    assert verdict["structural_checks"]["quote_in_body"] == "pass"
    assert verdict["structural_checks"]["quote_in_source"] == "pass"
    assert val_payload["session_patched"] is True

    # Session page entry should now be `validated`.
    patched = json.loads(session_path.read_text(encoding="utf-8"))
    page_entry = next(p for p in patched["pages"] if p["page_id"] == page_id)
    assert page_entry["status"] == "validated"
    assert page_entry["validation_path"] == str(verdict_path)

    # 7. bundle commit-page — now requires --validation AND session
    # status=validated.
    commit = runner.invoke(
        app,
        [
            "bundle",
            "commit-page",
            "--session",
            str(session_path),
            "--response",
            str(response_path),
            "--validation",
            str(verdict_path),
        ],
    )
    assert commit.exit_code == 0, commit.output
    commit_payload = json.loads(commit.output)
    assert Path(commit_payload["page_path"]).exists()
    assert Path(commit_payload["index_path"]).exists()
    assert Path(commit_payload["graph_path"]).exists()

    # 8. session close
    close = runner.invoke(
        app, ["session", "close", "--session", str(session_path)]
    )
    assert close.exit_code == 0, close.output
    close_payload = json.loads(close.output)
    assert close_payload["status"] == "completed"
    run_path = Path(close_payload["run_path"])
    assert run_path.exists()

    # --- Structural assertions on the final bundle ------------------------

    # Session file reflects the committed page.
    final_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert final_session["status"] == "completed"
    committed = [p for p in final_session["pages"] if p["status"] == "committed"]
    assert len(committed) == 1
    assert committed[0]["page_id"] == page_id

    # _run.json carries the v1 snapshot shape.
    snapshot = json.loads(run_path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == 1
    assert snapshot["strategy"] == "baseline"
    assert snapshot["status"] == "completed"
    assert snapshot["n_pages_committed"] == 1
    assert set(snapshot["stages"].keys()) == {"seed_selection", "extract", "write"}

    # Canonical page file exists under articles/.
    article_files = list(bundle_paths.articles_dir.glob("*.md"))
    assert len(article_files) == 1
    assert page_id in article_files[0].read_text(encoding="utf-8")

    # Indices exist.
    assert (bundle / "_index.json").exists()
    assert bundle_paths.graph_path.exists()


def test_baseline_skill_bundle_top_level_artifacts_match_legacy(
    tmp_path: Path, tiny_corpus: Path
) -> None:
    """Compare the top-level bundle artifact set produced by the skill
    path against the one produced by legacy ``run_baseline()`` on the
    same fixture.

    This is a contract lower bound — the skill path must at minimum
    emit every top-level artifact the legacy path emits, and must not
    silently introduce new ones the rest of the system doesn't know
    about. Deeper field-level parity for _run.json and _calls.jsonl is
    tracked as Tier 1 follow-up work.
    """
    # --- Legacy bundle ----------------------------------------------------
    from wikify.baselines.pipeline import BaselineConfig, run_baseline
    from wikify.cache import ExtractCache
    from wikify.distill.preload import preload_corpus
    from wikify.distill.strategy import build_strategy
    from wikify.meter import CostMeter

    from .fakes import FakeExtractor, FakeWriter
    from .test_baseline_pipeline import _ValidFakeWriter

    legacy_bundle = tmp_path / "legacy-bundle"
    legacy_paths = BundlePaths(legacy_bundle)
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=30_000.0,
        run_id="legacy-parity",
        events_path=legacy_paths.calls_path,
    )
    preloaded = preload_corpus(CorpusPaths(tiny_corpus))
    run_baseline(
        kg=preloaded.knowledge_graph,
        bundle=legacy_paths,
        strategy=build_strategy("balanced", seed=0),
        extractor=FakeExtractor(cache, meter),
        writer=_ValidFakeWriter(meter),
        meter=meter,
        budget_haiku_eq=30_000.0,
        preloaded=preloaded,
        config=BaselineConfig(min_evidence_chunks=1),
    )
    _ = FakeWriter  # keep the import visible so future parity work can diff.

    # --- Skill-path bundle ------------------------------------------------
    skill_bundle = tmp_path / "skill-bundle"
    skill_paths = BundlePaths(skill_bundle)
    init = runner.invoke(
        app,
        [
            "session",
            "init",
            "--bundle",
            str(skill_bundle),
            "--corpus",
            str(tiny_corpus),
            "--strategy",
            "baseline",
        ],
    )
    session_path = Path(json.loads(init.output)["session_path"])
    page_id = "ALD"
    runner.invoke(
        app,
        [
            "session",
            "update",
            "--session",
            str(session_path),
            "--patch",
            json.dumps({"pages": [{"page_id": page_id, "status": "planned"}]}),
        ],
    )
    ev = runner.invoke(
        app,
        ["kg", "evidence", "--session", str(session_path), "--page-id", page_id, "--top-k", "2"],
    )
    chunk_ids = json.loads(ev.output)["chunk_ids"]
    runner.invoke(
        app,
        [
            "draft",
            "write-request",
            "--session",
            str(session_path),
            "--page-id",
            page_id,
            "--chunk-ids",
            json.dumps(chunk_ids),
        ],
    )
    draft_path_2 = skill_paths.scratch_dir / f"draft-{page_id}.json"
    response_path = skill_paths.scratch_dir / f"response-{page_id}.json"
    _synthesise_valid_response(page_id, draft_path_2, response_path)
    val = runner.invoke(
        app,
        [
            "validate",
            "write",
            "--draft",
            str(draft_path_2),
            "--response",
            str(response_path),
            "--session",
            str(session_path),
        ],
    )
    verdict_path = json.loads(val.output)["validation_path"]
    runner.invoke(
        app,
        [
            "bundle",
            "commit-page",
            "--session",
            str(session_path),
            "--response",
            str(response_path),
            "--validation",
            verdict_path,
        ],
    )
    runner.invoke(app, ["session", "close", "--session", str(session_path)])

    # --- Compare top-level artifact sets ----------------------------------
    # Legacy artifacts that the skill-path should reproduce.
    legacy_top = {p.name for p in legacy_bundle.iterdir()}
    skill_top = {p.name for p in skill_bundle.iterdir()}

    # Every legacy artifact must exist on the skill path too. Skill-only
    # additions (_session, _scratch) are allowed because they are
    # explicitly part of the skill architecture (see schemas.md).
    legacy_required = {"_run.json", "_index.json", "_wiki_graph.json"}
    assert legacy_required.issubset(legacy_top), (
        f"legacy bundle missing expected artifacts: {legacy_required - legacy_top}"
    )
    missing_from_skill = legacy_required - skill_top
    assert not missing_from_skill, (
        f"skill bundle missing legacy artifacts: {missing_from_skill}"
    )

    # Articles directory must be present on both (baseline only writes
    # concept pages, not persons, on this fixture).
    assert (legacy_bundle / "articles").exists()
    assert (skill_bundle / "articles").exists()

    # Skill-only additions must match a documented allowlist — either
    # architectural additions (_session, _scratch per the schemas.md
    # contract) or bundle-side artifacts listed in paths.py that legacy
    # only writes conditionally (e.g., _wiki_vectors.* only appears
    # when rebuild_wiki_graph produces non-empty embeddings).
    skill_only = skill_top - legacy_top
    allowed_skill_only = {
        "_session",
        "_scratch",
        "_wiki_vectors.npz",
        "_wiki_vectors.ids.json",
        # Legacy-only conditional fields that the skill path also emits
        # now are intentionally allowed here; they converge when legacy
        # and skill paths share the same bundle state.
    }
    unexpected = skill_only - allowed_skill_only
    assert not unexpected, f"skill bundle introduced unexpected artifacts: {unexpected}"

    # --- _run.json overlay-field parity -----------------------------------
    legacy_run = json.loads((legacy_bundle / "_run.json").read_text(encoding="utf-8"))
    skill_run = json.loads((skill_bundle / "_run.json").read_text(encoding="utf-8"))

    # Every legacy `_run.json` field the skill path must now reproduce.
    # Values differ (canned fakes vs real fixture walk) so we assert
    # presence + type, not equality. Field-set parity is the named
    # Phase 5 deletion gate.
    overlay_fields = {
        # Baseline / pipeline overlay (Tier 1 item 2):
        "strategy": str,
        "mode": str,
        "iteration": str,
        "budget_target_haiku_eq": (int, float),
        "seed_doc_ids": list,
        "seed_chunks_read": list,
        "evidence_chunks_read": list,
        "split_initial": dict,
        "seed_extract_budget": (int, float),
        "baseline_write_fraction": (int, float),
        "min_evidence_chunks": (int, float),
        "skipped_thin_pages": list,
        "n_pages_written": int,
        "write_rejections": list,
        "timestamp_utc": str,
        # Meter-derived (Tier 1 item 3, matches CostMeter.snapshot shape):
        "run_id": str,
        "budget_used_haiku_eq": (int, float),
        "wall_seconds": (int, float),
        "by_role": dict,
        "by_tier": dict,
        "context": dict,
        "calls": int,  # count, not list
        "cache_hit_rate": (int, float),
    }
    missing_legacy = [k for k in overlay_fields if k not in legacy_run]
    missing_skill = [k for k in overlay_fields if k not in skill_run]
    assert not missing_legacy, f"legacy _run.json missing overlay fields: {missing_legacy}"
    assert not missing_skill, f"skill _run.json missing overlay fields: {missing_skill}"
    for key, expected_type in overlay_fields.items():
        assert isinstance(skill_run[key], expected_type), (
            f"skill _run.json[{key}] has wrong type: {type(skill_run[key])}"
        )

    # context sub-dict must carry the legacy shape.
    for subkey in ("used_max", "used_mean", "headroom_min", "headroom_mean"):
        assert subkey in skill_run["context"], (
            f"skill _run.json[context] missing {subkey}"
        )

    # _calls.jsonl must carry full CallRecord entries on the skill path.
    calls_jsonl = (skill_bundle / "_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert calls_jsonl, "skill path produced no _calls.jsonl records"
    expected_call_fields = {
        "role",
        "tier",
        "input_tokens",
        "output_tokens",
        "context_used",
        "context_cap",
        "wall_seconds",
        "cache_hit",
        "prompt_hash",
        "haiku_eq",
    }
    for line in calls_jsonl:
        rec = json.loads(line)
        missing = expected_call_fields - set(rec.keys())
        assert not missing, f"skill _calls.jsonl record missing fields: {missing}"
