"""Tests for the shared wiki runtime services."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlmodel import select

from wikify.store.db import DatabaseManager
from wikify.store.models import Campaign, MaintenanceFinding, WikiPage
from wikify.wiki.builder import article_path, slugify, write_article
from wikify.wiki.runtime import (
    compare_runs,
    export_metrics,
    query_wiki,
    reconcile_state,
    run_campaign,
    run_maintain,
)


def _session_factory(tmp_path: Path):
    db = DatabaseManager(db_path=str(tmp_path / "runtime.db"))

    def _new_session():
        return db.session()

    return _new_session


def _patch_runtime_sessions(session_factory):
    return patch("wikify.wiki.runtime.get_session", side_effect=session_factory), patch(
        "wikify.wiki.observability.runs.get_session", side_effect=session_factory
    )


def test_reconcile_state_populates_wikipage_rows(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"
    article = article_path(wiki_dir, "concepts", "atomic_layer_deposition")
    write_article(
        path=article,
        title="Atomic Layer Deposition",
        content="ALD is a thin-film method.",
        sources=["paper-1"],
        topics=["method"],
        status="full",
        model="test-model",
        page_type="concept",
        domains=["materials"],
    )

    runtime_patch, telemetry_patch = _patch_runtime_sessions(session_factory)
    with runtime_patch, telemetry_patch:
        summary = reconcile_state(wiki_dir)

    assert summary["pages_seen"] == 1
    with session_factory() as session:
        row = session.exec(select(WikiPage)).first()
        assert row is not None
        assert row.slug == "atomic_layer_deposition"
        assert row.page_type == "concept"


def test_run_maintain_persists_findings(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"
    article = article_path(wiki_dir, "concepts", "atomic_layer_deposition")
    write_article(
        path=article,
        title="Atomic Layer Deposition",
        content="ALD references [[Missing Target]].\n\nWARNING unresolved conflict.",
        sources=[],
        topics=["method"],
        status="draft",
        model="test-model",
        page_type="concept",
        domains=["materials"],
    )

    runtime_patch, telemetry_patch = _patch_runtime_sessions(session_factory)
    with runtime_patch, telemetry_patch:
        summary = run_maintain(wiki_dir)

    assert summary["findings"] >= 3
    with session_factory() as session:
        findings = list(session.exec(select(MaintenanceFinding)).all())
    finding_types = {row.finding_type for row in findings}
    assert "broken_link" in finding_types
    assert "weak_support" in finding_types
    assert "contradiction" in finding_types


def test_export_and_compare_runs_write_metric_artifacts(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"
    article = article_path(wiki_dir, "concepts", "atomic_layer_deposition")
    write_article(
        path=article,
        title="Atomic Layer Deposition",
        content="ALD links to [[Hafnium Oxide]].",
        sources=["paper-1"],
        topics=["method"],
        status="full",
        model="test-model",
        page_type="concept",
        domains=["materials"],
    )

    runtime_patch, telemetry_patch = _patch_runtime_sessions(session_factory)
    with runtime_patch, telemetry_patch:
        reconcile_state(wiki_dir)
        export_payload = export_metrics(wiki_dir, limit=10)
        compare_payload = compare_runs(wiki_dir, limit=10)

    assert export_payload["run_count"] >= 1
    assert compare_payload["run_count"] >= 1
    assert (wiki_dir / "_meta" / "metrics" / "export.json").exists()
    assert (wiki_dir / "_meta" / "metrics" / "comparison.json").exists()


def test_query_wiki_can_promote_visible_query_page(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"
    article = article_path(wiki_dir, "concepts", "atomic_layer_deposition")
    write_article(
        path=article,
        title="Atomic Layer Deposition",
        content="ALD is a thin-film deposition method.",
        sources=["paper-1"],
        topics=["method"],
        status="full",
        model="test-model",
        page_type="concept",
        domains=["materials"],
    )

    runtime_patch, telemetry_patch = _patch_runtime_sessions(session_factory)
    with runtime_patch, telemetry_patch, patch(
        "wikify.wiki.runtime.complete",
        return_value="ALD is a thin-film deposition method used for conformal coatings.",
    ):
        result = query_wiki("What is ALD?", wiki_dir=wiki_dir, promote=True)

    assert result["answered"] is True
    assert result["promoted_path"]
    promoted = Path(result["promoted_path"])
    assert promoted.exists()
    assert promoted.parent.name == "articles"


def test_run_campaign_persists_campaign_summary(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"
    article = article_path(wiki_dir, "concepts", "atomic_layer_deposition")
    write_article(
        path=article,
        title="Atomic Layer Deposition",
        content="ALD supports conformal coatings for oxide films.",
        sources=["paper-1"],
        topics=["method"],
        status="full",
        model="test-model",
        page_type="concept",
        domains=["materials"],
    )

    runtime_patch, telemetry_patch = _patch_runtime_sessions(session_factory)
    with (
        runtime_patch,
        telemetry_patch,
        patch("wikify.wiki.runtime.complete", return_value="Campaign answer."),
        patch("wikify.wiki.epoch.run_epoch", return_value=object()),
    ):
        result = run_campaign("ALD for oxide films", wiki_dir=wiki_dir, epochs=2)

    assert result["answered"] is True
    with session_factory() as session:
        campaign = session.get(Campaign, slugify("ALD for oxide films"))
        assert campaign is not None
        assert campaign.epochs_run == 2
