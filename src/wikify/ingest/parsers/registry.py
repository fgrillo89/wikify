"""Dispatch a source file to the right parser based on suffix."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from wikify.models import DocImage, DocKind


@dataclass
class RawImage:
    """Typed raw image record produced by parsers.

    Replaces the untyped ``metadata['_raw_images']`` dicts. Parsers fill
    either ``data`` (binary blob) or ``url`` (remote reference).
    """

    data: bytes | None = None
    url: str | None = None
    ext: str = "png"
    caption: str = ""
    alt_text: str = ""
    label: str | None = None
    page: int | None = None
    # PDF-specific fields (optional, carried through to sidecar)
    media_type: str | None = None
    bbox: tuple[float, ...] | None = None
    width: int | None = None
    height: int | None = None
    content_hash: str | None = None


@dataclass
class ParseResult:
    markdown: str
    sections: list[tuple[list[str], int, int]]  # (heading path, char start, char end)
    raw_images: list[RawImage] = field(default_factory=list)
    images: list[DocImage] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    title: str = ""


class DocumentParser(Protocol):
    """Protocol satisfied by every format-specific parser module."""

    def parse(self, path: Path) -> ParseResult: ...

    def supported_extensions(self) -> set[str]: ...


# ---------------------------------------------------------------------------
# Parser table -- one entry per supported format.  Lazy imports keep
# heavy dependencies (pymupdf, python-docx, ...) out of the module top.
# ---------------------------------------------------------------------------


def _lazy_md():
    from . import markdown as p

    return p


def _lazy_pdf():
    from . import pdf as p

    return p


def _lazy_docx():
    from . import docx as p

    return p


def _lazy_pptx():
    from . import pptx as p

    return p


def _lazy_html():
    from . import html as p

    return p


ParserBackend = str  # "default" | "docling" | custom key

_SUFFIX_TABLE: dict[str, callable] = {
    "md": _lazy_md,
    "markdown": _lazy_md,
    "txt": _lazy_md,
    "pdf": _lazy_pdf,
    "docx": _lazy_docx,
    "pptx": _lazy_pptx,
    "html": _lazy_html,
    "htm": _lazy_html,
}

_KIND_TABLE: dict[str, DocKind] = {
    "md": "md",
    "markdown": "md",
    "txt": "md",
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "html": "html",
    "htm": "html",
}

# Per-backend override tables.  Each maps suffix -> lazy loader.
# "default" is the built-in table above.
_BACKEND_OVERRIDES: dict[str, dict[str, callable]] = {}


def register_parser_backend(
    name: str, overrides: dict[str, callable],
) -> None:
    """Register a named parser backend that overrides specific formats.

    Example::

        register_parser_backend("docling", {"pdf": _lazy_docling_pdf})

    Then ``parse_file(path, parser_backend="docling")`` uses the
    docling parser for PDFs while falling back to the default table
    for other formats.
    """
    _BACKEND_OVERRIDES[name] = overrides


def parse_file(
    path: Path,
    *,
    parser_backend: ParserBackend = "default",
) -> tuple[DocKind, ParseResult]:
    """Dispatch a source file to the right parser.

    ``parser_backend`` selects an override table registered via
    ``register_parser_backend``.  Unknown backends raise ``ValueError``.
    ``"default"`` always works.
    """
    if parser_backend != "default" and parser_backend not in _BACKEND_OVERRIDES:
        raise ValueError(
            f"unknown parser backend {parser_backend!r}; "
            f"registered: {sorted(_BACKEND_OVERRIDES) or ['(none)']}"
        )
    suffix = path.suffix.lower().lstrip(".")
    overrides = _BACKEND_OVERRIDES.get(parser_backend, {})
    loader = overrides.get(suffix) or _SUFFIX_TABLE.get(suffix)
    if loader is None:
        raise ValueError(f"unsupported file type: {path.suffix}")
    kind = _KIND_TABLE.get(suffix)
    if kind is None:
        raise ValueError(f"unsupported file type: {path.suffix}")
    return kind, loader().parse(path)
