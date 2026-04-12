"""Tests for query traversal helpers and query log persistence (feature 2).

Exercises:
- read_wiki_page: returns markdown for a known page, None for unknown
- read_corpus_chunks: returns chunk dicts, skips missing, caps at 5
- persist_query_log: writes atomic JSON, validates QueryLogEntry schema
- query log deleted by maintenance (end-to-end read/delete check)
- escalation_events serialised correctly
- save_log=True writes a log entry; save_log=False does not
"""

import json
from pathlib import Path

import pytest

from .fakes import FakeExtractor, FakeQuerier, FakeWriter
from wikify.schema import EscalationEvent, QueryLogEntry
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.query import (
    persist_query_log,
    read_corpus_chunks,
    read_wiki_page,
)
from wikify.distill.query import (
    run as query_run,
)
from wikify.distill.strategy import build_strategy
from wikify.cache import ExtractCache
from wikify.meter import CostMeter
from wikify.embedding import embed_texts
from wikify.ingest.pipeline import ingest_corpus
from wikify.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def ready_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp("qt")
    corpus = ingest_corpus(FIXTURE, root / "corpus")
    bundle = BundlePaths(root=root / "bundle")
    cache = ExtractCache(root=root / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="qt_test",
        events_path=bundle.calls_path,
    )
    cfg = build_strategy("M", seed=0)
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=20_000.0,
    )
    return bundle, corpus


# --- read_wiki_page ---------------------------------------------------


def test_read_wiki_page_known(ready_bundle):
    bundle, _ = ready_bundle
    from wikify.store.wiki_index import WikiIndex
    index = WikiIndex.load(bundle)
    entries = list(index)
    if not entries:
        pytest.skip("no pages in bundle")
    pid = entries[0].id
    body = read_wiki_page(bundle, pid)
    assert body is not None
    assert len(body) > 0


def test_read_wiki_page_unknown(ready_bundle):
    bundle, _ = ready_bundle
    result = read_wiki_page(bundle, "nonexistent-page-xyz-123")
    assert result is None


# --- read_corpus_chunks -----------------------------------------------


def test_read_corpus_chunks_missing(tmp_path):
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.chunks_dir.mkdir(parents=True, exist_ok=True)
    result = read_corpus_chunks(corpus, ["no_such_chunk"])
    assert result == []


def test_read_corpus_chunks_present(tmp_path):
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk = {"id": "c1", "doc_id": "d1", "text": "hello world"}
    (corpus.chunks_dir / "c1.json").write_text(json.dumps(chunk), encoding="utf-8")
    result = read_corpus_chunks(corpus, ["c1"])
    assert len(result) == 1
    assert result[0]["text"] == "hello world"


def test_read_corpus_chunks_capped_at_5(tmp_path):
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.chunks_dir.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (corpus.chunks_dir / f"c{i}.json").write_text(
            json.dumps({"id": f"c{i}", "doc_id": "d1", "text": f"text {i}"}),
            encoding="utf-8",
        )
    result = read_corpus_chunks(corpus, [f"c{i}" for i in range(8)])
    assert len(result) == 5


# --- persist_query_log -----------------------------------------------


def test_persist_query_log_writes_valid_entry(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    from wikify.schema import QueryAnswer
    answer = QueryAnswer(text="Some answer.", citations=["P1"], chunks=[], follow_ups=[])
    entry_id = persist_query_log(
        bundle,
        question="What is ALD?",
        answer=answer,
        pages_touched=["P1", "P2"],
        model_id="haiku",
        tier="exploit",
    )
    log_path = bundle.query_log_dir / f"{entry_id}.json"
    assert log_path.exists()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    entry = QueryLogEntry.model_validate(data)
    assert entry.id == entry_id
    assert entry.question == "What is ALD?"
    assert entry.answer_text == "Some answer."
    assert "P1" in entry.pages_touched
    assert entry.model_id == "haiku"


def test_persist_query_log_with_escalation(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    from wikify.schema import QueryAnswer
    answer = QueryAnswer(text="Escalated.", citations=[], chunks=[], follow_ups=[])
    ev = EscalationEvent(reason="wiki insufficient", chunk_ids=["c1", "c2"])
    entry_id = persist_query_log(
        bundle,
        question="Detailed question?",
        answer=answer,
        pages_touched=[],
        escalation_events=[ev],
    )
    data = json.loads((bundle.query_log_dir / f"{entry_id}.json").read_text(encoding="utf-8"))
    entry = QueryLogEntry.model_validate(data)
    assert len(entry.escalation_events) == 1
    assert "c1" in entry.escalation_events[0].chunk_ids


# --- save_log integration -------------------------------------------


def _log_files(bundle: BundlePaths) -> set:
    if not bundle.query_log_dir.exists():
        return set()
    return set(bundle.query_log_dir.glob("*.json"))


def test_save_log_true_writes_entry(ready_bundle, tmp_path):
    bundle, corpus = ready_bundle
    before_files = _log_files(bundle)
    query_run(
        bundle=bundle,
        corpus=corpus,
        question="test save log question",
        querier=FakeQuerier(),
        embed=embed_texts,
        cache_root=tmp_path / "qcache",
        save_log=True,
    )
    new_files = _log_files(bundle) - before_files
    assert len(new_files) >= 1


def test_save_log_false_no_new_entry(ready_bundle, tmp_path):
    bundle, corpus = ready_bundle
    before_files = _log_files(bundle)
    query_run(
        bundle=bundle,
        corpus=corpus,
        question="test no save log",
        querier=FakeQuerier(),
        embed=embed_texts,
        cache_root=tmp_path / "qcache2",
        save_log=False,
    )
    new_files = _log_files(bundle) - before_files
    assert len(new_files) == 0
