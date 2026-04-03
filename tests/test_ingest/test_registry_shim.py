from __future__ import annotations

from pathlib import Path

from wikify.ingest import corpus_refresh, registry, service


def test_registry_reexports_public_ingest_api():
    assert registry._ingest_file is service.ingest_file
    assert registry._load_corpus_vocabulary is corpus_refresh.load_corpus_vocabulary


def test_registry_public_wrappers_delegate_to_service_modules(monkeypatch, tmp_path):
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf")
    seen: dict[str, object] = {}

    def fake_ingest_path(path: Path, parallel: bool = False, max_workers: int = 0) -> int:
        seen["path"] = path
        seen["parallel"] = parallel
        seen["max_workers"] = max_workers
        return 1

    def fake_refresh(new_paper_ids=None) -> None:
        seen["refresh"] = new_paper_ids

    monkeypatch.setattr("wikify.ingest.service.ingest_path", fake_ingest_path)
    monkeypatch.setattr("wikify.ingest.corpus_refresh.refresh_corpus", fake_refresh)

    assert registry.ingest_path(file_path, parallel=True, max_workers=4) == 1
    registry.run_batch_steps({"paper-1"})

    assert seen["path"] == file_path
    assert seen["parallel"] is True
    assert seen["max_workers"] == 4
    assert seen["refresh"] == {"paper-1"}


def test_registry_ingest_file_wrapper_calls_public_service(monkeypatch, tmp_path):
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf")
    seen: dict[str, Path] = {}

    def fake_ingest_file(path: Path, background_refresh: bool = True) -> int:
        seen["path"] = path
        seen["background_refresh"] = background_refresh
        return 1

    monkeypatch.setattr("wikify.ingest.registry._ingest_file", fake_ingest_file)

    result = registry.ingest_file(file_path, background_refresh=False)

    assert result == 1
    assert seen["path"] == file_path
    assert seen["background_refresh"] is False
