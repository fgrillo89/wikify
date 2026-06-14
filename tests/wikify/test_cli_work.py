"""Tests for `wikify work ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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
    assert json.loads(result.stdout)["appended"] == 2


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


def test_cluster_concepts_auto_picks_seeds_pre_evidence(tmp_path: Path) -> None:
    """Fresh bundle with concepts but no evidence: --by auto picks seeds."""
    from wikify.api import Bundle
    from wikify.bundle.work.card import create_concept, load_card, save_card

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    a_slug, _ = create_concept(bundle, page_id="Concept A", kind="article")
    b_slug, _ = create_concept(bundle, page_id="Concept B", kind="article")
    # Seed-handle overlap so seeds mode produces a non-empty cluster.
    for slug, handles in (
        (a_slug, ["doc:s1", "doc:s2", "doc:s3"]),
        (b_slug, ["doc:s2", "doc:s3", "doc:s4"]),
    ):
        card = load_card(bundle, slug)
        card.front["seed_doc_handles"] = handles
        save_card(bundle, slug, card)

    result = runner.invoke(
        app,
        [
            "work", "cluster-concepts",
            "--run", str(bundle_dir),
            "--by", "auto",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode_selected"] == "seeds"
    # A and B share 2/4 seed docs (Jaccard = 0.5 >= 0.15) → cluster together.
    grouped = [tuple(sorted(c["slugs"])) for c in payload["clusters"]]
    assert (a_slug, b_slug) in [tuple(sorted(g)) for g in grouped]


def test_cluster_concepts_auto_picks_evidence_post_evidence(
    tmp_path: Path,
) -> None:
    """Bundle with at least one slug carrying evidence: --by auto picks
    evidence."""
    from wikify.api import Bundle
    from wikify.bundle.work.card import create_concept
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    a_slug, _ = create_concept(bundle, page_id="Concept A", kind="article")
    b_slug, _ = create_concept(bundle, page_id="Concept B", kind="article")
    # Only one slug has evidence — still enough to flip auto into 'evidence'.
    append_evidence(
        bundle, a_slug,
        [EvidenceRecord(chunk_id=f"doc_{i}__c0", doc_id=f"doc_{i}") for i in range(3)],
    )

    result = runner.invoke(
        app,
        [
            "work", "cluster-concepts",
            "--run", str(bundle_dir),
            "--by", "auto",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode_selected"] == "evidence"
    # B has no evidence → empty doc set → singleton cluster.
    flat = {s for c in payload["clusters"] for s in c["slugs"]}
    assert a_slug in flat
    assert b_slug in flat


def test_cluster_concepts_auto_records_mode_in_output(tmp_path: Path) -> None:
    """JSON output includes the resolved mode under 'mode_selected'."""
    from wikify.api import Bundle
    from wikify.bundle.work.card import create_concept

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    create_concept(bundle, page_id="Concept A", kind="article")

    result = runner.invoke(
        app,
        [
            "work", "cluster-concepts",
            "--run", str(bundle_dir),
            "--by", "auto",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "mode_selected" in payload
    assert payload["mode_selected"] in ("seeds", "evidence")


def test_cluster_concepts_auto_empty_bundle(tmp_path: Path) -> None:
    """Empty bundle (no concepts at all): auto picks seeds, clusters empty."""
    bundle_dir = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "cluster-concepts",
            "--run", str(bundle_dir),
            "--by", "auto",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode_selected"] == "seeds"
    assert payload.get("clusters") in ([], None) or len(payload.get("clusters", [])) == 0


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
        Chunk(
            id="paper_x__c0004", doc_id="paper_x", ord=4,
            text=body, char_span=(0, len(body)),
            section_path=["References"], section_type="references",
        ),
        Chunk(
            id="paper_x__c0005", doc_id="paper_x", ord=5,
            text=body, char_span=(0, len(body)),
            section_path=["Figure"], section_type="figure",
        ),
        Chunk(
            id="paper_x__c0006", doc_id="paper_x", ord=6,
            text=body, char_span=(0, len(body)),
            section_path=["Caption"], section_type="caption",
        ),
        Chunk(
            id="paper_x__c0007", doc_id="paper_x", ord=7,
            text=body, char_span=(0, len(body)),
            section_path=["Acknowledgments"], section_type="acknowledgments",
        ),
        Chunk(
            id="paper_x__c0008", doc_id="paper_x", ord=8,
            text=body, char_span=(0, len(body)),
            section_path=["Appendix"], section_type="appendix",
        ),
        Chunk(
            id="paper_x__c0009", doc_id="paper_x", ord=9,
            text=body, char_span=(0, len(body)),
            section_path=["Table"], section_type="table",
        ),
        Chunk(
            id="paper_x__c0010", doc_id="paper_x", ord=10,
            text=body, char_span=(0, len(body)),
            section_path=["Body"], section_type="body",
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
        "references": "paper_x__c0004",
        "figure": "paper_x__c0005",
        "caption": "paper_x__c0006",
        "acknowledgments": "paper_x__c0007",
        "appendix": "paper_x__c0008",
        "table": "paper_x__c0009",
        "body": "paper_x__c0010",
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


def test_from_ids_rejects_references_section_chunk(tmp_path: Path) -> None:
    """A manually-supplied references chunk must be blocked structurally."""
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["references"],
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["error"] == "no_evidence"
    assert data["stats"]["rejected_excluded_kind"] == 1
    assert data["stats"]["appended"] == 0


@pytest.mark.parametrize(
    "kind_key",
    ["figure", "caption", "acknowledgments", "appendix", "table", "boilerplate"],
)
def test_from_ids_rejects_figure_caption_acknowledgments(
    tmp_path: Path, kind_key: str
) -> None:
    """Every structural-blacklist kind is rejected via --from-ids.

    ``boilerplate`` is included for completeness: the row has both
    ``is_boilerplate=True`` and ``section_type='boilerplate'``, so the
    earlier ``is_boilerplate`` guard wins and increments
    ``rejected_boilerplate`` rather than ``rejected_excluded_kind`` —
    asserted explicitly below to lock that ordering down.
    """
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids[kind_key],
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    stats = data["stats"]
    assert stats["appended"] == 0
    if kind_key == "boilerplate":
        assert stats["rejected_boilerplate"] == 1
        assert stats["rejected_excluded_kind"] == 0
    else:
        assert stats["rejected_excluded_kind"] == 1
        assert stats["rejected_boilerplate"] == 0


def test_from_ids_accepts_normal_body_chunk(tmp_path: Path) -> None:
    """Regression guard: ``section_type='body'`` is not in the blacklist."""
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["body"],
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    assert data["stats"]["rejected_excluded_kind"] == 0


def test_from_ids_json_stdin_basic_accept(tmp_path: Path) -> None:
    """JSON-via-stdin: supplied quote + score land on the EvidenceRecord."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import read_evidence

    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    quote = "Atomic layer deposition (ALD) is a vapor-phase thin-film growth"
    payload = json.dumps(
        [{"chunk_id": ids["ok_a"], "score": 0.95, "quote": quote}]
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    assert data["stats"]["rejected_quote_not_in_chunk"] == 0
    records = read_evidence(BundleApi.open(bundle), "atomic-layer-deposition")
    assert len(records) == 1
    assert records[0].quote == quote
    assert records[0].score == 0.95


def test_from_ids_json_stdin_quote_not_in_chunk_rejected(tmp_path: Path) -> None:
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    payload = json.dumps(
        [
            {
                "chunk_id": ids["ok_a"],
                "quote": "this string is not in the chunk",
            }
        ]
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["stats"]["rejected_quote_not_in_chunk"] == 1
    assert data["stats"]["appended"] == 0


def test_from_ids_json_stdin_missing_quote_falls_back_to_text400(
    tmp_path: Path,
) -> None:
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import read_evidence

    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    payload = json.dumps([{"chunk_id": ids["ok_a"]}])
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    records = read_evidence(BundleApi.open(bundle), "atomic-layer-deposition")
    assert len(records) == 1
    # Body chunk text starts with the canonical ALD sentence; truncated to 400.
    expected_prefix = (
        "Atomic layer deposition (ALD) is a vapor-phase thin-film growth"
    )
    assert records[0].quote.startswith(expected_prefix)
    assert len(records[0].quote) <= 400
    # Default score in JSON-stdin mode when score omitted is 1.0.
    assert records[0].score == 1.0


def test_from_ids_json_stdin_empty_list_errors(tmp_path: Path) -> None:
    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input="[]",
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["error"] == "no_ids_provided"


def test_from_ids_json_stdin_malformed_errors(tmp_path: Path) -> None:
    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input="not-json",
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["error"] == "bad_json"


def test_from_ids_json_stdin_non_list_errors(tmp_path: Path) -> None:
    """A JSON object (not a list) is rejected as bad_json."""
    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input='{"chunk_id": "x"}',
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["error"] == "bad_json"
    assert "expected JSON list" in data["message"]


def test_from_ids_csv_mode_still_works(tmp_path: Path) -> None:
    """Regression guard: bare CSV form continues to commit with score=1.0."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import read_evidence

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
    assert data["appended"] == 2
    assert data["stats"]["rejected_quote_not_in_chunk"] == 0
    records = read_evidence(BundleApi.open(bundle), "atomic-layer-deposition")
    for r in records:
        assert r.score == 1.0
        assert len(r.quote) <= 400


def test_from_ids_json_stdin_dedupes_first_wins(tmp_path: Path) -> None:
    """Duplicate chunk_ids in the JSON list: first occurrence wins."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import read_evidence

    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    first_quote = (
        "Atomic layer deposition (ALD) is a vapor-phase thin-film growth"
    )
    second_quote = "sequential self-limiting surface reactions"
    payload = json.dumps(
        [
            {"chunk_id": ids["ok_a"], "score": 0.95, "quote": first_quote},
            {"chunk_id": ids["ok_a"], "score": 0.50, "quote": second_quote},
        ]
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["appended"] == 1
    assert data["stats"]["ids_total"] == 1
    records = read_evidence(BundleApi.open(bundle), "atomic-layer-deposition")
    assert len(records) == 1
    assert records[0].quote == first_quote
    assert records[0].score == 0.95


def test_from_ids_archived_does_not_block_fresh_commit(tmp_path: Path) -> None:
    """An archived record in the ledger must not block a fresh active commit.

    The ledger is append-only-with-status-tracking: superseded records
    persist as ``status='archived'``. A vetter re-accepting the same
    chunk_id should produce a new active row, not be filtered out.
    """
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    # Seed the ledger with an archived record for ok_a.
    bundle_api = BundleApi.open(bundle)
    append_evidence(
        bundle_api,
        "atomic-layer-deposition",
        [
            EvidenceRecord(
                chunk_id=ids["ok_a"],
                doc_id="paper_x",
                quote="historical quote",
                score=1.0,
                status="archived",
                source="vetter",
            )
        ],
    )

    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", ids["ok_a"],
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    assert data["stats"]["rejected_already_committed"] == 0


def test_work_show_evidence_count_from_disk(tmp_path: Path) -> None:
    """``work show`` must reflect the on-disk evidence count, not the stale
    card frontmatter value. Regression for the display/cache bug where
    ``evidence_chunks: 0`` appeared even after ``build-evidence`` wrote records.
    """
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    # Directly append evidence without going through tend (which would refresh
    # the card). The card frontmatter still shows evidence_chunks=0.
    bundle_api = BundleApi.open(bundle)
    append_evidence(
        bundle_api,
        "ald",
        [
            EvidenceRecord(chunk_id="d1:001", doc_id="d1", score=0.9),
            EvidenceRecord(chunk_id="d1:002", doc_id="d1", score=0.8),
            EvidenceRecord(chunk_id="d2:001", doc_id="d2", score=0.7),
        ],
    )
    result = runner.invoke(
        app, ["work", "show", "ald", "--run", str(bundle), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Must show 3 active records and 2 distinct docs from disk, not the stale 0.
    assert data["front"]["evidence_chunks"] == 3
    assert data["front"]["evidence_docs"] == 2


def test_from_ids_nfkc_normalizes_unicode_confusables(tmp_path: Path) -> None:
    """A vetter quote using ASCII equivalents of superscript chars (e.g. Fe2+
    instead of Fe2+) must pass the verbatim check after NFKC normalization.
    Regression for the unicode confusable rejection that dropped one evidence
    record per chemistry/math slug.
    """
    from wikify.api import Corpus
    from wikify.bundle.work.card import create_concept
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents
    from wikify.models import Chunk, Document

    bundle_dir = tmp_path / "bundle"
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle_dir), "--corpus", str(corpus_root)],
    )

    # Chunk body uses Unicode superscript numerals and math glyphs.
    body_with_superscripts = (
        "The oxidation of iron produces Fe²⁺ ions in solution, "
        "which react with oxygen to form Fe₂O₃ compounds at "
        "elevated temperatures above 200 degrees Celsius or more."
    )
    doc = Document(
        id="paper_u", source_path="src/paper_u.md", kind="md",
        title="Iron Chemistry", metadata={}, markdown_path="markdown/paper_u.md",
        image_dir="images/paper_u/", n_chunks=1, n_tokens=50,
    )
    chunks = [
        Chunk(
            id="paper_u__c0000", doc_id="paper_u", ord=0,
            text=body_with_superscripts,
            char_span=(0, len(body_with_superscripts)),
            section_path=["Body"], section_type="body",
        )
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
    create_concept(BundleApi.open(bundle_dir), page_id="Iron Chemistry", kind="article")

    # Vetter submits quote using ASCII equivalents of the superscript chars.
    # "Fe2+" is the NFKC form of "Fe²⁺"; must pass after normalization.
    ascii_quote = "Fe2+ ions in solution"
    payload = json.dumps([{"chunk_id": "paper_u__c0000", "quote": ascii_quote}])
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "iron-chemistry",
            "--run", str(bundle_dir),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    assert data["stats"]["rejected_quote_not_in_chunk"] == 0


# ---- Friction B: OCR-whitespace-tolerant quote matching


def _build_ocr_corpus(tmp_path: Path):
    """Init bundle + corpus with one chunk whose text has OCR-inserted spaces."""
    from wikify.api import Corpus
    from wikify.bundle.work.card import create_concept
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents
    from wikify.models import Chunk, Document

    bundle_dir = tmp_path / "bundle"
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle_dir), "--corpus", str(corpus_root)],
    )

    # Chunk text as stored by OCR: "SiN x" (space inserted mid-token).
    ocr_body = (
        "Fe2+ ion migration through the SiN x dielectric layer was confirmed "
        "by impedance spectroscopy measurements at elevated temperatures."
    )
    doc = Document(
        id="paper_ocr", source_path="src/paper_ocr.md", kind="md",
        title="SiNx Synaptic Device", metadata={},
        markdown_path="markdown/paper_ocr.md",
        image_dir="images/paper_ocr/", n_chunks=1, n_tokens=40,
    )
    chunk = Chunk(
        id="paper_ocr__c0000", doc_id="paper_ocr", ord=0,
        text=ocr_body, char_span=(0, len(ocr_body)),
        section_path=["Body"], section_type="body",
    )
    corpus = Corpus(root=corpus_root)
    corpus.ensure()
    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(store, [doc], {doc.id: [chunk]})
        store.fts_rebuild()
    finally:
        store.close()

    from wikify.api import Bundle as BundleApi
    create_concept(
        BundleApi.open(bundle_dir), page_id="SiNx Synaptic Device", kind="article"
    )
    return bundle_dir, corpus_root


def test_from_ids_ocr_whitespace_recovered(tmp_path: Path) -> None:
    """A quote with collapsed OCR whitespace (SiNx vs SiN x) must commit
    and increment rejected_quote_then_whitespace_recovered.
    """
    bundle_dir, corpus_root = _build_ocr_corpus(tmp_path)
    # Writer quotes natural prose; OCR stored it with an extra space.
    natural_quote = "Fe2+ ion migration through the SiNx dielectric"
    payload = json.dumps(
        [{"chunk_id": "paper_ocr__c0000", "quote": natural_quote}]
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "sinx-synaptic-device",
            "--run", str(bundle_dir),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    stats = data["stats"]
    assert stats["rejected_quote_not_in_chunk"] == 0
    assert stats["rejected_quote_then_whitespace_recovered"] == 1


def test_from_ids_ocr_completely_different_content_rejects(tmp_path: Path) -> None:
    """A quote with completely different content must still be rejected even
    after whitespace collapsing.
    """
    bundle_dir, corpus_root = _build_ocr_corpus(tmp_path)
    unrelated_quote = "this content is entirely unrelated to the chunk text"
    payload = json.dumps(
        [{"chunk_id": "paper_ocr__c0000", "quote": unrelated_quote}]
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "sinx-synaptic-device",
            "--run", str(bundle_dir),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["stats"]["rejected_quote_not_in_chunk"] == 1
    assert data["stats"]["rejected_quote_then_whitespace_recovered"] == 0


# ---- Friction E: chunk-handle short-form acceptance


def test_from_ids_chunk_handle_short_form_resolves(tmp_path: Path) -> None:
    """Passing chunk:c0000 (suffix of paper_x__c0000) must commit successfully
    with the full id resolved from the corpus store.
    """
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import read_evidence

    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    # The fixture id is paper_x__c0000; its suffix after the last _ is c0000.
    short_handle = "chunk:c0000"
    payload = json.dumps([{"chunk_id": short_handle, "score": 0.88}])
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    assert data["stats"]["rejected_not_found"] == 0
    records = read_evidence(BundleApi.open(bundle), "atomic-layer-deposition")
    assert len(records) == 1
    # Committed under the full id, not the handle.
    assert records[0].chunk_id == ids["ok_a"]
    assert records[0].score == 0.88


def test_from_ids_short_quote_rejected_to_block_collision(tmp_path: Path) -> None:
    """A quote whose whitespace-stripped form is <12 chars must NOT pass
    Tier-2 even when it substring-matches the stripped chunk text; otherwise
    a 4-char quote like 'SiNx' could falsely match a different token region
    such as 'GeSiNxO' in an unrelated chunk.
    """
    bundle_dir, corpus_root = _build_ocr_corpus(tmp_path)
    short_collision_quote = "SiNx"  # 4 chars stripped, present in the chunk
    payload = json.dumps(
        [{"chunk_id": "paper_ocr__c0000", "quote": short_collision_quote}]
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "sinx-synaptic-device",
            "--run", str(bundle_dir),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["stats"]["rejected_quote_not_in_chunk"] == 1
    assert data["stats"]["rejected_quote_then_whitespace_recovered"] == 0


def test_from_ids_chunk_handle_short_form_like_wildcards_escaped(
    tmp_path: Path,
) -> None:
    """A chunk:&lt;suffix&gt; whose suffix contains SQLite LIKE wildcards (% or _)
    must be matched literally, not expanded as a wildcard pattern. Otherwise
    a suffix of '%' would match every chunk in the store.
    """
    bundle, corpus_root, ids = _build_evidence_bundle(tmp_path)
    # '%' is the SQL LIKE 'match any string' wildcard. Without escaping,
    # 'chunk:%' would match every row; with the ESCAPE clause it matches
    # nothing (no chunk_id literally ends in '_%').
    payload = json.dumps([{"chunk_id": "chunk:%", "score": 0.5}])
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle),
            "--corpus", str(corpus_root),
            "--from-ids", "@-",
            "--format", "json",
        ],
        input=payload,
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["stats"]["rejected_not_found"] == 1
