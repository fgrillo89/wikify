"""Tests for the arXiv harvester (core + CLI), all offline via MockTransport."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from wikify.cli import app
from wikify.sources import arxiv as arxiv_src

runner = CliRunner()


# --------------------------------------------------------------------------
# OAI XML fixtures
# --------------------------------------------------------------------------

def _record_xml(arxiv_id: str, *, deleted: bool = False) -> str:
    if deleted:
        return (
            f'<record><header status="deleted">'
            f"<identifier>oai:arXiv.org:{arxiv_id}</identifier>"
            f"<datestamp>2023-01-03</datestamp></header></record>"
        )
    return f"""<record>
      <header>
        <identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>2023-01-03</datestamp>
        <setSpec>cs:cs:LG</setSpec>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{arxiv_id}</id>
          <created>2023-01-01</created>
          <updated>2023-01-02</updated>
          <authors><author><keyname>Doe</keyname><forenames>Jane</forenames></author></authors>
          <title>Paper {arxiv_id}</title>
          <categories>cs.LG cs.AI</categories>
          <abstract>Abstract for {arxiv_id}</abstract>
          <doi>10.1000/{arxiv_id}</doi>
        </arXiv>
      </metadata>
    </record>"""


def _listrecords_xml(records: list[str], token: str) -> str:
    token_xml = (
        f'<resumptionToken completeListSize="3" cursor="0">{token}</resumptionToken>'
        if token
        else '<resumptionToken completeListSize="3" cursor="2"></resumptionToken>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <responseDate>2024-01-01T00:00:00Z</responseDate>
      <request verb="ListRecords">https://oaipmh.arxiv.org/oai</request>
      <ListRecords>
        {"".join(records)}
        {token_xml}
      </ListRecords>
    </OAI-PMH>"""


def _two_page_oai_handler() -> httpx.MockTransport:
    """Page 1 -> two records + token TOK1; page 2 (TOK1) -> one record, end."""
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("resumptionToken") == "TOK1":
            body = _listrecords_xml([_record_xml("2301.00003")], token="")
        else:
            assert params.get("set") == "cs:cs:LG"
            assert params.get("metadataPrefix") == "arXiv"
            body = _listrecords_xml(
                [_record_xml("2301.00001"), _record_xml("2301.00002")], token="TOK1"
            )
        return httpx.Response(200, text=body)

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------
# setspec + parsing
# --------------------------------------------------------------------------

def test_setspec_for_category():
    assert arxiv_src.setspec_for_category("cs.LG") == "cs:cs:LG"
    assert arxiv_src.setspec_for_category("stat.ML") == "stat:stat:ML"
    # physics-group archives get the physics group prefix.
    assert arxiv_src.setspec_for_category("cond-mat.mtrl-sci") == "physics:cond-mat:mtrl-sci"
    assert arxiv_src.setspec_for_category("physics.optics") == "physics:physics:optics"
    # raw setSpec and bare archive pass through.
    assert arxiv_src.setspec_for_category("physics:cond-mat:mtrl-sci") == (
        "physics:cond-mat:mtrl-sci"
    )
    assert arxiv_src.setspec_for_category("hep-th") == "hep-th"


def test_setspec_rejects_unknown_archive():
    with pytest.raises(arxiv_src.UnknownArxivCategoryError):
        arxiv_src.setspec_for_category("bogus.XX")


def test_parse_record_fields():
    from xml.etree import ElementTree as ET

    root = ET.fromstring(_listrecords_xml([_record_xml("2301.00001")], token=""))
    rec_el = root.find(f"{arxiv_src.OAI_NS}ListRecords").find(f"{arxiv_src.OAI_NS}record")
    rec = arxiv_src.parse_record(rec_el)
    assert rec is not None
    assert rec.arxiv_id == "2301.00001"
    assert rec.title == "Paper 2301.00001"
    assert rec.authors == ["Jane Doe"]
    assert rec.categories == ["cs.LG", "cs.AI"]
    assert rec.primary_category == "cs.LG"
    assert rec.doi == "10.1000/2301.00001"
    assert rec.pdf_url == "https://export.arxiv.org/pdf/2301.00001"
    assert rec.pdf_filename == "2301.00001.pdf"


def test_parse_record_deleted_is_none():
    from xml.etree import ElementTree as ET

    root = ET.fromstring(_listrecords_xml([_record_xml("x", deleted=True)], token=""))
    rec_el = root.find(f"{arxiv_src.OAI_NS}ListRecords").find(f"{arxiv_src.OAI_NS}record")
    assert arxiv_src.parse_record(rec_el) is None


def test_parse_record_old_style_id_filename():
    from xml.etree import ElementTree as ET

    root = ET.fromstring(_listrecords_xml([_record_xml("cs/0503069")], token=""))
    rec_el = root.find(f"{arxiv_src.OAI_NS}ListRecords").find(f"{arxiv_src.OAI_NS}record")
    rec = arxiv_src.parse_record(rec_el)
    assert rec.pdf_filename == "cs_0503069.pdf"


# --------------------------------------------------------------------------
# harvest (phase 1)
# --------------------------------------------------------------------------

def test_harvest_paginates_and_completes(tmp_path: Path):
    report = arxiv_src.harvest(
        ["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler()
    )
    assert report.harvested == 3
    assert report.complete_list_size == 3
    assert report.already_done is False

    records = arxiv_src.read_records(tmp_path)
    assert [r.arxiv_id for r in records] == [
        "2301.00001", "2301.00002", "2301.00003",
    ]
    assert all(r.status == "pending" for r in records)

    state = arxiv_src.read_state(tmp_path)
    assert state["done"] is True
    assert state["resumption_token"] == ""


def test_harvest_already_done_is_noop(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())

    def fail_handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no OAI request expected on a completed harvest")

    report = arxiv_src.harvest(
        ["cs:cs:LG"], tmp_path, delay_s=0.0, transport=httpx.MockTransport(fail_handler)
    )
    assert report.already_done is True
    assert report.harvested == 3


def test_harvest_recovers_from_expired_token(tmp_path: Path):
    # An expired resumptionToken must restart the current set rather than
    # wedge the harvest permanently.
    arxiv_src.write_state(tmp_path, {
        "sets": ["cs:cs:LG"], "metadata_prefix": "arXiv", "pending_sets": [],
        "current_set": "cs:cs:LG", "resumption_token": "DEAD", "harvested": 0,
        "set_sizes": {}, "complete_list_size": None, "done": False,
    })

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.params.get("resumptionToken") == "DEAD":
            body = """<?xml version="1.0"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <error code="badResumptionToken">expired</error>
            </OAI-PMH>"""
            return httpx.Response(200, text=body)
        # fresh ListRecords (no token) -> one record, end.
        return httpx.Response(200, text=_listrecords_xml([_record_xml("2301.00009")], token=""))

    report = arxiv_src.harvest(
        ["cs:cs:LG"], tmp_path, delay_s=0.0, transport=httpx.MockTransport(handler)
    )
    assert report.harvested == 1
    assert [r.arxiv_id for r in arxiv_src.read_records(tmp_path)] == ["2301.00009"]
    state = arxiv_src.read_state(tmp_path)
    assert state["done"] is True


def test_harvest_resumes_from_saved_token(tmp_path: Path):
    # Simulate an interruption after page 1: manifest has 2 records, state
    # points at TOK1 for the current set.
    arxiv_src.append_records(tmp_path, [
        arxiv_src.ArxivRecord(
            arxiv_id="2301.00001", title="Paper 2301.00001", authors=["Jane Doe"],
            summary="Abstract for 2301.00001", categories=["cs.LG", "cs.AI"],
            primary_category="cs.LG", published="2023-01-01", updated="2023-01-02",
            doi="10.1000/2301.00001", journal_ref="",
            pdf_url="https://arxiv.org/pdf/2301.00001", pdf_filename="2301.00001.pdf",
        ),
        arxiv_src.ArxivRecord(
            arxiv_id="2301.00002", title="Paper 2301.00002", authors=["Jane Doe"],
            summary="Abstract for 2301.00002", categories=["cs.LG", "cs.AI"],
            primary_category="cs.LG", published="2023-01-01", updated="2023-01-02",
            doi="10.1000/2301.00002", journal_ref="",
            pdf_url="https://arxiv.org/pdf/2301.00002", pdf_filename="2301.00002.pdf",
        ),
    ])
    arxiv_src.write_state(tmp_path, {
        "sets": ["cs:cs:LG"], "metadata_prefix": "arXiv", "pending_sets": [],
        "current_set": "cs:cs:LG", "resumption_token": "TOK1", "harvested": 2,
        "complete_list_size": 3, "done": False,
    })

    report = arxiv_src.harvest(
        ["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler()
    )
    assert report.resumed is True
    assert report.harvested == 3
    ids = [r.arxiv_id for r in arxiv_src.read_records(tmp_path)]
    assert ids == ["2301.00001", "2301.00002", "2301.00003"]  # no duplicates


def test_harvest_rejects_state_for_different_categories(tmp_path: Path):
    # Complete a harvest for one set, then request a different set in the
    # same dir: must refuse rather than silently report already_done.
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    with pytest.raises(arxiv_src.HarvestStateMismatchError):
        arxiv_src.harvest(["cs:cs:AI"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())


def test_harvest_resume_is_order_insensitive(tmp_path: Path):
    # Re-running with the same sets in a different order still resumes.
    arxiv_src.write_state(tmp_path, {
        "sets": ["cs:cs:LG", "cs:cs:AI"], "metadata_prefix": "arXiv",
        "pending_sets": [], "current_set": None, "resumption_token": "",
        "harvested": 0, "set_sizes": {}, "complete_list_size": None, "done": True,
    })
    report = arxiv_src.harvest(
        ["cs:cs:AI", "cs:cs:LG"], tmp_path, delay_s=0.0,
        transport=_two_page_oai_handler(),
    )
    assert report.already_done is True


# --------------------------------------------------------------------------
# download (phase 2)
# --------------------------------------------------------------------------

def _pdf_handler(missing: set[str] | None = None) -> httpx.MockTransport:
    missing = missing or set()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "export.arxiv.org"
        arxiv_id = request.url.path.removeprefix("/pdf/")
        if arxiv_id in missing:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf")

    return httpx.MockTransport(handler)


def test_download_all_writes_pdfs_and_flips_status(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())

    report = arxiv_src.download_all(
        tmp_path, concurrency=2, rate=100.0, transport=_pdf_handler()
    )
    assert report.downloaded == 3
    assert report.skipped == 0
    assert report.failed == []
    for name in ("2301.00001.pdf", "2301.00002.pdf", "2301.00003.pdf"):
        assert (tmp_path / name).read_bytes() == b"%PDF-1.4 fake pdf"
    assert all(r.status == "done" for r in arxiv_src.read_records(tmp_path))


def test_download_all_resumes_skipping_existing(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    arxiv_src.download_all(tmp_path, concurrency=2, rate=100.0, transport=_pdf_handler())

    def fail_handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no PDF request expected when all files exist")

    report = arxiv_src.download_all(
        tmp_path, concurrency=2, rate=100.0, transport=httpx.MockTransport(fail_handler)
    )
    assert report.downloaded == 0
    assert report.skipped == 3


def test_download_retries_on_429(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:  # throttle the very first GET, then recover.
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf")

    report = arxiv_src.download_all(
        tmp_path, concurrency=1, rate=1000.0, transport=httpx.MockTransport(handler)
    )
    assert report.downloaded == 3
    assert report.failed == []  # 429 backed off and retried, not failed.


def test_download_respects_concurrency_ceiling(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    inflight = {"now": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        inflight["now"] += 1
        inflight["max"] = max(inflight["max"], inflight["now"])
        await asyncio.sleep(0.02)
        inflight["now"] -= 1
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf")

    report = arxiv_src.download_all(
        tmp_path, concurrency=2, rate=1000.0, transport=httpx.MockTransport(handler)
    )
    assert report.downloaded == 3
    assert inflight["max"] <= 2  # semaphore caps simultaneous downloads.


def test_download_defaults_match_pdf_rate():
    # PDF phase defaults to arXiv's PDF-friendly ~4 req/s, 4 connections.
    assert arxiv_src._PDF_CONCURRENCY == 4
    assert arxiv_src._PDF_RATE == 4.0


def test_download_checkpoint_preserves_appended_records(tmp_path: Path):
    # Simulate a concurrent `identify` appending a record mid-download: the
    # checkpoint rewrite must merge by id, not clobber the new record.
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    appended = arxiv_src.ArxivRecord(
        arxiv_id="2301.99999", title="Late", authors=[], summary="", categories=["cs.LG"],
        primary_category="cs.LG", published="", updated="", doi="", journal_ref="",
        pdf_url=arxiv_src.PDF_BASE + "2301.99999", pdf_filename="2301.99999.pdf",
    )
    state = {"appended": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not state["appended"]:
            arxiv_src.append_records(tmp_path, [appended])  # concurrent identify write
            state["appended"] = True
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf")

    arxiv_src.download_all(
        tmp_path, concurrency=1, rate=1000.0, transport=httpx.MockTransport(handler)
    )
    ids = {r.arxiv_id for r in arxiv_src.read_records(tmp_path)}
    assert "2301.99999" in ids  # appended record survived the checkpoint


def test_download_all_records_failures(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    report = arxiv_src.download_all(
        tmp_path, concurrency=2, rate=100.0, transport=_pdf_handler(missing={"2301.00002"})
    )
    assert report.downloaded == 2
    assert len(report.failed) == 1
    assert report.failed[0]["arxiv_id"] == "2301.00002"
    assert not (tmp_path / "2301.00002.pdf").exists()


# --------------------------------------------------------------------------
# scout (Query API discovery)
# --------------------------------------------------------------------------

def _atom_entry(arxiv_id: str, primary: str, extra: list[str]) -> str:
    cats = "".join(f'<category term="{c}"/>' for c in [primary, *extra])
    return f"""<entry>
      <id>http://arxiv.org/abs/{arxiv_id}v1</id>
      <title>Paper {arxiv_id}</title>
      <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="{primary}"/>
      {cats}
    </entry>"""


def _atom_feed(entries: list[str], total: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>{total}</opensearch:totalResults>
      {"".join(entries)}
    </feed>"""


def _query_handler() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("search_query") == "all:machine learning"
        feed = _atom_feed([
            _atom_entry("2301.00001", "cs.LG", ["cs.AI"]),
            _atom_entry("2301.00002", "cs.LG", []),
            _atom_entry("2301.00003", "stat.ML", ["cs.LG"]),
        ], total=4242)
        return httpx.Response(200, text=feed)

    return httpx.MockTransport(handler)


def test_scout_builds_primary_histogram(tmp_path: Path):
    report = arxiv_src.scout(
        "all:machine learning", max_results=200, transport=_query_handler()
    )
    assert report.total_results == 4242
    assert report.sampled == 3
    assert report.primary_histogram[0] == {
        "category": "cs.LG", "count": 2, "setspec": "cs:cs:LG",
    }
    cats = {row["category"]: row["count"] for row in report.primary_histogram}
    assert cats == {"cs.LG": 2, "stat.ML": 1}


def test_cli_scout_envelope(monkeypatch):
    def fake_scout(query, **kwargs):
        return arxiv_src.ScoutReport(
            query=query, total_results=100, sampled=2,
            primary_histogram=[{"category": "cs.LG", "count": 2, "setspec": "cs:cs:LG"}],
        )

    monkeypatch.setattr(arxiv_src, "scout", fake_scout)
    result = runner.invoke(app, [
        "arxiv", "scout", "all:machine learning", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["total_results"] == 100
    assert payload["primary_histogram"][0]["setspec"] == "cs:cs:LG"


def test_cli_scout_rejects_nonpositive_max():
    result = runner.invoke(app, ["arxiv", "scout", "x", "--max", "0"])
    assert result.exit_code == 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_identify_maps_categories_to_setspecs(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_harvest(sets, out, **kwargs):
        captured["sets"] = sets
        captured["out"] = out
        return arxiv_src.HarvestReport(harvested=5, complete_list_size=5, resumed=False)

    monkeypatch.setattr(arxiv_src, "harvest", fake_harvest)
    result = runner.invoke(app, [
        "arxiv", "identify", "--category", "cs.LG", "--category", "cs.AI",
        "--out", str(tmp_path), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    assert captured["sets"] == ["cs:cs:LG", "cs:cs:AI"]
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["harvested"] == 5


def test_cli_identify_requires_a_set(tmp_path: Path):
    result = runner.invoke(app, ["arxiv", "identify", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "no_sets" in result.output


def test_cli_identify_rejects_unknown_category(tmp_path: Path):
    result = runner.invoke(app, [
        "arxiv", "identify", "--category", "bogus.XX", "--out", str(tmp_path),
    ])
    assert result.exit_code == 1
    assert "unknown_category" in result.output


def test_cli_identify_rejects_state_mismatch(tmp_path: Path, monkeypatch):
    def boom(sets, out, **kwargs):
        raise arxiv_src.HarvestStateMismatchError(["cs:cs:LG"], list(sets), "arXiv", "arXiv")

    monkeypatch.setattr(arxiv_src, "harvest", boom)
    result = runner.invoke(app, [
        "arxiv", "identify", "--category", "cs.AI", "--out", str(tmp_path),
    ])
    assert result.exit_code == 1
    assert "state_mismatch" in result.output


def test_cli_download_requires_manifest(tmp_path: Path):
    result = runner.invoke(app, ["arxiv", "download", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "no_manifest" in result.output


def _stage_manifest(tmp_path: Path, *, harvest_done: bool = True) -> None:
    arxiv_src.manifest_path(tmp_path).write_text("", encoding="utf-8")
    arxiv_src.write_state(tmp_path, {"sets": ["cs:cs:LG"], "done": harvest_done})


def test_cli_download_envelope(tmp_path: Path, monkeypatch):
    _stage_manifest(tmp_path)

    def fake_download(out, **kwargs):
        return arxiv_src.DownloadReport(downloaded=3, skipped=1, failed=[])

    monkeypatch.setattr(arxiv_src, "download_all", fake_download)
    result = runner.invoke(app, [
        "arxiv", "download", "--out", str(tmp_path), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"ok": True, "downloaded": 3, "skipped": 1, "failed": [],
                       "harvest_done": True, "out": str(tmp_path)}


def test_cli_download_refuses_incomplete_harvest(tmp_path: Path, monkeypatch):
    _stage_manifest(tmp_path, harvest_done=False)
    monkeypatch.setattr(arxiv_src, "download_all",
                        lambda *a, **k: pytest.fail("download must not run"))
    result = runner.invoke(app, ["arxiv", "download", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "harvest_incomplete" in result.output


def test_cli_download_allow_incomplete_harvest(tmp_path: Path, monkeypatch):
    _stage_manifest(tmp_path, harvest_done=False)

    def fake_download(out, **kwargs):
        return arxiv_src.DownloadReport(downloaded=0, skipped=0, failed=[])

    monkeypatch.setattr(arxiv_src, "download_all", fake_download)
    result = runner.invoke(app, [
        "arxiv", "download", "--out", str(tmp_path),
        "--allow-incomplete-harvest", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["harvest_done"] is False


def test_cli_download_fails_when_pdfs_fail(tmp_path: Path, monkeypatch):
    _stage_manifest(tmp_path)

    def fake_download(out, **kwargs):
        return arxiv_src.DownloadReport(
            downloaded=1, skipped=0, failed=[{"arxiv_id": "2301.1", "error": "404"}]
        )

    monkeypatch.setattr(arxiv_src, "download_all", fake_download)
    result = runner.invoke(app, [
        "arxiv", "download", "--out", str(tmp_path), "--format", "json",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"] == "download_incomplete"
    assert payload["failed"][0]["arxiv_id"] == "2301.1"  # failed list preserved


def test_cli_download_allow_partial_exits_zero(tmp_path: Path, monkeypatch):
    _stage_manifest(tmp_path)

    def fake_download(out, **kwargs):
        return arxiv_src.DownloadReport(
            downloaded=1, skipped=0, failed=[{"arxiv_id": "2301.1", "error": "404"}]
        )

    monkeypatch.setattr(arxiv_src, "download_all", fake_download)
    result = runner.invoke(app, [
        "arxiv", "download", "--out", str(tmp_path), "--allow-partial", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["failed"]) == 1


def test_cli_status_reports_counts(tmp_path: Path):
    arxiv_src.harvest(["cs:cs:LG"], tmp_path, delay_s=0.0, transport=_two_page_oai_handler())
    arxiv_src.download_all(tmp_path, concurrency=2, rate=100.0, transport=_pdf_handler())
    result = runner.invoke(app, [
        "arxiv", "status", "--out", str(tmp_path), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == 3
    assert payload["done"] == 3
    assert payload["pending"] == 0
    assert payload["harvest_done"] is True


@pytest.mark.parametrize("flag,value", [("--concurrency", "0"), ("--rate", "0")])
def test_cli_download_rejects_nonpositive(tmp_path: Path, flag, value):
    arxiv_src.manifest_path(tmp_path).write_text("", encoding="utf-8")
    result = runner.invoke(app, [
        "arxiv", "download", "--out", str(tmp_path), flag, value,
    ])
    assert result.exit_code == 1
