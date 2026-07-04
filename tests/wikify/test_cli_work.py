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


def test_resolve_doc_id_skips_malformed_handle() -> None:
    """A non-string seed handle (e.g. a hand-edited notebook seed_doc)
    resolves to None instead of raising AttributeError, so the seed phase
    skips it rather than crashing the gather."""
    from wikify.cli.work import _resolve_doc_id

    assert _resolve_doc_id(None, 123) is None
    assert _resolve_doc_id(None, None) is None
    assert _resolve_doc_id(None, {"doc": "x"}) is None


def test_build_evidence_person_unknown_author_is_graceful(tmp_path: Path) -> None:
    """A person card whose `author:` alias is not in the corpus graph must
    not crash build-evidence: the author-seed traversal is skipped (the
    narrowed except catches what resolve_author_key raises for an absent
    author) and the gather proceeds from the notebook seed. Exercises the
    person author-seed branch end to end."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.card import create_concept

    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    create_concept(
        BundleApi.open(bundle), page_id="Nobody Author", kind="person",
        aliases=["author:nobody_x"],
    )
    runner.invoke(
        app,
        [
            "work", "notebook-init", "nobody-author", "--kind", "person",
            "--seed-docs", '["doc:paper_x"]', "--run", str(bundle),
        ],
    )
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "nobody-author",
            "--run", str(bundle), "--corpus", str(corpus_root),
            "--target", "3", "--format", "json",
        ],
    )
    assert result.exception is None, result.output
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["stats"]["seed_records"] > 0  # gathered from the notebook seed


def _person_evidence_bundle(tmp_path: Path):
    """Bundle + corpus with one authored doc carrying an affiliation line.

    The doc has ``A. Mackus`` as author (so the author graph resolves the
    ``author:a_mackus`` alias -> this doc), a substantive intro chunk
    (the contribution material), a boilerplate affiliation line naming
    Mackus (identity-context material), and a boilerplate byline naming a
    different person (must NOT be lifted).
    """
    from wikify.api import Bundle as BundleApi
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
        id="mackus_paper", source_path="src/mackus_paper.md", kind="md",
        title="Area-Selective ALD", metadata={"authors": ["A. Mackus"]},
        markdown_path="markdown/mackus_paper.md",
        image_dir="images/mackus_paper/", n_chunks=3, n_tokens=200,
    )
    intro = (
        "Area-selective atomic layer deposition enables bottom-up nanofabrication "
        "by confining film growth to predefined regions of the substrate through "
        "self-limiting surface chemistry."
    )
    affiliation = (
        "A. Mackus, Department of Applied Physics, "
        "Eindhoven University of Technology"
    )
    other_byline = (
        "J. Smith, Department of Chemistry, Some Other University of Elsewhere"
    )
    chunks = [
        Chunk(
            id="mackus_paper__c0000", doc_id="mackus_paper", ord=0,
            text=intro, char_span=(0, len(intro)), section_path=["Intro"],
            section_type="introduction",
        ),
        Chunk(
            id="mackus_paper__c0001", doc_id="mackus_paper", ord=1,
            text=affiliation, char_span=(0, len(affiliation)),
            section_path=["Boiler"], section_type="boilerplate",
            is_boilerplate=True,
        ),
        Chunk(
            id="mackus_paper__c0002", doc_id="mackus_paper", ord=2,
            text=other_byline, char_span=(0, len(other_byline)),
            section_path=["Boiler"], section_type="boilerplate",
            is_boilerplate=True,
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

    create_concept(
        BundleApi.open(bundle), page_id="A. Mackus", kind="person",
        aliases=["author:a_mackus"],
    )
    runner.invoke(
        app,
        [
            "work", "notebook-init", "a-mackus", "--kind", "person",
            "--run", str(bundle),
        ],
    )
    return bundle, corpus_root


def test_build_evidence_person_gathers_identity_context(tmp_path: Path) -> None:
    """The person path lifts a boilerplate affiliation line naming the target
    author as an ``identity_context`` record, while a byline naming a
    different person is excluded and the contribution gather is unchanged."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import read_evidence

    bundle, corpus_root = _person_evidence_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "a-mackus",
            "--run", str(bundle), "--corpus", str(corpus_root),
            "--target", "1", "--format", "json",
        ],
    )
    assert result.exception is None, result.output
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    stats = data["stats"]
    # Contribution gather unchanged: the intro seed chunk is still lifted.
    assert stats["seed_records"] >= 1
    # Exactly one identity chunk: the Mackus affiliation line, not the
    # J. Smith byline (which carries a signal but does not name the author).
    assert stats["identity_context_records"] == 1

    recs = read_evidence(BundleApi.open(bundle), "a-mackus")
    identity = [r for r in recs if r.note == "identity_context"]
    assert len(identity) == 1
    assert identity[0].chunk_id == "mackus_paper__c0001"
    assert "Department of Applied Physics" in identity[0].quote
    # The other-person byline was never committed.
    assert not any(r.chunk_id == "mackus_paper__c0002" for r in recs)


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


def test_build_evidence_seeds_from_notebook_seed_docs(tmp_path: Path) -> None:
    """Seeds set via ``notebook-init --seed-docs`` persist on the notebook
    provenance, not the work card. build-evidence must union them so the
    documented add-concept -> notebook-init -> build-evidence flow seeds
    the gather (previously the seed phase only read the card and saw none)."""
    bundle, corpus_root, _ = _build_evidence_bundle(tmp_path)
    init = runner.invoke(
        app,
        [
            "work", "notebook-init", "atomic-layer-deposition",
            "--seed-docs", '["doc:paper_x"]',
            "--run", str(bundle),
        ],
    )
    assert init.exit_code == 0, init.output
    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "atomic-layer-deposition",
            "--run", str(bundle), "--corpus", str(corpus_root),
            "--target", "3", "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["stats"]["seed_records"] > 0


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


def test_work_refine_candidates_flags_grown_pages(tmp_path: Path) -> None:
    """The refine baseline is ``evidence_total`` (evidence the draft was
    written from), compared against a LIVE recount of active evidence
    records on disk -- not the cached ``evidence_chunks`` frontmatter.

    - ``grown``: committed with evidence_total=8, 17 active records live
      (8 -> 17, ratio > 1.5) is a candidate.
    - ``flat``: committed with evidence_total=10, 11 live (below both
      thresholds) is not.
    - ``person``: the PERSON false-positive -- evidence_total=14 chunks
      gathered but only evidence_count=6 markers used; 14 active records
      live. Comparing 14 -> 14 (ratio 1.0) it must NOT flag, so a
      perfectly-refined page converges instead of looping forever.
    - ``legacy``: an older event carrying only evidence_count (no
      evidence_total) falls back to evidence_count as the baseline
      (6 -> 17, ratio > 1.5) -> candidate."""
    from wikify.api import Bundle
    from wikify.bundle.run.events import Event, append_event
    from wikify.bundle.run.state import load_state
    from wikify.bundle.work.card import create_concept, load_card, save_card
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    run_id = load_state(bundle).run_id

    def _commit(
        slug: str, page_id: str, now: int, data: dict, kind: str = "article"
    ) -> None:
        create_concept(bundle, page_id=page_id, slug=slug, kind=kind)
        # Seed ``now`` active evidence records on disk; refine-candidates
        # recounts these live rather than trusting cached frontmatter.
        append_evidence(
            bundle, slug,
            [EvidenceRecord(chunk_id=f"{slug}_c{i}", doc_id=f"{slug}_d{i}")
             for i in range(now)],
        )
        card = load_card(bundle, slug)
        card.front["status"] = "committed"
        card.front["evidence_chunks"] = now
        card.front["evidence_docs"] = now
        save_card(bundle, slug, card)
        append_event(
            bundle,
            Event(
                run_id=run_id,
                type="page_committed",
                actor="test",
                page_id=page_id,
                data={"slug": slug, "kind": kind, **data},
            ),
        )

    # ratio 2.125 -> candidate (baseline from evidence_total)
    _commit("grown", "Grown Page", now=17,
            data={"evidence_count": 6, "evidence_total": 8})
    # ratio 1.1, delta 1 -> no
    _commit("flat", "Flat Page", now=11,
            data={"evidence_count": 9, "evidence_total": 10})
    # person false-positive: 14 gathered / 6 used, live still 14 -> ratio 1.0
    _commit("person", "Person Page", now=14, kind="person",
            data={"evidence_count": 6, "evidence_total": 14})
    # legacy event: only evidence_count -> baseline falls back to 6
    _commit("legacy", "Legacy Page", now=17,
            data={"evidence_count": 6})

    result = runner.invoke(
        app,
        ["work", "refine-candidates", "--run", str(bundle_dir), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["kind"] == "refine_candidates"
    assert data["n_committed"] == 4
    assert data["n_candidates"] == 2
    assert sorted(it["slug"] for it in data["items"]) == ["grown", "legacy"]
    by_slug = {it["slug"]: it for it in data["items"]}
    assert by_slug["grown"]["evidence_at_commit"] == 8
    assert by_slug["grown"]["evidence_now"] == 17
    assert by_slug["grown"]["ratio"] == 2.125
    assert by_slug["grown"]["delta"] == 9
    assert by_slug["grown"]["reason"] == "both"  # ratio>=1.5 and delta>=6
    # Legacy falls back to evidence_count baseline of 6.
    assert by_slug["legacy"]["evidence_at_commit"] == 6
    assert by_slug["legacy"]["evidence_now"] == 17


def test_work_seen_chunks_unions_active_evidence(tmp_path: Path) -> None:
    from wikify.api import Bundle
    from wikify.bundle.work.card import create_concept
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    create_concept(bundle, page_id="ALD", slug="ald")
    create_concept(bundle, page_id="CVD", slug="cvd")
    append_evidence(bundle, "ald", [
        EvidenceRecord(chunk_id="c1", doc_id="d1", status="active"),
        EvidenceRecord(chunk_id="c2", doc_id="d1", status="archived"),
    ])
    append_evidence(bundle, "cvd", [
        EvidenceRecord(chunk_id="c3", doc_id="d2", status="active"),
    ])
    result = runner.invoke(
        app, ["work", "seen-chunks", "ald", "cvd", "--run", str(bundle_dir)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Union of active evidence across both slugs; archived excluded.
    assert sorted(data["seen_chunk_ids"]) == ["c1", "c3"]
    assert data["n_seen"] == 2


def test_work_refine_candidates_flags_new_data(tmp_path: Path) -> None:
    """Relevance is DOC-level, not chunk-level. A committed page flags with
    reason ``new_data`` when a relevant data artifact -- sharing a source
    DOCUMENT (but NOT a chunk) with its active evidence -- is committed after
    it. This is the disjoint-chunk case the chunk-level join missed: the DATA
    wave harvests the number chunks the article explorer skipped. It converges
    once the page re-commits with the artifact in its ``data_artifacts_seen``
    snapshot; ``--no-data`` disables the signal; an artifact from a different
    document never flags."""
    from wikify.api import Bundle
    from wikify.bundle.run.events import Event, append_event
    from wikify.bundle.run.state import load_state
    from wikify.bundle.work.card import create_concept, load_card, save_card
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence
    from wikify.data.consolidate import consolidate
    from wikify.data.models import ArtifactSpec, DataPoint
    from wikify.data.store import DataStore

    bundle_dir = _init_bundle(tmp_path)
    bundle = Bundle.open(bundle_dir)
    run_id = load_state(bundle).run_id

    def _page_committed(seen: list[str]) -> None:
        append_event(
            bundle,
            Event(
                run_id=run_id, type="page_committed", actor="test", page_id="ALD",
                data={
                    "slug": "ald", "kind": "article",
                    "evidence_count": 2, "evidence_total": 2,
                    "data_artifacts_seen": seen,
                },
            ),
        )

    # Committed page whose active evidence cites the PROSE chunks cB1 + cB2 of
    # doc d1 -- deliberately NOT the number-dense chunk cA the DATA wave harvests.
    create_concept(bundle, page_id="ALD", slug="ald")
    append_evidence(bundle, "ald", [
        EvidenceRecord(chunk_id="cB1", doc_id="d1", status="active"),
        EvidenceRecord(chunk_id="cB2", doc_id="d1", status="active"),
    ])
    card = load_card(bundle, "ald")
    card.front["status"] = "committed"
    save_card(bundle, "ald", card)
    _page_committed(seen=[])

    def _point(subject: str, chunk: str, doc: str, prop: str) -> DataPoint:
        return DataPoint(
            subject=subject, property=prop, value_text="1.1", unit="A/cycle",
            doc_id=doc, chunk_id=chunk, grounding_quote="q",
            verification_status="verified", quote_verified=True,
        ).finalize()

    # Artifact backed by chunk cA of doc d1: shares the DOCUMENT with the page
    # but NOT a chunk. Committed AFTER the page -> must flag ``new_data``.
    store = DataStore.open(bundle.root)
    store.add_points([_point("Al2O3", "cA", "d1", "GPC")])
    spec = ArtifactSpec(artifact_id="gpc", title="GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.upsert_artifact(spec, n_rows=table.n_rows)
    store.set_artifact_claims("gpc", table.claim_ids)
    store.set_artifact_status("gpc", "committed")
    store.close()

    def _refine(*extra: str) -> dict:
        res = runner.invoke(
            app,
            ["work", "refine-candidates", "--run", str(bundle_dir),
             "--format", "json", *extra],
        )
        assert res.exit_code == 0, res.output
        return json.loads(res.output)

    data = _refine()
    by_slug = {it["slug"]: it for it in data["items"]}
    assert "ald" in by_slug  # chunk-level join would MISS this (disjoint chunks)
    assert "new_data" in by_slug["ald"]["reason"]
    assert by_slug["ald"]["new_data_artifacts"] == ["gpc"]

    # --no-data suppresses the signal (no evidence growth here).
    assert _refine("--no-data")["n_candidates"] == 0

    # Convergence: re-commit recording the now-seen artifact -> not flagged.
    _page_committed(seen=["gpc"])
    assert _refine()["n_candidates"] == 0

    # An artifact from a DIFFERENT document (d2) is irrelevant -> never flags.
    store = DataStore.open(bundle.root)
    store.add_points([_point("HfO2", "zC", "d2", "THK")])
    spec2 = ArtifactSpec(artifact_id="other", title="Other", properties=["THK"])
    table2 = consolidate(store, spec2)
    store.upsert_artifact(spec2, n_rows=table2.n_rows)
    store.set_artifact_claims("other", table2.claim_ids)
    store.set_artifact_status("other", "committed")
    store.close()
    assert _refine()["n_candidates"] == 0


# -------------------------------------------------------- concept-recall


def _recall_bundle(tmp_path: Path):
    """Bundle + corpus with 5 photonics docs across a year spread.

    Returns ``(bundle_path, corpus_path, docs)`` where ``docs`` maps
    ``doc_id -> (chunk_id, year)``. Each doc has a single content chunk
    whose text matches the query term ``photonics`` under BM25, with a
    distinct section_type so the diversity signal is exercised. Years
    2010/2012/2015/2018/2021 bucket as early={2010,2012},
    middle={2015}, recent={2018,2021} under the p25/p75 split.
    """
    from wikify.api import Bundle as BundleApi
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

    spec = [
        (2010, "introduction"),
        (2012, "body"),
        (2015, "methods"),
        (2018, "results"),
        (2021, "discussion"),
    ]
    body = (
        "Silicon photonics enables integrated optical circuits for "
        "high-bandwidth data transmission and on-chip light manipulation "
        "in modern communication devices."
    )
    docs: list[Document] = []
    chunk_map: dict[str, list[Chunk]] = {}
    out: dict[str, tuple[str, int]] = {}
    for year, sect in spec:
        did = f"doc_{year}"
        cid = f"{did}__c0000"
        docs.append(
            Document(
                id=did, source_path=f"src/{did}.md", kind="md",
                title=f"Photonics {year}", metadata={"year": year},
                markdown_path=f"markdown/{did}.md", image_dir=f"images/{did}/",
                n_chunks=1, n_tokens=40,
            )
        )
        chunk_map[did] = [
            Chunk(
                id=cid, doc_id=did, ord=0, text=body,
                char_span=(0, len(body)), section_path=["S"],
                section_type=sect,
            )
        ]
        out[did] = (cid, year)

    corpus = Corpus(root=corpus_root)
    corpus.ensure()
    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(store, docs, chunk_map)
        store.fts_rebuild()
    finally:
        store.close()

    create_concept(BundleApi.open(bundle), page_id="Photonics", kind="article")
    return bundle, corpus_root, out


def _run_recall(bundle: Path, corpus_root: Path) -> dict:
    result = runner.invoke(
        app,
        [
            "work", "concept-recall", "photonics",
            "--run", str(bundle), "--corpus", str(corpus_root),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_concept_recall_json_shape(tmp_path: Path) -> None:
    """The envelope carries every documented recall field with the right
    shape."""
    bundle, corpus_root, _docs = _recall_bundle(tmp_path)
    data = _run_recall(bundle, corpus_root)
    assert data["ok"] is True
    assert data["slug"] == "photonics"
    recall = data["recall"]
    for key in (
        "candidate_docs", "represented_docs", "missing_docs", "year_buckets",
        "empty_buckets", "section_types_represented", "max_doc_share",
        "min_represented", "recall_ok",
    ):
        assert key in recall, key
    # All 5 photonics docs surface as candidates.
    assert len(recall["candidate_docs"]) == 5
    for c in recall["candidate_docs"]:
        assert set(c) == {"doc_id", "year", "score", "citation_proximity"}
        # No evidence and no citation edges -> proximity is a no-op.
        assert c["citation_proximity"] == 0.0
    assert set(recall["year_buckets"]) == {"early", "middle", "recent"}
    for b in recall["year_buckets"].values():
        assert set(b) == {"total", "represented"}
    # early={2010,2012}, middle={2015}, recent={2018,2021}
    assert recall["year_buckets"]["early"]["total"] == 2
    assert recall["year_buckets"]["middle"]["total"] == 1
    assert recall["year_buckets"]["recent"]["total"] == 2


def test_concept_recall_missing_then_covered(tmp_path: Path) -> None:
    """Evidence from one doc leaves the rest missing and recall_ok false;
    adding the missing docs (balanced records, all buckets covered) flips
    recall_ok true."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle, corpus_root, docs = _recall_bundle(tmp_path)
    bundle_api = BundleApi.open(bundle)

    # Only the 2010 doc is represented.
    cid_2010, _ = docs["doc_2010"]
    append_evidence(
        bundle_api, "photonics",
        [EvidenceRecord(chunk_id=cid_2010, doc_id="doc_2010", status="active")],
    )
    recall = _run_recall(bundle, corpus_root)["recall"]
    assert recall["min_represented"] == 3  # min(8, ceil(0.6*5))
    assert len(recall["represented_docs"]) == 1
    assert recall["recall_ok"] is False
    missing_ids = {c["doc_id"] for c in recall["missing_docs"]}
    assert missing_ids == {"doc_2012", "doc_2015", "doc_2018", "doc_2021"}

    # Add one balanced record from each remaining doc.
    for did in ("doc_2012", "doc_2015", "doc_2018", "doc_2021"):
        cid, _ = docs[did]
        append_evidence(
            bundle_api, "photonics",
            [EvidenceRecord(chunk_id=cid, doc_id=did, status="active")],
        )
    recall = _run_recall(bundle, corpus_root)["recall"]
    assert len(recall["represented_docs"]) == 5
    assert recall["missing_docs"] == []
    assert recall["empty_buckets"] == []
    assert recall["max_doc_share"] == 0.2  # 1/5 records per doc
    assert recall["recall_ok"] is True
    # Represented chunks span multiple section types (diversity signal).
    assert len(recall["section_types_represented"]) >= 3


def test_concept_recall_max_doc_share_blocks(tmp_path: Path) -> None:
    """Every bucket is represented and enough docs are covered, but the
    records concentrate in one doc (share > 0.35) so recall_ok is false."""
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle, corpus_root, docs = _recall_bundle(tmp_path)
    bundle_api = BundleApi.open(bundle)

    # 2010 (early), 2015 (middle), 2021 (recent) cover all buckets, but the
    # 2010 doc holds 10 of the 12 records.
    cid_2010, _ = docs["doc_2010"]
    append_evidence(
        bundle_api, "photonics",
        [EvidenceRecord(chunk_id=cid_2010, doc_id="doc_2010", status="active")
         for _ in range(10)],
    )
    for did in ("doc_2015", "doc_2021"):
        cid, _ = docs[did]
        append_evidence(
            bundle_api, "photonics",
            [EvidenceRecord(chunk_id=cid, doc_id=did, status="active")],
        )

    recall = _run_recall(bundle, corpus_root)["recall"]
    assert len(recall["represented_docs"]) == 3
    assert recall["represented_docs"] == ["doc_2010", "doc_2015", "doc_2021"]
    assert recall["empty_buckets"] == []
    assert recall["max_doc_share"] > 0.35
    assert recall["recall_ok"] is False


def test_concept_recall_default_bm25_loads_no_embedder(
    tmp_path: Path, monkeypatch
) -> None:
    """The default ranking is BM25: concept-recall must not load an embedder.

    ``embedder_for`` is patched to raise; the default run still succeeds and
    ranks candidates, proving the semantic (embedding) path is never taken.
    """
    import wikify.embedding as embedding_mod

    bundle, corpus_root, _docs = _recall_bundle(tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("embedder must not be loaded on the BM25 path")

    monkeypatch.setattr(embedding_mod, "embedder_for", _boom)
    recall = _run_recall(bundle, corpus_root)["recall"]
    assert len(recall["candidate_docs"]) == 5


def test_concept_recall_rank_semantic_opt_in(tmp_path: Path, monkeypatch) -> None:
    """``--rank semantic`` routes the relevance search through the semantic
    mode; the default routes through bm25."""
    import wikify.corpus.queries as queries_mod

    bundle, corpus_root, _docs = _recall_bundle(tmp_path)
    seen_modes: list[str] = []

    def _capture(corpus, query, *, top_k, rank, exclude_kinds):
        seen_modes.append(rank)
        return [{"doc_id": "doc_2015", "score": 0.9}]

    # The command imports search_chunks locally from the queries module.
    monkeypatch.setattr(queries_mod, "search_chunks", _capture)

    for args, expected in ((["--rank", "semantic"], "semantic"), ([], "bm25")):
        seen_modes.clear()
        result = runner.invoke(
            app,
            [
                "work", "concept-recall", "photonics",
                "--run", str(bundle), "--corpus", str(corpus_root),
                "--format", "json", *args,
            ],
        )
        assert result.exit_code == 0, result.output
        assert seen_modes and all(m == expected for m in seen_modes), seen_modes


def _add_reference_edges(corpus_root: Path, citing: str, cited: list[str]) -> None:
    """Add doc->doc ``references`` edges (``citing`` cites each of ``cited``)."""
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.graph import Edge, GraphStore

    store = Store((corpus_root / "wikify.db"))
    try:
        with transaction(store.con):
            GraphStore(store.con).upsert_edges(
                [Edge("source", citing, "references", "source", c) for c in cited]
            )
    finally:
        store.close()


def test_concept_recall_citation_proximity_reranks(tmp_path: Path) -> None:
    """Candidates sharing a citation edge with the concept's evidence float
    above equally-relevant candidates that do not.

    All five fixture docs carry identical chunk text, so BM25 relevance ties
    across them; ordering is then decided by citation proximity. The evidence
    doc (2015) cites doc_2010 and doc_2018 but not doc_2012 / doc_2021, so the
    two cited docs rank first with proximity 1/3 and the rest score 0.0.
    """
    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle, corpus_root, docs = _recall_bundle(tmp_path)
    bundle_api = BundleApi.open(bundle)

    # Evidence: the 2015 doc, which cites 2010 (X) and 2018 (Y) but not
    # 2012 / 2021 (Z candidates).
    cid_2015, _ = docs["doc_2015"]
    append_evidence(
        bundle_api, "photonics",
        [EvidenceRecord(chunk_id=cid_2015, doc_id="doc_2015", status="active")],
    )
    _add_reference_edges(corpus_root, "doc_2015", ["doc_2010", "doc_2018"])

    recall = _run_recall(bundle, corpus_root)["recall"]
    prox = {c["doc_id"]: c["citation_proximity"] for c in recall["candidate_docs"]}
    assert prox["doc_2010"] == pytest.approx(1 / 3, abs=1e-4)
    assert prox["doc_2018"] == pytest.approx(1 / 3, abs=1e-4)
    assert prox["doc_2012"] == 0.0
    assert prox["doc_2021"] == 0.0

    order = [c["doc_id"] for c in recall["candidate_docs"]]
    # Both cited docs outrank the equally-relevant, non-adjacent Z docs.
    for cited in ("doc_2010", "doc_2018"):
        assert order.index(cited) < order.index("doc_2012")
        assert order.index(cited) < order.index("doc_2021")


def test_concept_recall_proximity_no_evidence_falls_back(tmp_path: Path) -> None:
    """With citation edges present but no evidence docs, proximity is 0 and
    ordering falls back to pure relevance (no crash)."""
    bundle, corpus_root, _docs = _recall_bundle(tmp_path)
    _add_reference_edges(corpus_root, "doc_2015", ["doc_2010", "doc_2018"])

    recall = _run_recall(bundle, corpus_root)["recall"]
    assert recall["represented_docs"] == []
    assert all(c["citation_proximity"] == 0.0 for c in recall["candidate_docs"])
    # Equal relevance + zero proximity -> deterministic doc_id order.
    order = [c["doc_id"] for c in recall["candidate_docs"]]
    assert order == sorted(order)


def test_concept_recall_proximity_no_graph_edges_table(tmp_path: Path) -> None:
    """A corpus whose ``graph_edges`` table is absent yields proximity 0 for
    every candidate without crashing (targeted read swallows the error)."""
    import sqlite3

    from wikify.api import Bundle as BundleApi
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle, corpus_root, docs = _recall_bundle(tmp_path)
    bundle_api = BundleApi.open(bundle)
    cid_2015, _ = docs["doc_2015"]
    append_evidence(
        bundle_api, "photonics",
        [EvidenceRecord(chunk_id=cid_2015, doc_id="doc_2015", status="active")],
    )
    con = sqlite3.connect(str(corpus_root / "wikify.db"))
    try:
        con.execute("DROP TABLE graph_edges")
        con.commit()
    finally:
        con.close()

    recall = _run_recall(bundle, corpus_root)["recall"]
    assert all(c["citation_proximity"] == 0.0 for c in recall["candidate_docs"])
