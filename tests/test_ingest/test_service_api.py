from __future__ import annotations

from pathlib import Path

from scholarforge.ingest.service import ingest_path


def test_ingest_path_file_uses_public_ingest_file(monkeypatch, tmp_path):
    seen: dict[str, Path] = {}
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf")

    def fake_ingest_file(path: Path, background_refresh: bool = True) -> int:
        seen["path"] = path
        seen["background_refresh"] = background_refresh
        return 1

    monkeypatch.setattr("scholarforge.ingest.service.ingest_file", fake_ingest_file)

    result = ingest_path(file_path)

    assert result == 1
    assert seen["path"] == file_path
    assert seen["background_refresh"] is True


def test_ingest_path_directory_runs_single_refresh_after_sequential_ingest(monkeypatch, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / "b.docx").write_bytes(b"b")
    calls: list[tuple[str, bool]] = []

    def fake_ingest_file(path: Path, background_refresh: bool = True) -> int:
        calls.append((path.name, background_refresh))
        return 1

    seen: dict[str, int] = {"refresh_calls": 0}

    def fake_refresh(new_paper_ids=None):
        seen["refresh_calls"] += 1

    monkeypatch.setattr("scholarforge.ingest.service.ingest_file", fake_ingest_file)
    monkeypatch.setattr("scholarforge.ingest.service.refresh_corpus", fake_refresh)

    result = ingest_path(tmp_path, parallel=False)

    assert result == 2
    assert sorted(calls) == [("a.pdf", False), ("b.docx", False)]
    assert seen["refresh_calls"] == 1
