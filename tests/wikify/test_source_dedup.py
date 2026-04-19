"""Tests for same-stem source-file dedup in ``iter_sources``.

When a directory contains multiple formats of the same paper (the
Chua 1971 regression: a ``.pdf`` and a ``.docx`` with identical
stems), only the preferred-format file survives enumeration.
"""

from __future__ import annotations

from pathlib import Path

from wikify.ingest.pipeline import iter_sources


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_pdf_preferred_over_docx(tmp_path: Path) -> None:
    _touch(tmp_path / "[1971 Chua] Memristor.pdf")
    _touch(tmp_path / "[1971 Chua] Memristor.docx")
    out = sorted(p.name for p in iter_sources(tmp_path, dedup_same_stem=True))
    assert out == ["[1971 Chua] Memristor.pdf"]


def test_docx_preferred_over_pptx(tmp_path: Path) -> None:
    _touch(tmp_path / "talk.pptx")
    _touch(tmp_path / "talk.docx")
    out = sorted(p.name for p in iter_sources(tmp_path, dedup_same_stem=True))
    assert out == ["talk.docx"]


def test_unique_stems_all_kept(tmp_path: Path) -> None:
    _touch(tmp_path / "a.pdf")
    _touch(tmp_path / "b.docx")
    _touch(tmp_path / "c.html")
    out = sorted(p.name for p in iter_sources(tmp_path, dedup_same_stem=True))
    assert out == ["a.pdf", "b.docx", "c.html"]


def test_same_stem_in_different_subdirs_not_deduped(tmp_path: Path) -> None:
    # Same stem in different directories is a different paper — never
    # collapse across directory boundaries.
    _touch(tmp_path / "2023" / "review.pdf")
    _touch(tmp_path / "2024" / "review.pdf")
    out = sorted(str(p.relative_to(tmp_path)) for p in iter_sources(tmp_path, dedup_same_stem=True))
    assert out == [str(Path("2023") / "review.pdf"),
                   str(Path("2024") / "review.pdf")]


def test_three_format_collision_keeps_pdf(tmp_path: Path) -> None:
    _touch(tmp_path / "paper.pdf")
    _touch(tmp_path / "paper.docx")
    _touch(tmp_path / "paper.pptx")
    out = sorted(p.name for p in iter_sources(tmp_path, dedup_same_stem=True))
    assert out == ["paper.pdf"]


def test_unsupported_extensions_ignored(tmp_path: Path) -> None:
    _touch(tmp_path / "paper.pdf")
    _touch(tmp_path / "paper.jpg")  # unsupported
    _touch(tmp_path / "paper.xlsx")  # unsupported
    out = sorted(p.name for p in iter_sources(tmp_path, dedup_same_stem=True))
    assert out == ["paper.pdf"]
