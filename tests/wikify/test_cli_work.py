"""Tests for `wikify work ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def _init_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    return bundle


def test_work_add_concept(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "add", "concept",
            "Atomic Layer Deposition",
            "--run", str(bundle),
            "--kind", "article",
            "--aliases", '["ALD"]',
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["slug"] == "atomic-layer-deposition"


def test_work_list_concepts(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(
        app, ["work", "add", "concept", "ALD", "--run", str(bundle)]
    )
    runner.invoke(
        app, ["work", "add", "concept", "CVD", "--run", str(bundle)]
    )
    result = runner.invoke(app, ["work", "list", "--run", str(bundle)])
    assert result.exit_code == 0
    assert "ald" in result.output
    assert "cvd" in result.output


def test_work_show(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(app, ["work", "show", "ald", "--run", str(bundle)])
    assert result.exit_code == 0
    assert "ALD" in result.output


def test_work_show_unknown_concept(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app, ["work", "show", "no-such", "--run", str(bundle)]
    )
    assert result.exit_code != 0


def test_work_claim_release_roundtrip(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    claim = runner.invoke(
        app,
        ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"],
    )
    assert claim.exit_code == 0
    assert "claimed ald" in claim.output

    release = runner.invoke(
        app,
        ["work", "release", "ald", "--run", str(bundle), "--owner", "a"],
    )
    assert release.exit_code == 0


def test_work_claim_contention_exits_2(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"]
    )
    result = runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "b"]
    )
    assert result.exit_code == 2


def test_work_release_non_owner_exits_2(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"]
    )
    result = runner.invoke(
        app, ["work", "release", "ald", "--run", str(bundle), "--owner", "b"]
    )
    assert result.exit_code == 2


def test_work_list_claims(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"]
    )
    result = runner.invoke(
        app, ["work", "list", "claims", "--run", str(bundle), "--format", "json"]
    )
    assert result.exit_code == 0
    items = json.loads(result.output)["items"]
    assert len(items) == 1
    assert items[0]["slug"] == "ald"


def test_work_add_evidence_from_records(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    records = tmp_path / "ev.jsonl"
    records.write_text(
        '{"chunk_id": "d1:001", "doc_id": "d1", "score": 0.9}\n'
        '{"chunk_id": "d1:002", "doc_id": "d1", "score": 0.7}\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle),
            "--records", str(records),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["appended"] == 2


def test_work_set_status(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app,
        ["work", "set", "ald", "--run", str(bundle), "--status", "needs_refine"],
    )
    assert result.exit_code == 0
    show = runner.invoke(
        app, ["work", "show", "ald", "--run", str(bundle), "--format", "json"]
    )
    assert json.loads(show.output)["front"]["status"] == "needs_refine"


def test_work_set_aliases(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app,
        [
            "work", "set", "ald",
            "--run", str(bundle),
            "--aliases", '["Atomic layer deposition", "ALD process"]',
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["front"]["aliases"] == [
        "Atomic layer deposition",
        "ALD process",
    ]


def test_work_tend_runs(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app, ["work", "tend", "--run", str(bundle), "--format", "json"]
    )
    assert result.exit_code == 0
    summary = json.loads(result.output)
    assert summary["concepts"] == 1
    assert "index_path" in summary


def test_work_tend_persists_seed_doc_handles(tmp_path: Path) -> None:
    """An extractor record carrying ``seed_doc_handles`` should survive
    ``work tend`` and land on the concept card."""
    from wikify.api import Bundle
    from wikify.bundle.work.card import load_card

    bundle_dir = _init_bundle(tmp_path)
    record = tmp_path / "concepts.jsonl"
    record.write_text(
        json.dumps(
            {
                "title": "Memristor",
                "kind": "article",
                "aliases": ["RRAM"],
                "seed_doc_handles": ["doc:abc12345", "doc:def67890"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runner.invoke(
        app,
        [
            "work", "add", "feedback", "concept",
            "--run", str(bundle_dir),
            "--record", str(record),
        ],
    )
    result = runner.invoke(app, ["work", "tend", "--run", str(bundle_dir)])
    assert result.exit_code == 0, result.output
    card = load_card(Bundle.open(bundle_dir), "memristor")
    assert card.front["seed_doc_handles"] == ["doc:abc12345", "doc:def67890"]


def test_work_cluster_concepts_by_doc_overlap(tmp_path: Path) -> None:
    """Concepts that share evidence docs cluster together; persons get
    their own cluster regardless of overlap."""
    from wikify.api import Bundle
    from wikify.bundle.work.card import create_concept
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    # Two articles with 5/6 docs overlap → cluster together
    a_slug, _ = create_concept(bundle, page_id="Concept A", kind="article")
    b_slug, _ = create_concept(bundle, page_id="Concept B", kind="article")
    # Unrelated article — distinct doc set → singleton
    c_slug, _ = create_concept(bundle, page_id="Concept C", kind="article")
    # Person — own cluster
    p_slug, _ = create_concept(bundle, page_id="Some Person", kind="person")

    a_docs = [f"doc_{i}" for i in range(6)]
    b_docs = [f"doc_{i}" for i in range(1, 7)]  # overlap doc_1..doc_5
    c_docs = [f"other_{i}" for i in range(6)]
    p_docs = [f"author_doc_{i}" for i in range(3)]
    append_evidence(
        bundle, a_slug,
        [EvidenceRecord(chunk_id=f"{d}__c0", doc_id=d) for d in a_docs],
    )
    append_evidence(
        bundle, b_slug,
        [EvidenceRecord(chunk_id=f"{d}__c0", doc_id=d) for d in b_docs],
    )
    append_evidence(
        bundle, c_slug,
        [EvidenceRecord(chunk_id=f"{d}__c0", doc_id=d) for d in c_docs],
    )
    append_evidence(
        bundle, p_slug,
        [EvidenceRecord(chunk_id=f"{d}__c0", doc_id=d) for d in p_docs],
    )

    result = runner.invoke(
        app,
        [
            "work", "cluster-concepts",
            "--run", str(bundle_dir),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    clusters = {tuple(sorted(c["slugs"])): c for c in payload["clusters"]}
    # A and B share 5/6 docs (Jaccard ≈ 5/7), should cluster.
    ab_keys = [k for k in clusters if a_slug in k and b_slug in k]
    assert ab_keys, f"A and B not clustered together: {clusters}"
    # C should be alone in its article cluster.
    c_keys = [k for k in clusters if c_slug in k]
    assert c_keys and len(c_keys[0]) == 1
    # Person cluster should be the person alone, marked kind=person.
    p_clusters = [c for c in payload["clusters"] if c["kind"] == "person"]
    assert p_clusters and p_clusters[0]["slugs"] == [p_slug]


def test_work_add_feedback(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    record = tmp_path / "fb.json"
    record.write_text('{"query": "How does ALD differ from CVD?"}', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "work", "add", "feedback", "query",
            "--run", str(bundle),
            "--record", str(record),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["kind"] == "query_feedback"
    assert data["appended"] == 1


# -------------------------------------------------------- build-evidence --from-ids


def _build_evidence_bundle(tmp_path: Path):
    """Init a bundle and a SQLite corpus with 4 chunks the vetter mode hits.

    Returns ``(bundle_path, corpus_path, chunk_ids)`` where ``chunk_ids``
    is a dict with named ids used across the tests.
    """
    from wikify.api import Corpus
    from wikify.bundle.work.card import create_concept
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents
    from wikify.models import Chunk, Document

    bundle = tmp_path / "bundle"
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus_root)],
    )

    doc = Document(
        id="paper_x", source_path="src/paper_x.md", kind="md", title="Paper X",
        metadata={}, markdown_path="markdown/paper_x.md",
        image_dir="images/paper_x/", n_chunks=4, n_tokens=200,
    )
    body = (
        "Atomic layer deposition (ALD) is a vapor-phase thin-film growth "
        "technique that uses sequential self-limiting surface reactions to "
        "deposit conformal films one monolayer at a time."
    )
    chunks = [
        Chunk(
            id="paper_x__c0000", doc_id="paper_x", ord=0,
            text=body, char_span=(0, len(body)), section_path=["Intro"],
            section_type="introduction",
        ),
        Chunk(
            id="paper_x__c0001", doc_id="paper_x", ord=1,
            text=body + " Additional methods discussion follows.",
            char_span=(0, len(body) + 50), section_path=["Methods"],
            section_type="methods",
        ),
        Chunk(
            id="paper_x__c0002", doc_id="paper_x", ord=2,
            text=body, char_span=(0, len(body)), section_path=["Boiler"],
            section_type="boilerplate",
            is_boilerplate=True,
        ),
        Chunk(
            id="paper_x__c0003", doc_id="paper_x", ord=3,
            text="short", char_span=(0, 5), section_path=["Body"],
            section_type="body",
        ),
    ]
    corpus = Corpus(root=corpus_root)
    corpus.ensure()
    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(store, [doc], {doc.id: chunks})
        store.fts_rebuild()
    finally:
        store.close()

    from wikify.api import Bundle as BundleApi
    create_concept(
        BundleApi.open(bundle), page_id="Atomic Layer Deposition", kind="article",
    )
    return bundle, corpus_root, {
        "ok_a": "paper_x__c0000",
        "ok_b": "paper_x__c0001",
        "boilerplate": "paper_x__c0002",
        "short": "paper_x__c0003",
    }


def test_build_evidence_from_ids_appends_valid(tmp_path: Path) -> None:
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", f"{ids['ok_a']},{ids['ok_b']}",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 2
    assert data["distinct_docs"] == 1
    stats = data["stats"]
    assert stats["ids_total"] == 2
    assert stats["appended"] == 2
    assert stats["rejected_not_found"] == 0
    assert stats["rejected_boilerplate"] == 0


def test_build_evidence_from_ids_rejects_boilerplate(tmp_path: Path) -> None:
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["boilerplate"],
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["error"] == "no_evidence"
    assert data["stats"]["rejected_boilerplate"] == 1


def test_build_evidence_from_ids_rejects_short(tmp_path: Path) -> None:
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["short"],
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["stats"]["rejected_short"] == 1


def test_build_evidence_from_ids_rejects_unknown(tmp_path: Path) -> None:
    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "no-such-chunk",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["stats"]["rejected_not_found"] == 1


def test_build_evidence_from_ids_rejects_already_committed(tmp_path: Path) -> None:
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    # First commit succeeds.
    first = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["ok_a"],
            "--format", "json",
        ],
    )
    assert first.exit_code == 0, first.output
    # Second commit of the same id is rejected as already committed.
    second = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["ok_a"],
            "--format", "json",
        ],
    )
    assert second.exit_code != 0, second.output
    data = json.loads(second.output)
    assert data["stats"]["rejected_already_committed"] == 1


def test_build_evidence_from_ids_only_commas_errors(tmp_path: Path) -> None:
    """`--from-ids ' , , '` reaches the from-ids branch but parses to []."""
    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", " , , ",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["error"] == "no_ids_provided"


def test_build_evidence_from_ids_mixed_valid_invalid(tmp_path: Path) -> None:
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids",
            f"{ids['ok_a']},{ids['boilerplate']},no-such,{ids['ok_b']},{ids['ok_a']}",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # ok_a deduped to 1; ok_b counted once; boilerplate + unknown rejected.
    assert data["appended"] == 2
    stats = data["stats"]
    assert stats["ids_total"] == 4  # dedupe of ok_a brings it down from 5
    assert stats["rejected_boilerplate"] == 1
    assert stats["rejected_not_found"] == 1
