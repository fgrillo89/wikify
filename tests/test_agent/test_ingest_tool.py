from __future__ import annotations

import json
from types import SimpleNamespace

from wikify.papers.agent.tools import ingest_paper
from wikify.store.models import Paper


def test_ingest_paper_returns_structured_missing_file_error(tmp_path):
    missing = tmp_path / "missing.pdf"

    data = json.loads(ingest_paper(str(missing)))

    assert data["ok"] is False
    assert data["status"] == "missing_file"
    assert str(missing) in data["error"]


def test_ingest_paper_returns_structured_unsupported_format(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello", encoding="utf-8")

    data = json.loads(ingest_paper(str(file_path)))

    assert data["ok"] is False
    assert data["status"] == "unsupported_format"
    assert ".txt" in data["error"]


def test_ingest_paper_returns_structured_already_ingested(monkeypatch, tmp_path):
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf-bytes")
    paper = Paper(
        id="hash-id",
        title="Test Paper",
        authors='["Alice Kim"]',
        year=2024,
        doi="10.1000/test",
        source_path=str(file_path),
    )

    monkeypatch.setattr("wikify.ingest.service.ingest_file", lambda *args, **kwargs: 0)

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, key):
            return paper

    monkeypatch.setattr("wikify.store.db.get_session", lambda: _Session())

    data = json.loads(ingest_paper(str(file_path)))

    assert data["ok"] is True
    assert data["status"] == "already_ingested"
    assert data["paper"]["title"] == "Test Paper"
    assert data["background_refresh"] is False


def test_ingest_paper_returns_structured_success(monkeypatch, tmp_path):
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf-bytes")
    paper = Paper(
        id="hash-id",
        title="Test Paper",
        authors='["Alice Kim"]',
        year=2024,
        doi="10.1000/test",
        source_path=str(file_path),
    )

    monkeypatch.setattr("wikify.ingest.service.ingest_file", lambda *args, **kwargs: 1)

    class _ExecResult:
        def all(self):
            return [SimpleNamespace(), SimpleNamespace()]

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, key):
            return paper

        def exec(self, query):
            return _ExecResult()

    monkeypatch.setattr("wikify.store.db.get_session", lambda: _Session())

    data = json.loads(ingest_paper(str(file_path)))

    assert data["ok"] is True
    assert data["status"] == "ingested"
    assert data["chunk_count"] == 2
    assert data["background_refresh"] is True
