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
from wikify.data.harvest import number_dense_chunks, sweep_property_candidates
from wikify.data.models import (
    ArtifactSpec,
    DataPoint,
    normalize_key,
    parse_leading_number,
)
from wikify.data.store import DataStore
from wikify.data.verify import number_supported, quote_in_source, verify_point
from wikify.models import Chunk, Document

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


def test_data_gate_grounding_matches_validator() -> None:
    """F19 root fix: the data verifier and the draft validator must ground a
    dossier-copied quote identically — control chars and inline citation
    markers tolerated at both gates, fabrication rejected at both."""
    from wikify.bundle.draft.validator import _quote_is_grounded

    pairs = [
        ("endurance of 10 6 cycles was measured",
         "endurance \x01 of 10 6 cycles was measured"),
        ("Memristors exhibit pinched hysteresis under bias.",
         "Memristors exhibit pinched hysteresis [1-3] under bias."),
    ]
    for quote, source in pairs:
        assert quote_in_source(quote, source)  # data gate now tolerates noise
        assert _quote_is_grounded(quote, source) == quote_in_source(quote, source)
    # Fabrication still rejected by both gates.
    fab_q, fab_s = "ALD enables 5 nm copper interconnects", "ALD grows oxide films."
    assert not quote_in_source(fab_q, fab_s)
    assert not _quote_is_grounded(fab_q, fab_s)


def test_ocr_mangled_scalar_rejected_but_legit_verified() -> None:
    """F8: an OCR-mangled scalar value ("1 10 5 ohm cm" for 1e5) must be
    rejected — its leading-number parse (1.0) would otherwise verify against
    any source containing a "1". A well-formed scalar still verifies, and a
    range that legitimately carries two numbers is not flagged."""
    from wikify.data.models import DataPoint
    from wikify.data.verify import is_ocr_mangled_scalar, verify_point

    assert is_ocr_mangled_scalar("1 10 5 ohm cm")
    assert not is_ocr_mangled_scalar("1.1 A")
    assert not is_ocr_mangled_scalar("2.5e-3 cm2")  # unit digit is not a bare number
    assert not is_ocr_mangled_scalar("10 to 20 nm")  # run breaks at "to"
    # Locale thousands grouping is legitimate, not OCR mangling.
    assert not is_ocr_mangled_scalar("1 000 cycles")
    assert not is_ocr_mangled_scalar("10 000 s")
    assert not is_ocr_mangled_scalar("1 234 567 events")
    assert not is_ocr_mangled_scalar("1 000.5 nm")
    # ...but a non-3-digit second group is still mangled.
    assert is_ocr_mangled_scalar("1 00 5 ohm")

    mangled = DataPoint(
        subject="film", property="resistivity",
        value_text="1 10 5 ohm cm", value_original="1 10 5 ohm cm",
        doc_id="d", grounding_quote="resistivity of 1 10 5 ohm cm",
        value_type="scalar").finalize()
    verify_point(mangled, chunk_text="we measured resistivity of 1 10 5 ohm cm here")
    assert mangled.verification_status == "rejected"

    legit = DataPoint(
        subject="film", property="gpc", value_text="1.1", value_original="1.1 A",
        doc_id="d", grounding_quote="GPC was 1.1 A", value_type="scalar").finalize()
    verify_point(legit, chunk_text="the GPC was 1.1 A in this process")
    assert legit.verification_status == "verified"

    # A spaced-thousands scalar must NOT be dropped as OCR-mangled, AND must
    # carry the correct magnitude (10000, not the leading token 10) so
    # consolidation dedup/conflict logic compares it correctly.
    grouped = DataPoint(
        subject="film", property="endurance", value_text="10 000 cycles",
        value_original="10 000 cycles", doc_id="d",
        grounding_quote="endurance of 10 000 cycles", value_type="scalar").finalize()
    assert grouped.value_num == 10000.0
    verify_point(grouped, chunk_text="measured endurance of 10 000 cycles here")
    assert grouped.verification_status == "verified"


def test_parse_leading_number_handles_spaced_thousands() -> None:
    from wikify.data.models import parse_leading_number

    assert parse_leading_number("10 000 cycles") == 10000.0
    assert parse_leading_number("1 234 567 events") == 1234567.0
    assert parse_leading_number("1 000.5 nm") == 1000.5
    assert parse_leading_number("1.1 A") == 1.1  # ordinary value unchanged
    # OCR-mangled run is NOT a thousands grouping -> not collapsed.
    assert parse_leading_number("1 10 5 ohm") == 1.0

    # A range with two numbers is exempt (value_type != scalar/bound).
    rng = DataPoint(
        subject="film", property="thickness", value_text="10 to 20 nm",
        value_original="10 to 20 nm", doc_id="d",
        grounding_quote="thickness 10 to 20 nm", value_type="range").finalize()
    verify_point(rng, chunk_text="film thickness 10 to 20 nm measured")
    assert rng.verification_status == "verified"


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


def test_consolidate_reports_empty_columns(tmp_path: Path) -> None:
    """F22: a spec property that matches no stored claim is surfaced as an
    empty column instead of silently shipping a blank column."""
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1"),
    ])
    spec = ArtifactSpec(
        artifact_id="t", title="T", properties=["GPC", "On/Off Ratio"]
    )
    table = consolidate(store, spec)
    # GPC has data; "On/Off Ratio" has none -> reported, not silently blank.
    assert table.empty_columns == ["On/Off Ratio"]
    assert table.n_rows == 1


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
    assert '[^d1]: doc1 > "GPC was 1.1"' in md


def test_register_artifact_wiki_page_inserts_data_row(tmp_path: Path) -> None:
    """F28: a committed data artifact gets a kind=data row in the wiki page DB
    so navigation/index/graph can reference it instead of orphaning it."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.page_naming import page_id_from_title
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import register_artifact_wiki_page

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()

    page_id = register_artifact_wiki_page(bundle, spec, table)
    assert page_id == page_id_from_title("ALD GPC")
    con = open_wiki_store(bundle.sqlite_path)
    try:
        row = con.execute(
            "SELECT kind, title FROM wiki_pages WHERE page_id = ?", (page_id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None, "data artifact must be registered in wiki_pages"
    assert row["kind"] == "data"


def test_committed_data_artifact_round_trips_find_and_show(tmp_path: Path) -> None:
    """F28 follow-up: a committed data artifact must round-trip — its DB-backed
    search hit's path/handle resolves back to the real on-disk page via
    show_page (slug == on-disk stem, kind=data path-aware)."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.queries import find_text, show_page
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC Comparison", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    # Mirror the commit path: write the on-disk page AND register the DB row.
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)

    # The data page surfaces in search and its emitted path is the real file.
    hits = [h for h in find_text(bundle, "GPC") if h["kind"] == "data"]
    assert hits, "committed data artifact must surface in find_text"
    assert (bundle.root / hits[0]["path"]).is_file()

    # The emitted slug round-trips through show_page back to the data page.
    shown = show_page(bundle, handle=hits[0]["slug"])
    assert shown is not None
    assert shown["kind"] == "data"
    assert shown["slug"] == page_id
    assert (bundle.root / shown["path"]).is_file()
    assert "ALD GPC Comparison" in shown["text"] or "GPC" in shown["text"]


def test_committed_data_artifact_in_index_and_committed_pages(tmp_path: Path) -> None:
    """F28 follow-up: a committed data artifact must appear in the canonical
    committed-page projections (list_committed_pages + derived/index.json),
    not only in the ad hoc query helpers — no split-brain."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import (
        list_committed_pages,
        read_index,
        rebuild_index,
    )
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC Comparison", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    register_artifact_wiki_page(bundle, spec, table)

    assert any(p["kind"] == "data" for p in list_committed_pages(bundle))
    rebuild_index(bundle)
    assert any(p["kind"] == "data" for p in read_index(bundle)["pages"])


def test_wiki_rebuild_preserves_data_artifact_chunk_provenance(tmp_path: Path) -> None:
    """A `wiki rebuild` after committing a data artifact must NOT overwrite the
    precise claim chunk_id (stored by register from the claim store) with the
    doc id from the lossy rendered markdown."""
    import sqlite3

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    # A claim whose chunk_id differs from its doc_id.
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle",
                                "doc_99", "doc_99__c0007_abcd", "GPC was 1.1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)

    rebuild_graph(bundle)  # the lossy-markdown rebuild must not touch data rows

    con = open_wiki_store(bundle.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT chunk_id FROM wiki_evidence WHERE page_id = ?", (page_id,)
        ).fetchall()
    finally:
        con.close()
    chunk_ids = {r["chunk_id"] for r in rows}
    assert "doc_99__c0007_abcd" in chunk_ids, chunk_ids


def test_wiki_rebuild_restores_data_page_from_disk_only(tmp_path: Path) -> None:
    """Recovery: with the data page's .md + sidecar on disk but no wiki_pages
    row (deleted/corrupt wiki.db), `wiki rebuild` must restore a kind=data row
    with the precise claim chunk evidence (reconstructed from the claim store,
    not the lossy markdown)."""
    import sqlite3

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle",
                                "doc_7", "doc_7__c0003_beef", "GPC was 1.1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)

    # Simulate a lost wiki.db row (recovery scenario).
    con = open_wiki_store(bundle.sqlite_path)
    con.execute("DELETE FROM wiki_pages WHERE page_id = ?", (page_id,))
    con.commit()
    con.close()

    rebuild_graph(bundle)

    con = open_wiki_store(bundle.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT kind FROM wiki_pages WHERE page_id = ?", (page_id,)
        ).fetchone()
        ev = con.execute(
            "SELECT chunk_id FROM wiki_evidence WHERE page_id = ?", (page_id,)
        ).fetchall()
    finally:
        con.close()
    assert row is not None and row["kind"] == "data"
    assert "doc_7__c0003_beef" in {r["chunk_id"] for r in ev}


def test_rebuild_preserves_data_page_when_claim_store_missing(tmp_path: Path) -> None:
    """A wiki rebuild with a missing/stale claims.db must NOT overwrite a
    committed data page with an empty projection — it preserves the row."""
    import sqlite3

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle",
                                "doc_7", "doc_7__c0003_beef", "GPC was 1.1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)

    # Simulate a lost claim store (recovery from partial corruption).
    bundle.claims_db_path.unlink()

    rebuild_graph(bundle)  # must not consolidate-zero and clobber the row

    con = open_wiki_store(bundle.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        ev = con.execute(
            "SELECT chunk_id FROM wiki_evidence WHERE page_id = ?", (page_id,)
        ).fetchall()
    finally:
        con.close()
    # The original chunk evidence survives (not erased by an empty projection).
    assert "doc_7__c0003_beef" in {r["chunk_id"] for r in ev}


def test_artifact_title_cannot_inject_frontmatter_kind(tmp_path: Path) -> None:
    """A title containing a newline + 'kind:' must not change the parsed page
    kind — frontmatter injection is neutralized."""
    from wikify.bundle.wiki.page import parse_page
    from wikify.data.artifact_page import write_artifact_page

    store = DataStore(tmp_path / "claims.db")
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(
        artifact_id="x", title="Good\nkind: person", properties=["GPC"]
    )
    table = consolidate(store, spec)
    wiki_data = tmp_path / "wiki" / "data"
    md_path = write_artifact_page(wiki_data, spec, table)
    parsed = parse_page(md_path)
    assert parsed.kind == "data"
    # No frontmatter LINE overrides kind (the injected newline was collapsed).
    lines = [ln.strip() for ln in md_path.read_text(encoding="utf-8").splitlines()]
    assert "kind: person" not in lines


def test_data_artifact_id_consistent_for_reserved_char_title(tmp_path: Path) -> None:
    """A data artifact title with a filesystem-reserved char must yield ONE
    canonical id across frontmatter, filename, and the DB row, and a rebuild
    must not fail on a unique-slug conflict."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.page import parse_page
    from wikify.bundle.wiki.page_naming import page_filename, page_id_from_title
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "On/Off Ratio", "1e5", "", "d1", "c1", "ratio 1e5")])
    spec = ArtifactSpec(artifact_id="r", title="ON/OFF Ratio", properties=["On/Off Ratio"])
    table = consolidate(store, spec)
    store.close()
    canonical = page_id_from_title("ON/OFF Ratio")
    md_path = write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)

    assert page_id == canonical
    assert md_path.name == page_filename(canonical)
    # Frontmatter id matches the filename/DB id, not the raw slash title.
    assert parse_page(md_path).id == canonical
    # A rebuild does not raise (no split file/DB identity, no slug collision).
    rebuild_graph(bundle)


def test_data_consolidate_commit_respects_run_lock(tmp_path: Path) -> None:
    """`data consolidate --commit` must not mutate wiki state while the bundle
    run lock is held by another owner — it exits EXIT_LOCK_HELD (2)."""
    import json as _json

    from typer.testing import CliRunner

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.run.lock import run_lock
    from wikify.cli import app

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    store.close()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        _json.dumps({"artifact_id": "gpc", "title": "ALD GPC", "properties": ["GPC"]}),
        encoding="utf-8",
    )
    runner = CliRunner()
    with run_lock(bundle, owner="someone-else"):
        res = runner.invoke(
            app, ["data", "consolidate", str(spec_path), "--run", str(bdir), "--commit"]
        )
    assert res.exit_code == 2, res.output
    # No page was written while the lock was held.
    assert not list(bundle.wiki_data_dir.glob("*.md")) if bundle.wiki_data_dir.is_dir() else True


def test_wiki_rebuild_restores_data_page_when_both_dbs_lost(tmp_path: Path) -> None:
    """Recovery with BOTH wiki.db and claims.db lost: rebuild_graph must still
    restore a kind=data row from the on-disk page markdown (degraded, doc-level
    evidence) so the data page is not orphaned/split-brain."""
    import sqlite3

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "GPC was 1.1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)

    # Lose both stores; only the on-disk page + sidecar survive.
    bundle.sqlite_path.unlink()
    bundle.claims_db_path.unlink()

    rebuild_graph(bundle)

    con = open_wiki_store(bundle.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT kind FROM wiki_pages WHERE page_id = ?", (page_id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None and row["kind"] == "data", "data page must be restored, not orphaned"


def test_data_artifact_traverses_to_evidence(tmp_path: Path) -> None:
    """A first-class data page must traverse to its evidence (the slug-resolution
    helpers now include wiki/data), so `wiki traverse <data> --to evidence` works."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.queries import traverse_page
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle",
                                "doc_7", "doc_7__c0003_beef", "GPC was 1.1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    page_id = write_artifact_page(bundle.wiki_data_dir, spec, table).stem
    register_artifact_wiki_page(bundle, spec, table)

    rows = traverse_page(bundle, slug=page_id, relation="evidence")
    assert any(r["chunk_id"] == "doc_7__c0003_beef" for r in rows), rows


def test_wiki_rebuild_data_row_does_not_advance_past_committed(tmp_path: Path) -> None:
    """wiki rebuild is a projection of COMMITTED disk state: a claim added after
    the artifact was committed (but before `data rebuild`) must NOT appear in
    the wiki.db row, or DB search/traverse would expose rows absent from the
    committed markdown page."""
    import sqlite3

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "chunkA", "GPC 1.1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)
    # A new matching claim lands AFTER commit (not yet in the committed page).
    store.add_points([_verified("HfO2", "GPC", "1.0", "A/cycle", "d2", "chunkB", "GPC 1.0")])
    store.close()

    rebuild_graph(bundle)

    con = open_wiki_store(bundle.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        chunk_ids = {
            r["chunk_id"] for r in con.execute(
                "SELECT chunk_id FROM wiki_evidence WHERE page_id = ?", (page_id,)
            ).fetchall()
        }
    finally:
        con.close()
    assert "chunkA" in chunk_ids
    assert "chunkB" not in chunk_ids  # uncommitted claim must not leak into wiki.db


def test_data_page_navigation_url_and_freshness(tmp_path: Path) -> None:
    """A committed data page is first-class in navigation: it gets a data/...html
    URL, and modifying its markdown makes navigation stale."""
    import os

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.navigation import (
        build_navigation_context,
        navigation_is_fresh,
        navigation_path,
        write_navigation,
    )
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    md_path = write_artifact_page(bundle.wiki_data_dir, spec, table)
    register_artifact_wiki_page(bundle, spec, table)

    data_pages = [p for p in build_navigation_context(bundle)["pages"] if p["kind"] == "data"]
    assert data_pages, "data page must appear in navigation context"
    assert data_pages[0]["url"].startswith("data/"), data_pages[0]["url"]

    write_navigation(bundle, {"groups": []})
    nav_mtime = navigation_path(bundle).stat().st_mtime
    os.utime(md_path, (nav_mtime + 10, nav_mtime + 10))
    assert navigation_is_fresh(bundle) is False


def test_data_artifact_cannot_overwrite_existing_page_row(tmp_path: Path) -> None:
    """A data artifact whose id collides with an existing article/person page
    must be refused, not silently overwrite that row (wiki_pages is keyed by
    page_id alone)."""
    import pytest

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.store import open_wiki_store, upsert_wiki_page
    from wikify.data.artifact_page import (
        DataPageCollisionError,
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    # An existing article whose page_id equals a would-be data artifact title.
    con = open_wiki_store(bundle.sqlite_path)
    upsert_wiki_page(con, page_id="ALD GPC", slug="ald-gpc", title="ALD GPC",
                     kind="article", body="article body", frontmatter={},
                     evidence=[], links=[])
    con.commit()
    con.close()

    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)

    with pytest.raises(DataPageCollisionError):
        register_artifact_wiki_page(bundle, spec, table)

    # The article row is intact (kind/body unchanged).
    con = open_wiki_store(bundle.sqlite_path)
    try:
        row = con.execute(
            "SELECT kind, body FROM wiki_pages WHERE page_id = ?", ("ALD GPC",)
        ).fetchone()
    finally:
        con.close()
    assert row[0] == "article"
    assert row[1] == "article body"


def test_cli_consolidate_commit_collision_leaves_no_files(tmp_path: Path) -> None:
    """A colliding `data consolidate --commit` (id already a non-data page) must
    exit nonzero AND leave no orphaned wiki/data files on disk."""
    import json as _json

    from typer.testing import CliRunner

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.store import open_wiki_store, upsert_wiki_page
    from wikify.cli import app

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    con = open_wiki_store(bundle.sqlite_path)
    upsert_wiki_page(con, page_id="ALD GPC", slug="ald-gpc", title="ALD GPC",
                     kind="article", body="article body", frontmatter={},
                     evidence=[], links=[])
    con.commit()
    con.close()
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    store.close()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        _json.dumps({"artifact_id": "gpc", "title": "ALD GPC", "properties": ["GPC"]}),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        app, ["data", "consolidate", str(spec_path), "--run", str(bdir), "--commit"]
    )
    assert res.exit_code == 1, res.output  # EXIT_VALIDATION (collision)
    # No orphaned page/sidecar written for the rejected commit.
    if bundle.wiki_data_dir.is_dir():
        assert not list(bundle.wiki_data_dir.glob("*.md"))
        assert not list(bundle.wiki_data_dir.glob("*.dataspec.json"))


def test_duplicate_data_titles_different_artifacts_collide(tmp_path: Path) -> None:
    """Two data artifacts with the same title (different artifact_id) map to the
    same page_id; committing the second must be refused so it cannot overwrite
    the first artifact's page/row, while re-committing the SAME artifact is OK."""
    import pytest

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.data.artifact_page import (
        DataPageCollisionError,
        check_data_page_id_free,
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec_a = ArtifactSpec(artifact_id="A", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec_a)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec_a, table)
    register_artifact_wiki_page(bundle, spec_a, table)

    with pytest.raises(DataPageCollisionError):
        check_data_page_id_free(bundle, "ALD GPC", "B")  # different artifact, same title
    check_data_page_id_free(bundle, "ALD GPC", "A")  # same artifact -> allowed


def test_collision_check_sees_disk_article_without_db_row(tmp_path: Path) -> None:
    """On-disk article/person markdown is authoritative: a data commit must be
    refused when an article with the same id exists on disk even if wiki.db has
    no row for it (deleted/stale projection)."""
    import pytest

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.data.artifact_page import (
        DataPageCollisionError,
        check_data_page_id_free,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    bundle.wiki_articles_dir.mkdir(parents=True, exist_ok=True)
    (bundle.wiki_articles_dir / "ald-gpc.md").write_text(
        "---\nid: ALD GPC\nkind: article\ntitle: ALD GPC\naliases: []\nlinks: []\n---\n"
        "# ALD GPC\n\nbody\n",
        encoding="utf-8",
    )
    # wiki.db has no such row; the authoritative on-disk article must still block.
    with pytest.raises(DataPageCollisionError):
        check_data_page_id_free(bundle, "ALD GPC", "gpc")


def test_navigation_export_skips_gracefully_under_run_lock(tmp_path: Path) -> None:
    """navigation's wiki.db rebuild is serialized under the run lock; if the
    bundle is locked it skips the DB export gracefully (file-only), never
    crashing or racing."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.run.lock import run_lock
    from wikify.bundle.wiki.navigation import navigation_path, write_navigation

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    with run_lock(bundle, owner="someone-else"):
        write_navigation(bundle, {"groups": []})
    assert navigation_path(bundle).is_file()


def test_rebuild_collision_skip_is_observable(tmp_path: Path) -> None:
    """When a data page is skipped on rebuild because its id collides with a
    non-data page, a data_page_collision_skipped event is emitted (not silent)."""
    from wikify.api import Bundle
    from wikify.bundle.run.events import read_events
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.store import open_wiki_store, upsert_wiki_page
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        register_committed_data_pages,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([_verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1")])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    store.close()
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)
    con = open_wiki_store(bundle.sqlite_path)
    upsert_wiki_page(con, page_id=page_id, slug="ald-gpc", title=page_id,
                     kind="article", body="article", frontmatter={},
                     evidence=[], links=[])
    con.commit()
    con.close()

    register_committed_data_pages(bundle)

    assert any(e.type == "data_page_collision_skipped" for e in read_events(bundle))


def test_rebuild_does_not_publish_shrunken_data_table(tmp_path: Path) -> None:
    """If some committed claims were deleted from the store, the restricted
    lossless reconstruction is smaller than the committed snapshot; rebuild must
    NOT publish that shrunken table — it preserves the committed wiki.db row so
    wiki.db cannot silently diverge from the authoritative markdown."""
    import sqlite3

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.wiki.derived import rebuild_graph
    from wikify.bundle.wiki.store import open_wiki_store
    from wikify.data.artifact_page import (
        register_artifact_wiki_page,
        write_artifact_page,
    )

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    store = DataStore.open(bundle.root)
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "cA", "GPC 1.1"),
        _verified("HfO2", "GPC", "1.0", "A/cycle", "d2", "cB", "GPC 1.0"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD GPC", properties=["GPC"])
    table = consolidate(store, spec)
    committed_claim_ids = list(table.claim_ids)
    assert len(committed_claim_ids) == 2
    write_artifact_page(bundle.wiki_data_dir, spec, table)
    page_id = register_artifact_wiki_page(bundle, spec, table)
    store.close()

    # One committed claim is deleted from the store after commit.
    raw = sqlite3.connect(str(bundle.claims_db_path))
    raw.execute("DELETE FROM data_points WHERE claim_id = ?", (committed_claim_ids[0],))
    raw.commit()
    raw.close()

    rebuild_graph(bundle)

    con = open_wiki_store(bundle.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        chunk_ids = {
            r["chunk_id"] for r in con.execute(
                "SELECT chunk_id FROM wiki_evidence WHERE page_id = ?", (page_id,)
            ).fetchall()
        }
    finally:
        con.close()
    # Both committed evidence rows survive (not shrunk to the single remaining claim).
    assert {"cA", "cB"} <= chunk_ids, chunk_ids


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


def test_artifacts_for_chunks_after_commit(tmp_path: Path) -> None:
    """A committed artifact is discoverable from any chunk backing it — the
    join that lets a concept page surface its related data artifact."""
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1", "A/cycle", "d1", "c1", "q1"),
        _verified("HfO2", "GPC", "1.0", "A/cycle", "d2", "c2", "q2"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="GPC Table", properties=["GPC"])
    table = consolidate(store, spec)
    store.upsert_artifact(spec, n_rows=table.n_rows)
    store.set_artifact_claims(spec.artifact_id, table.claim_ids)
    # Draft (not committed) -> not surfaced.
    assert store.artifacts_for_chunks(["c1"]) == []
    store.set_artifact_status(spec.artifact_id, "committed")
    hits = store.artifacts_for_chunks(["c1"])
    assert [h["artifact_id"] for h in hits] == ["gpc"]
    # A chunk that backs nothing returns no artifact.
    assert store.artifacts_for_chunks(["nope"]) == []


def test_related_data_cross_link_rendered(tmp_path: Path) -> None:
    """A concept page that shares an evidence source with a data artifact gets
    an automatic 'Related data' link to it at render time."""
    from wikify.bundle.wiki.page import load_bundle
    from wikify.render.html.render import build_site

    wiki = tmp_path / "wiki"
    (wiki / "articles").mkdir(parents=True)
    (wiki / "data").mkdir(parents=True)
    # Article cites doc handle 'doc:abc123def456'; data page cites the canonical
    # form of the same doc — they must still cross-link (doc-key normalization).
    (wiki / "articles" / "Atomic Layer Deposition.md").write_text(
        "---\nid: Atomic Layer Deposition\nkind: article\n"
        "title: Atomic Layer Deposition\naliases: []\nlinks: []\n---\n\n"
        "# Atomic Layer Deposition\n\n"
        + ("ALD is a self-limiting thin-film growth technique. " * 6)
        + "[^e1]\n\n## References\n\n"
        '[^e1]: c0 (doc:abc123def456) > "ALD is a thin-film technique"\n',
        encoding="utf-8",
    )
    store = DataStore(tmp_path / "claims.db")
    store.add_points([
        _verified("Al2O3", "GPC", "1.1 A/cycle", "A/cycle",
                  "paper_2020_abc123def456", "c1", "GPC 1.1"),
    ])
    spec = ArtifactSpec(artifact_id="gpc", title="ALD Growth Per Cycle", properties=["GPC"])
    write_artifact_page(wiki / "data", spec, consolidate(store, spec))

    out = tmp_path / "site"
    build_site(load_bundle(wiki), out, corpus_root=None)
    article = (out / "articles" / "Atomic_Layer_Deposition.html").read_text(encoding="utf-8")
    assert "Related data" in article
    assert "ALD_Growth_Per_Cycle.html" in article


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


def test_related_data_is_doc_level_not_chunk_level(tmp_path: Path) -> None:
    """`related` artifacts match on source DOCUMENT, not chunk. The DATA wave
    harvests the number-dense chunk the article explorer skips, so an artifact
    and the page it generalizes share a doc but not a chunk. A page whose
    evidence is a DIFFERENT chunk of that doc still surfaces the artifact,
    while `points` stay chunk-level."""
    from wikify.bundle.draft.builder import _data_for_evidence

    corpus = _numeric_corpus(tmp_path / "corpus")
    bundle = _init_bundle(tmp_path, corpus)
    store = DataStore.open(bundle.root)
    # The verified number lives on doc_a's number-dense chunk c0000.
    store.add_points([
        _verified("Al2O3", "Growth per cycle", "1.1 A/cycle", "A/cycle",
                  "doc_a", "doc_a__c0000", "growth per cycle of 1.1 A/cycle"),
    ])
    spec = ArtifactSpec(
        artifact_id="gpc", title="GPC Table", properties=["Growth per cycle"]
    )
    table = consolidate(store, spec)
    store.upsert_artifact(spec, n_rows=table.n_rows)
    store.set_artifact_claims(spec.artifact_id, table.claim_ids)
    store.set_artifact_status(spec.artifact_id, "committed")
    store.close()

    # The page's evidence is a DIFFERENT chunk of the SAME document.
    points, related = _data_for_evidence(bundle, {"doc_a__c0009"}, {"doc_a"})
    # Doc-level: the artifact surfaces even though the chunk sets are disjoint
    # (a chunk-level join would have returned []).
    assert [a["title"] for a in related] == ["GPC Table"]
    # Chunk-level: the number's chunk is absent from evidence -> no point.
    assert points == []

    # Positive control: same chunk -> the point IS citable (chunk-level).
    points_hit, _ = _data_for_evidence(bundle, {"doc_a__c0000"}, {"doc_a"})
    assert [p["value"] for p in points_hit] == ["1.1 A/cycle"]


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


# --------------------------------------------------------------------------
# property-targeted whole-corpus data harvest (harvest-property)
# --------------------------------------------------------------------------


def _doc_with_chunk(doc_id: str, text: str) -> tuple[Document, list[Chunk]]:
    doc = Document(
        id=doc_id, source_path=f"/p/{doc_id}.pdf", kind="pdf", title=doc_id,
        metadata={}, markdown_path=f"markdown/{doc_id}.md",
        image_dir=f"images/{doc_id}/",
    )
    ch = Chunk(
        id=f"{doc_id}__c0000", doc_id=doc_id, ord=0, text=text,
        char_span=(0, len(text)), section_path=["Body"], section_type="body",
    )
    return doc, [ch]


# N docs mention the property across the WHOLE corpus (phrase, alias, or unit);
# one control doc mentions none. A doc-slice scan would only see its slice.
_SWEEP_DOCS = [
    ("d1", "The Al2O3 growth per cycle was 1.1 A/cycle at 200 C over 100 cycles."),
    ("d2", "HfO2 growth per cycle reached 1.0 A/cycle at 250 C for 50 cycles."),
    ("d3", "A GPC of 0.9 A/cycle was recorded for TiO2 across 300 cycles at 150 C."),
    ("d4", "ZnO deposition gave 2.0 A/cycle at 180 C over 400 cycles here."),
    ("d5", "This review discusses precursor chemistry and reactor design in 2020."),
]


def _sweep_corpus(make_sqlite_corpus) -> Corpus:
    return make_sqlite_corpus([_doc_with_chunk(d, t) for d, t in _SWEEP_DOCS])


def test_harvest_property_enumerates_whole_corpus(make_sqlite_corpus) -> None:
    """Every doc mentioning the property (phrase, alias, or unit) is a
    candidate -- NOT just a doc-list slice. d1-d4 mention it, d5 does not."""
    corpus = _sweep_corpus(make_sqlite_corpus)
    sweep = sweep_property_candidates(
        corpus, phrasings=["growth per cycle", "GPC"], units=["A/cycle"]
    )
    mentioning = set(sweep["docs_mentioning"])
    assert mentioning == {"d1", "d2", "d3", "d4"}  # d4 via unit; d5 excluded
    cand_docs = {c["doc_id"] for c in sweep["candidates"]}
    assert cand_docs == {"d1", "d2", "d3", "d4"}
    assert sweep["candidate_chunks"] == 4
    assert sweep["truncated"] is False
    # The alias 'GPC' is what tags d3 -- the whole-corpus sweep found it.
    d3 = next(c for c in sweep["candidates"] if c["doc_id"] == "d3")
    assert d3["matched_phrasing"] == "gpc"

    # Contrast with the old doc-list-scoped path: scanning one doc's slice
    # sees only that doc, missing the property mentions in d2-d4.
    slice_docs = {c["doc_id"] for c in number_dense_chunks(corpus, doc_ids=["d1"])}
    assert slice_docs == {"d1"}
    assert len(mentioning) > len(slice_docs)


def test_harvest_property_include_text_returns_chunk_body(make_sqlite_corpus) -> None:
    corpus = _sweep_corpus(make_sqlite_corpus)
    sweep = sweep_property_candidates(
        corpus, phrasings=["growth per cycle"], units=["A/cycle"],
        include_text=True,
    )
    assert all("text" in c for c in sweep["candidates"])
    d1 = next(c for c in sweep["candidates"] if c["doc_id"] == "d1")
    assert "growth per cycle" in d1["text"]


def test_harvest_property_truncation_flag(make_sqlite_corpus) -> None:
    """Over the cap: candidate list truncates but docs_mentioning stays full,
    so the recall denominator is unaffected by the cap."""
    corpus = _sweep_corpus(make_sqlite_corpus)
    sweep = sweep_property_candidates(
        corpus, phrasings=["growth per cycle", "GPC"], units=["A/cycle"],
        max_chunks=2,
    )
    assert sweep["truncated"] is True
    assert sweep["candidate_chunks"] == 2
    assert sweep["matched_chunks"] == 4
    assert len(sweep["docs_mentioning"]) == 4  # full set despite the cap


def test_cli_harvest_property_json_shape_and_recall(
    make_sqlite_corpus, tmp_path: Path
) -> None:
    """CLI --format json: recall report keys/values + candidate rows, with
    data_recall = docs_in_table / docs_mentioning_property."""
    from wikify.bundle.run.lifecycle import init_run

    corpus = _sweep_corpus(make_sqlite_corpus)
    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path=str(corpus.root))
    store = DataStore.open(bundle.root)
    # Two of the four mentioning docs already have a verified claim in the
    # table -> docs_in_table = 2, docs_mentioning = 4 -> recall 0.5.
    store.add_points([
        _verified("Al2O3", "growth per cycle", "1.1", "A/cycle", "d1", "d1__c0000", "q1"),
        _verified("HfO2", "growth per cycle", "1.0", "A/cycle", "d2", "d2__c0000", "q2"),
    ])
    store.close()

    res = runner.invoke(app, [
        "data", "harvest-property",
        "--property", "growth per cycle",
        "--alias", "GPC", "--alias", "per-cycle growth",
        "--unit", "A/cycle",
        "--corpus", str(corpus.root), "--run", str(bdir),
        "--format", "json",
    ])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    report = payload["report"]
    assert set(report) == {
        "property", "docs_mentioning_property", "candidate_chunks",
        "docs_in_table", "data_recall", "truncated",
    }
    assert report["property"] == "growth per cycle"
    assert report["docs_mentioning_property"] == 4
    assert report["docs_in_table"] == 2
    assert report["data_recall"] == 0.5
    assert report["truncated"] is False
    assert "warning" not in payload  # 3 distinct phrasings supplied
    assert payload["docs_extracted"] == 2
    assert payload["candidates"], "worklist must be non-empty"
    for c in payload["candidates"]:
        assert set(c) >= {"doc_id", "chunk_id", "matched_phrasing", "source_kind"}

    # The sweep bookkeeping was persisted to the claim store.
    store = DataStore.open(bundle.root)
    try:
        rec = store.get_property_sweep(normalize_key("growth per cycle"))
    finally:
        store.close()
    assert rec is not None
    assert rec["docs_mentioning"] == 4 and rec["docs_in_table"] == 2
    assert rec["candidate_chunks"] == 4 and rec["last_sweep"]


def test_cli_harvest_property_warns_below_alias_min(
    make_sqlite_corpus, tmp_path: Path
) -> None:
    """Fewer than PROPERTY_ALIAS_MIN distinct phrasings surfaces a warning."""
    from wikify.bundle.run.lifecycle import init_run

    corpus = _sweep_corpus(make_sqlite_corpus)
    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path=str(corpus.root))

    res = runner.invoke(app, [
        "data", "harvest-property", "--property", "growth per cycle",
        "--unit", "A/cycle", "--corpus", str(corpus.root), "--run", str(bdir),
        "--format", "json",
    ])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert "warning" in payload  # only 1 phrasing (the canonical name)
