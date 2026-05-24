"""Tests for `wikify wiki ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.wikify.test_wiki_commit import _setup_validated  # noqa: E402
from wikify.cli import app

runner = CliRunner()


def test_wiki_list_empty(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    result = runner.invoke(app, ["wiki", "list", "--run", str(bundle)])
    assert result.exit_code == 0


def test_wiki_commit_then_list(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    result = runner.invoke(
        app,
        ["wiki", "commit", slug, "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["slug"] == slug
    listing = runner.invoke(
        app, ["wiki", "list", "--run", str(bundle.root), "--format", "json"]
    )
    assert listing.exit_code == 0
    items = json.loads(listing.output)["items"]
    assert any(it["slug"] == slug for it in items)


def test_wiki_commit_rejects_unvalidated(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app, ["wiki", "commit", "ald", "--run", str(bundle)]
    )
    assert result.exit_code != 0


def test_wiki_show_after_commit(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(
        app, ["wiki", "commit", slug, "--run", str(bundle.root)]
    )
    result = runner.invoke(
        app,
        ["wiki", "show", slug, "--run", str(bundle.root), "--full"],
    )
    assert result.exit_code == 0
    assert "Atomic Layer Deposition" in result.output


def test_wiki_find_text(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        ["wiki", "find", "atomic layer", "--run", str(bundle.root), "--text"],
    )
    assert result.exit_code == 0
    assert slug in result.output


def test_wiki_find_modes_default_and_text_alias(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    default = runner.invoke(
        app,
        [
            "wiki",
            "find",
            "atomic layer",
            "--run",
            str(bundle.root),
            "--format",
            "json",
        ],
    )
    assert default.exit_code == 0, default.output
    default_data = json.loads(default.output)
    assert default_data["mode"] == "hybrid"
    assert any(item["slug"] == slug for item in default_data["items"])

    text = runner.invoke(
        app,
        [
            "wiki",
            "find",
            "atomic layer",
            "--run",
            str(bundle.root),
            "--text",
            "--format",
            "json",
        ],
    )
    assert text.exit_code == 0, text.output
    text_data = json.loads(text.output)
    assert text_data["mode"] == "text"
    assert any(item["slug"] == slug for item in text_data["items"])


def test_wiki_build_indexes(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        ["wiki", "build", "indexes", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "path" in data


def test_wiki_build_vectors_populates_wiki_db_embeddings(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])

    result = runner.invoke(
        app,
        ["wiki", "build", "vectors", "--run", str(bundle.root), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    import sqlite3

    con = sqlite3.connect(bundle.sqlite_path)
    try:
        n_spaces = con.execute("SELECT COUNT(*) FROM wiki_embedding_spaces").fetchone()[0]
        n_embeddings = con.execute("SELECT COUNT(*) FROM wiki_embeddings").fetchone()[0]
    finally:
        con.close()
    assert n_spaces == 1
    assert n_embeddings == 1


def test_wiki_rebuild_happy_path(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])

    result = runner.invoke(
        app,
        ["wiki", "rebuild", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["run"] == str(bundle.root)
    assert data["skipped"] == []
    names = [s["step"] for s in data["steps"]]
    assert names == ["vectors", "indexes", "graph"]
    assert all(s["ok"] for s in data["steps"])
    assert all("duration_ms" in s for s in data["steps"])
    assert bundle.derived_vectors_path.exists()
    assert bundle.derived_index_path.exists()
    assert bundle.sqlite_path.exists()


def test_wiki_rebuild_skip(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])

    # Wipe wiki.db so we can prove `--skip graph` does not touch it.
    if bundle.sqlite_path.exists():
        bundle.sqlite_path.unlink()

    result = runner.invoke(
        app,
        [
            "wiki", "rebuild",
            "--run", str(bundle.root),
            "--skip", "graph",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["skipped"] == ["graph"]
    names = [s["step"] for s in data["steps"]]
    assert names == ["vectors", "indexes"]
    assert all(s["ok"] for s in data["steps"])
    # vectors internally rebuilds wiki.db; to prove `graph` was the skipped
    # step, assert the explicit graph step is absent from steps_done.
    assert "graph" not in names


def test_wiki_rebuild_propagates_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])

    from wikify.cli import wiki as wiki_cli

    def _boom(_bundle: object) -> None:
        raise RuntimeError("indexes blew up")

    original_steps = wiki_cli._REBUILD_STEPS
    patched_steps = tuple(
        (name, _boom if name == "indexes" else fn)
        for name, fn in original_steps
    )
    monkeypatch.setattr(wiki_cli, "_REBUILD_STEPS", patched_steps)

    result = runner.invoke(
        app,
        ["wiki", "rebuild", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"] == "rebuild_failed"
    assert payload["failed_step"] == "indexes"
    assert "indexes blew up" in payload["message"]
    # vectors ran and succeeded; graph never ran (short-circuit).
    step_names = [s["step"] for s in payload["steps"]]
    assert step_names == ["vectors", "indexes"]
    assert payload["steps"][0]["ok"] is True
    assert payload["steps"][1]["ok"] is False


def test_wiki_traverse_category_pages_from_navigation(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "run").mkdir(parents=True)
    (bundle_dir / "run" / "state.json").write_text(
        json.dumps({"run_id": "test", "corpus_path": "data/corpora/foo"}),
        encoding="utf-8",
    )
    articles_dir = bundle_dir / "wiki" / "articles"
    articles_dir.mkdir(parents=True)
    slug = "Atomic Layer Deposition"
    (articles_dir / f"{slug}.md").write_text(
        "---\nid: Atomic Layer Deposition\nkind: article\n"
        "title: Atomic Layer Deposition\n---\n\n# ALD\n\nBody.\n",
        encoding="utf-8",
    )
    derived_dir = bundle_dir / "derived"
    derived_dir.mkdir()
    (derived_dir / "navigation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "groups": [
                    {
                        "id": "methods",
                        "title": "Methods",
                        "description": "",
                        "page_ids": ["Atomic Layer Deposition"],
                        "children": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "wiki",
            "traverse",
            "category:methods",
            "--run",
            str(bundle_dir),
            "--to",
            "pages",
            "--format",
            "quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"page:{slug}" in result.output


def test_wiki_traverse_page_handles_round_trip_when_slug_differs_from_id(
    tmp_path: Path,
) -> None:
    """Page-typed traverse output must use file slug, not graph page id.

    `wiki show` resolves by filename. If `traverse ... --to links` emits
    `page:<page_id>` and `page_id` differs from the file slug, the
    pipeline `traverse ... --format quiet | xargs wiki show` breaks.
    """
    # Set up a minimal bundle by hand: state.json + two committed pages
    # where slug != frontmatter id.
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "run").mkdir(parents=True)
    (bundle_dir / "run" / "state.json").write_text(
        json.dumps({"run_id": "test", "corpus_path": "data/corpora/foo"}),
        encoding="utf-8",
    )
    articles_dir = bundle_dir / "wiki" / "articles"
    articles_dir.mkdir(parents=True)
    custom_id = "concept-ald"
    target_slug = "Atomic Layer Deposition"
    other_slug = "Memristor"
    (articles_dir / f"{target_slug}.md").write_text(
        f"---\nid: {custom_id}\nkind: article\n"
        f"title: Atomic Layer Deposition\n---\n\n# ALD\n\nBody.\n",
        encoding="utf-8",
    )
    (articles_dir / f"{other_slug}.md").write_text(
        "---\nid: Memristor\nkind: article\ntitle: Memristor\n"
        f"links: [{custom_id}]\n---\n\n# Memristor\n\nLinks to ALD.\n",
        encoding="utf-8",
    )

    # Build the graph.
    build = runner.invoke(
        app, ["wiki", "build", "graph", "--run", str(bundle_dir)]
    )
    assert build.exit_code == 0, build.output

    # Traverse: who links to the ALD page (via slug input)? Memristor.
    result = runner.invoke(
        app,
        [
            "wiki", "traverse", target_slug,
            "--run", str(bundle_dir),
            "--to", "linked-by",
            "--format", "quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    handles = [line.strip() for line in result.output.splitlines() if line.strip()]
    assert handles, f"expected at least one linked-by result, got: {result.output!r}"
    # Output must be the filename slug, not the frontmatter id.
    assert f"page:{other_slug}" in handles

    # Round-trip: every emitted handle must resolve via `wiki show`.
    for handle in handles:
        show_result = runner.invoke(
            app,
            ["wiki", "show", handle, "--run", str(bundle_dir)],
        )
        assert show_result.exit_code == 0, (handle, show_result.output)


def test_wiki_traverse_resolves_slug_to_page_id(tmp_path: Path) -> None:
    """Slug-passed-to-graph-keyed-by-id was returning empty. Regression test."""
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    runner.invoke(app, ["wiki", "build", "graph", "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        [
            "wiki", "traverse", slug,
            "--run", str(bundle.root),
            "--to", "evidence",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    # The committed page has one evidence chunk attached at fixture time.
    assert len(data["items"]) >= 1
    assert any("chunk_id" in item for item in data["items"])


def test_wiki_check(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app, ["wiki", "check", "--run", str(bundle.root), "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["articles"] == 1


def test_wiki_commit_lock_held_returns_exit_2(tmp_path: Path) -> None:
    """When the bundle lock is held by someone else, `wiki commit` exits 2."""
    from wikify.bundle.run.lock import acquire_lock

    bundle, slug = _setup_validated(tmp_path)
    # Pre-acquire the lock as another owner so commit_page contends.
    acquire_lock(bundle, owner="other-process", ttl_seconds=120)
    result = runner.invoke(
        app,
        ["wiki", "commit", slug, "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"] == "lock_held"
    assert payload["owner"] == "other-process"


def test_wiki_repl_find_show_and_list(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        [
            "wiki", "repl",
            "--run", str(bundle.root),
            "--prompt", "",
        ],
        input=(
            "list articles\n"
            "find --top-k 1 atomic layer\n"
            f"show {slug} --full\n"
            "exit\n"
        ),
    )
    assert result.exit_code == 0, result.output
    assert "ready bundle=" in result.output
    assert slug in result.output
    assert "Atomic Layer Deposition" in result.output


def test_wiki_repl_reports_user_errors_and_continues(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        [
            "wiki", "repl",
            "--run", str(bundle.root),
            "--prompt", "",
        ],
        input="bogus\nlist articles\nexit\n",
    )
    assert result.exit_code == 0
    assert "error: unknown command: bogus; type help" in result.stderr
    assert slug in result.output
