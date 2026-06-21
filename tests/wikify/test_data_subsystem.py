"""Tests for the factual-data subsystem (claim store, verify, consolidate,
artifact page, render integration, and the ``wikify data`` CLI)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle, Corpus
from wikify.cli import app
from wikify.data.artifact_page import render_artifact_markdown, write_artifact_page
from wikify.data.consolidate import consolidate
from wikify.data.models import (
    ArtifactSpec,
    DataPoint,
    normalize_key,
    parse_leading_number,
)
from wikify.data.store import DataStore
from wikify.data.verify import number_supported, quote_in_source, verify_point

runner = CliRunner()


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------


def test_normalize_key_collapses_separators() -> None:
    assert normalize_key("Growth-per_cycle ") == "growth per cycle"
    assert normalize_key("GPC/film") == "gpc film"


def test_parse_leading_number_variants() -> None:
    assert parse_leading_number("1.1 A/cycle") == 1.1
    assert parse_leading_number("−0.5 eV") == -0.5  # unicode minus
    assert parse_leading_number("1,200 cycles") == 1200.0
    assert parse_leading_number("2.5e-3 m") == 2.5e-3
    assert parse_leading_number("no number here") is None


def test_parse_leading_number_bare_integers_not_truncated() -> None:
    """Regression: bare integers >= 1000 must not collapse to their first
    three digits (the thousands-group regex previously matched "100" out of
    "1000" and silently rejected clean endurance/retention values)."""
    assert parse_leading_number("1000") == 1000.0
    assert parse_leading_number("2000 cycles") == 2000.0
    assert parse_leading_number("10000") == 10000.0
    assert parse_leading_number("1000000") == 1000000.0


def test_datapoint_claim_id_is_stable_and_idempotent() -> None:
    p1 = DataPoint(
        subject="Al2O3", property="GPC", value_text="1.1 A/cycle",
        unit="A/cycle", doc_id="d1", chunk_id="c1", grounding_quote="q",
    ).finalize()
    p2 = DataPoint(
        subject="al2o3 ", property="gpc", value_text="1.1 A/cycle",
        unit="A/cycle", doc_id="d1", chunk_id="c1", grounding_quote="q",
    ).finalize()
    # Same content (subject/property normalized) -> same id.
    assert p1.claim_id == p2.claim_id
    # Re-finalizing does not change the id.
    assert p1.finalize().claim_id == p1.claim_id


def test_claim_id_merges_numeric_equivalents() -> None:
    """Review m2: '1.1' and '1.10' are the same fact -> one claim id."""
    a = DataPoint(subject="X", property="GPC", value_text="1.1", unit="A",
                  doc_id="d", chunk_id="c", grounding_quote="q").finalize()
    b = DataPoint(subject="X", property="GPC", value_text="1.10", unit="A",
                  doc_id="d", chunk_id="c", grounding_quote="q").finalize()
    assert a.claim_id == b.claim_id


def test_claim_id_separates_on_uncertainty() -> None:
    """Review m2: two facts differing only in uncertainty are distinct."""
    a = DataPoint(subject="X", property="GPC", value_text="1.1", unit="A",
                  uncertainty="0.1", doc_id="d", chunk_id="c",
                  grounding_quote="q").finalize()
    b = DataPoint(subject="X", property="GPC", value_text="1.1", unit="A",
                  uncertainty="0.2", doc_id="d", chunk_id="c",
                  grounding_quote="q").finalize()
    assert a.claim_id != b.claim_id


def test_datapoint_from_dict_accepts_value_and_quote_aliases() -> None:
    p = DataPoint.from_dict(
        {"subject": "X", "property": "Y", "value": "3 nm", "doc_id": "d", "quote": "z"}
    )
    assert p.value_text == "3 nm"
    assert p.grounding_quote == "z"


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------


def test_quote_in_source_exact_and_whitespace() -> None:
    assert quote_in_source("a b c", "x a b c y")
    assert quote_in_source("a   b", "text a b text")  # collapsed whitespace
    assert not quote_in_source("missing", "other text")


def test_number_supported_float_normalization() -> None:
    assert number_supported("1.10 A", "GPC was 1.1 A", "GPC was 1.1 A reported")
    assert not number_supported("9.9 A", "GPC was 1.1 A", "GPC was 1.1 A")


def test_number_supported_scientific_notation() -> None:
    """Regression (review M2): a scientific-notation value must verify against
    a source that prints it the same way, and equal magnitudes in different
    forms (2.5e-3 vs 0.0025) must match."""
    assert number_supported(
        "2.5e-3 cm2", "area of 2.5e-3 cm2", "device area of 2.5e-3 cm2"
    )
    assert number_supported("0.0025", "value 2.5e-3", "value 2.5e-3")


def test_number_supported_bare_integer_thousands() -> None:
    """Regression: an endurance of 1000 cycles verifies against '1000' in
    the source (the integer-truncation bug would have rejected it)."""
    assert number_supported("1000 cycles", "endurance of 1000 cycles", "1000 cycles")


def test_verify_point_verified() -> None:
    p = DataPoint(
        subject="Al2O3", property="GPC", value_text="1.1 A/cycle", unit="A/cycle",
        doc_id="d", chunk_id="c", grounding_quote="a GPC of 1.1 A/cycle",
    )
    verify_point(p, chunk_text="We measured a GPC of 1.1 A/cycle at 200 C.")
    assert p.verification_status == "verified"
    assert p.quote_verified is True


def test_verify_point_rejected_when_number_absent() -> None:
    p = DataPoint(
        subject="Al2O3", property="GPC", value_text="9.9 A/cycle", unit="A/cycle",
        doc_id="d", chunk_id="c", grounding_quote="a GPC of 9.9 A/cycle",
    )
    verify_point(p, chunk_text="We measured a GPC of 1.1 A/cycle.")
    assert p.verification_status == "rejected"


def test_verify_point_verifies_against_caption() -> None:
    p = DataPoint(
        subject="HfO2", property="GPC", value_text="1.0 A/cycle", unit="A/cycle",
        doc_id="d", chunk_id="c", source_kind="figure_caption",
        grounding_quote="GPC of 1.0 A/cycle",
    )
    verify_point(p, chunk_text="See figure.", caption="Figure 2. GPC of 1.0 A/cycle.")
    assert p.verification_status == "verified"


def test_verify_point_figure_tier_is_flagged_not_rejected() -> None:
    p = DataPoint(
        subject="TiO2", property="GPC", value_text="0.5 A/cycle", unit="A/cycle",
        doc_id="d", chunk_id="c", source_kind="figure", extraction_tier="T3",
        grounding_quote="read from plot",
    )
    verify_point(p, chunk_text="unrelated")
    assert p.verification_status == "figure_digitized"
    assert p.quote_verified is False


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------


def _verified(
    subject: str, prop: str, value: str, unit: str, doc: str, chunk: str, quote: str
) -> DataPoint:
    p = DataPoint(
        subject=subject, property=prop, value_text=value, unit=unit,
        doc_id=doc, chunk_id=chunk, grounding_quote=quote,
        verification_status="verified", quote_verified=True,
    )
    return p.finalize()


def test_store_add_dedup_and_coverage(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    p = _verified("Al2O3", "GPC", "1.1 A/cycle", "A/cycle", "d1", "c1", "q1")
    r1 = store.add_points([p])
    r2 = store.add_points([p])  # same content -> duplicate
    assert r1["added"] == 1
    assert r2["added"] == 0 and r2["duplicate"] == 1
    cov = store.coverage()
    assert cov["n_points"] == 1 and cov["n_verified"] == 1
    assert cov["verified_ratio"] == 1.0


def test_store_registry_picks_modal_unit(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("A", "GPC", "1.0", "A/cycle", "d1", "c1", "q"),
        _verified("B", "GPC", "1.2", "A/cycle", "d2", "c2", "q"),
        _verified("C", "GPC", "0.12", "nm/cycle", "d3", "c3", "q"),
    ])
    props = {p["property_norm"]: p for p in store.properties()}
    assert props["gpc"]["canonical_unit"] == "A/cycle"
    assert props["gpc"]["n_points"] == 3


def test_store_list_filters(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q"),
        _verified("HfO2", "GPC", "1.0", "A/cycle", "d2", "c2", "q"),
    ])
    rows = store.list_points(subject="Al2O3")
    assert len(rows) == 1 and rows[0]["subject"] == "Al2O3"
    assert len(store.list_points(property="GPC")) == 2


# --------------------------------------------------------------------------
# consolidate
# --------------------------------------------------------------------------


def test_consolidate_pivots_subjects_by_property(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1"),
        _verified("HfO2", "GPC", "1.0", "A/cycle", "d2", "c2", "q2"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="GPC Table", properties=["GPC"])
    table = consolidate(store, spec)
    assert table.n_rows == 2
    assert table.columns == ["GPC"]
    assert len(table.evidence) == 2
    subjects = {r["subject"] for r in table.rows}
    assert subjects == {"Al2O3", "HfO2"}


def test_consolidate_flags_conflicts(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1"),
        _verified("Al2O3", "GPC", "0.9", "A/cycle", "d2", "c2", "q2"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="GPC", properties=["GPC"])
    table = consolidate(store, spec)
    assert table.n_conflicts == 1
    cell = table.rows[0]["cells"]["GPC"]
    assert cell.conflict is True
    assert len(cell.markers) == 2


def test_consolidate_does_not_mutate_claim_status(tmp_path: Path) -> None:
    """Review M1: consolidation is a pure projection — a conflict cell must
    NOT rewrite the backing claims' stored verification_status."""
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1"),
        _verified("Al2O3", "GPC", "0.9", "A/cycle", "d2", "c2", "q2"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="GPC", properties=["GPC"])
    table = consolidate(store, spec)
    assert table.n_conflicts == 1
    # Both backing claims stay 'verified' in the store (no side effect).
    statuses = {r["verification_status"] for r in store.list_points(property="GPC")}
    assert statuses == {"verified"}


def test_consolidate_honors_spec_subject_order(tmp_path: Path) -> None:
    """Review m6: when the spec lists subjects, rows follow that order."""
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q"),
        _verified("HfO2", "GPC", "1.0", "A/cycle", "d2", "c2", "q"),
        _verified("TiO2", "GPC", "0.5", "A/cycle", "d3", "c3", "q"),
    ])
    spec = ArtifactSpec(
        artifact_id="gpc", title="GPC", properties=["GPC"],
        subjects=["TiO2", "Al2O3", "HfO2"],
    )
    table = consolidate(store, spec)
    assert [r["subject"] for r in table.rows] == ["TiO2", "Al2O3", "HfO2"]


def test_consolidate_respects_min_verification(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    p_unver = DataPoint(
        subject="X", property="GPC", value_text="2.0", unit="A/cycle",
        doc_id="d", chunk_id="c", grounding_quote="q",
        verification_status="unverified",
    ).finalize()
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1"),
        p_unver,
    ])
    spec = ArtifactSpec(
        artifact_id="gpc", title="GPC", properties=["GPC"], min_verification="verified"
    )
    assert consolidate(store, spec).n_rows == 1  # unverified excluded
    spec_any = ArtifactSpec(
        artifact_id="gpc", title="GPC", properties=["GPC"], min_verification="any"
    )
    assert consolidate(store, spec_any).n_rows == 2


# --------------------------------------------------------------------------
# artifact page
# --------------------------------------------------------------------------


def test_render_artifact_markdown_structure(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1 A/cycle", "A/cycle", "doc1", "c1", "GPC was 1.1"),
    ])
    spec = ArtifactSpec(
        artifact_id="gpc", title="ALD GPC", properties=["GPC"],
        description="Growth per cycle across ALD processes.",
    )
    table = consolidate(store, spec)
    md = render_artifact_markdown(table)
    assert "kind: data" in md
    assert "# ALD GPC" in md
    assert "| Subject | GPC |" in md
    assert "Al2O3" in md
    assert "[^d1]" in md
    assert "## References" in md
    assert '[^d1]: c1 (doc1) > "GPC was 1.1"' in md


def test_write_artifact_page_emits_md_and_sidecar(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "doc1", "c1", "q"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    wiki_data = tmp_path / "wiki" / "data"
    md_path = write_artifact_page(wiki_data, spec, table)
    assert md_path.is_file()
    sidecar = md_path.with_suffix(".dataspec.json")
    assert sidecar.is_file()
    side = json.loads(sidecar.read_text(encoding="utf-8"))
    assert side["artifact_id"] == "gpc"
    assert side["claim_ids"]


# --------------------------------------------------------------------------
# render integration: a data page becomes HTML with references
# --------------------------------------------------------------------------


def test_data_page_renders_to_html_with_references(tmp_path: Path) -> None:
    from wikify.bundle.wiki.page import load_bundle
    from wikify.render.html.render import build_site

    wiki = tmp_path / "wiki"
    (wiki / "articles").mkdir(parents=True)
    (wiki / "data").mkdir(parents=True)
    # one ordinary article so the site has prose pages too
    (wiki / "articles" / "Atomic Layer Deposition.md").write_text(
        "---\nid: Atomic Layer Deposition\nkind: article\n"
        "title: Atomic Layer Deposition\naliases: []\nlinks: []\n---\n\n"
        "# Atomic Layer Deposition\n\n"
        + ("ALD is a thin-film growth technique with self-limiting reactions. " * 6)
        + "[^e1]\n\n## References\n\n"
        '[^e1]: c0 (doc1) > "ALD is a thin-film technique"\n',
        encoding="utf-8",
    )
    # the data artifact page
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1 A/cycle", "A/cycle", "doc1", "c1", "GPC 1.1"),
        _verified("HfO2", "GPC", "1.0 A/cycle", "A/cycle", "doc2", "c2", "GPC 1.0"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD Growth Per Cycle", properties=["GPC"])
    write_artifact_page(wiki / "data", spec, consolidate(store, spec))

    bundle = load_bundle(wiki)
    assert any(p.kind == "data" for p in bundle.pages)

    out = tmp_path / "site"
    build_site(bundle, out, corpus_root=None)

    data_html = list((out / "data").glob("*.html"))
    assert data_html, "expected a rendered data-artifact HTML page"
    text = data_html[0].read_text(encoding="utf-8")
    assert "<table>" in text
    assert "Al2O3" in text and "HfO2" in text
    # references page lists the data page's sources
    refs = (out / "references.html").read_text(encoding="utf-8")
    assert "doc1" in refs or "doc2" in refs
    # data table appears on the homepage
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "ALD Growth Per Cycle" in index


# --------------------------------------------------------------------------
# CLI end-to-end: add (verify against corpus) -> consolidate --commit
# --------------------------------------------------------------------------


def _numeric_corpus(root: Path) -> Corpus:
    """Tiny corpus whose chunks carry verifiable numbers."""
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents
    from wikify.models import Chunk, Document

    docs = []
    chunks = {}
    specs = [
        ("doc_a", "Al2O3 ALD",
         "The Al2O3 process gives a growth per cycle of 1.1 A/cycle at 200 C."),
        ("doc_b", "HfO2 ALD", "For HfO2 the measured growth per cycle is 1.0 A/cycle."),
    ]
    for i, (doc_id, title, text) in enumerate(specs):
        docs.append(Document(
            id=doc_id, source_path=f"src/{doc_id}.md", kind="md", title=title,
            metadata={"year": 2020 + i, "authors": [f"Author {i}"]},
            markdown_path=f"markdown/{doc_id}.md", image_dir=f"images/{doc_id}/",
            n_chunks=1, n_tokens=40,
        ))
        chunks[doc_id] = [Chunk(
            id=f"{doc_id}__c0000", doc_id=doc_id, ord=0, text=text,
            char_span=(0, len(text)), section_path=["results"], section_type="body",
        )]
    corpus = Corpus(root=root)
    corpus.ensure()
    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(store, docs, chunks)
        store.fts_rebuild()
    finally:
        store.close()
    corpus.manifest_path.write_text("{}", encoding="utf-8")
    return corpus


def _init_bundle(tmp_path: Path, corpus: Corpus) -> Bundle:
    from wikify.bundle.run.lifecycle import init_run

    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "run").mkdir(parents=True)
    bundle = Bundle(root=bundle_dir)
    init_run(bundle, corpus_path=str(corpus.root))
    return bundle


def test_cli_add_verifies_and_rejects(tmp_path: Path) -> None:
    corpus = _numeric_corpus(tmp_path / "corpus")
    bundle = _init_bundle(tmp_path, corpus)
    records = tmp_path / "staged.jsonl"
    records.write_text(
        json.dumps({
            "subject": "Al2O3", "property": "Growth per cycle", "value": "1.1 A/cycle",
            "unit": "A/cycle", "doc_id": "doc_a", "chunk_id": "doc_a__c0000",
            "grounding_quote": "a growth per cycle of 1.1 A/cycle",
        }) + "\n" + json.dumps({
            "subject": "Fake", "property": "Growth per cycle", "value": "9.9 A/cycle",
            "unit": "A/cycle", "doc_id": "doc_a", "chunk_id": "doc_a__c0000",
            "grounding_quote": "a growth per cycle of 9.9 A/cycle",
        }) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["data", "add", str(records), "--run", str(bundle.root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verified"] == 1
    assert payload["rejected"] == 1
    assert payload["stored"] == 1


def test_cli_add_errors_when_corpus_unresolvable(tmp_path: Path) -> None:
    """Review M3: `data add` must fail loudly (not silently reject all) when
    no corpus can be resolved to verify against."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run

    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "run").mkdir(parents=True)
    bundle = Bundle(root=bundle_dir)
    init_run(bundle, corpus_path=str(tmp_path / "does_not_exist"))
    records = tmp_path / "staged.jsonl"
    records.write_text(json.dumps({
        "subject": "X", "property": "Y", "value": "1", "doc_id": "d",
        "chunk_id": "c", "grounding_quote": "q",
    }) + "\n", encoding="utf-8")
    result = runner.invoke(
        app, ["data", "add", str(records), "--run", str(bundle.root)]
    )
    assert result.exit_code != 0
    assert "no_corpus" in result.output


def test_cli_consolidate_commit_then_render(tmp_path: Path) -> None:
    corpus = _numeric_corpus(tmp_path / "corpus")
    bundle = _init_bundle(tmp_path, corpus)
    records = tmp_path / "staged.jsonl"
    records.write_text(
        json.dumps({
            "subject": "Al2O3", "property": "Growth per cycle", "value": "1.1 A/cycle",
            "unit": "A/cycle", "doc_id": "doc_a", "chunk_id": "doc_a__c0000",
            "grounding_quote": "a growth per cycle of 1.1 A/cycle",
        }) + "\n" + json.dumps({
            "subject": "HfO2", "property": "Growth per cycle", "value": "1.0 A/cycle",
            "unit": "A/cycle", "doc_id": "doc_b", "chunk_id": "doc_b__c0000",
            "grounding_quote": "growth per cycle is 1.0 A/cycle",
        }) + "\n",
        encoding="utf-8",
    )
    assert runner.invoke(
        app, ["data", "add", str(records), "--run", str(bundle.root)]
    ).exit_code == 0

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "artifact_id": "ald-gpc",
        "title": "ALD Growth Per Cycle",
        "properties": ["Growth per cycle"],
        "description": "Growth per cycle for ALD processes.",
    }), encoding="utf-8")
    res = runner.invoke(app, [
        "data", "consolidate", str(spec), "--run", str(bundle.root),
        "--commit", "--format", "json",
    ])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["rows"] == 2

    page = bundle.wiki_data_dir / "ALD Growth Per Cycle.md"
    assert page.is_file()
    assert "kind: data" in page.read_text(encoding="utf-8")

    # Render and confirm the data page + references.
    out = tmp_path / "site"
    rr = runner.invoke(app, [
        "render", "--bundle", str(bundle.root), "--format", "html",
        "--out", str(out), "--corpus", str(corpus.root),
    ])
    assert rr.exit_code == 0, rr.output
    data_html = list((out / "data").glob("*.html"))
    assert data_html
    html = data_html[0].read_text(encoding="utf-8")
    assert "<table>" in html and "Al2O3" in html


def test_dossier_surfaces_data_points_from_evidence(tmp_path: Path) -> None:
    """A verified claim on a gathered evidence chunk appears in the dossier's
    ``## Available data`` table, citable via that chunk's marker."""
    from wikify.bundle.draft.artifact import dossier_path
    from wikify.bundle.draft.builder import build_draft
    from wikify.bundle.work.card import create_concept
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    corpus = _numeric_corpus(tmp_path / "corpus")
    bundle = _init_bundle(tmp_path, corpus)
    slug, _ = create_concept(bundle, page_id="Al2O3 ALD", aliases=["alumina ALD"])
    append_evidence(
        bundle, slug,
        [EvidenceRecord(chunk_id="doc_a__c0000", doc_id="doc_a",
                        quote="growth per cycle of 1.1 A/cycle")],
    )
    store = DataStore.open(bundle.root)
    store.add_points([
        _verified("Al2O3", "Growth per cycle", "1.1 A/cycle", "A/cycle",
                  "doc_a", "doc_a__c0000", "growth per cycle of 1.1 A/cycle"),
    ])
    store.close()

    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    dossier = dossier_path(bundle, slug).read_text(encoding="utf-8")
    assert "## Available data" in dossier
    assert "Growth per cycle" in dossier
    assert "1.1 A/cycle" in dossier
    # The marker referenced must be a real evidence marker (e1).
    assert "[^e1]" in dossier


def test_cli_rebuild_reflects_new_claims(tmp_path: Path) -> None:
    corpus = _numeric_corpus(tmp_path / "corpus")
    bundle = _init_bundle(tmp_path, corpus)
    store = DataStore.open(bundle.root)
    store.add_points([
        _verified("Al2O3", "Growth per cycle", "1.1 A/cycle", "A/cycle",
                  "doc_a", "doc_a__c0000", "q"),
    ])
    store.close()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "artifact_id": "ald-gpc", "title": "ALD GPC", "properties": ["Growth per cycle"],
    }), encoding="utf-8")
    runner.invoke(app, [
        "data", "consolidate", str(spec), "--run", str(bundle.root), "--commit"
    ])
    # Add a second subject, then rebuild.
    store = DataStore.open(bundle.root)
    store.add_points([
        _verified("HfO2", "Growth per cycle", "1.0 A/cycle", "A/cycle",
                  "doc_b", "doc_b__c0000", "q"),
    ])
    store.close()
    res = runner.invoke(app, [
        "data", "rebuild", "--run", str(bundle.root), "--format", "json"
    ])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["rebuilt"][0]["rows"] == 2  # now reflects both subjects
