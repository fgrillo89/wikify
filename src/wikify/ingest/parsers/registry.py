"""Dispatch a source file to the right parser based on suffix.

Parser configuration lives in one table.  Adding a new backend (e.g.
docling) requires one parser module, one row in ``_BACKEND_PARSERS``,
and selecting it via ``--parser docling`` on the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from wikify.models import DocImage, DocKind

# ---------------------------------------------------------------------------
# Data types produced by parsers
# ---------------------------------------------------------------------------


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
# Parser backend enum
# ---------------------------------------------------------------------------


class ParserBackend(str, Enum):
    """Known parser backends.

    ``DEFAULT`` is always available.  Others may require optional
    dependencies -- call ``validate_backend()`` before starting ingest.
    """

    DEFAULT = "default"
    DOCLING = "docling"

    @classmethod
    def from_str(cls, value: str) -> ParserBackend:
        """Resolve a CLI string to a backend, including custom backends."""
        try:
            return cls(value)
        except ValueError:
            if value in _CUSTOM_BACKENDS:
                # Custom backends registered at import time are valid but
                # don't have an enum member.  Return as-is via the str base.
                return value  # type: ignore[return-value]
            raise ValueError(
                f"unknown parser backend {value!r}; "
                f"available: {sorted(b.value for b in cls)}"
                + (f" + custom: {sorted(_CUSTOM_BACKENDS)}" if _CUSTOM_BACKENDS else "")
            ) from None


# ---------------------------------------------------------------------------
# Unified parser table
#
# One entry per (suffix, backend) pair.  Each maps to:
#   - a DocKind for the Document record
#   - a lazy loader that returns the parser module
#
# Lazy imports keep heavy dependencies (pymupdf, python-docx, ...) out of
# the module top level.
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


# suffix -> (DocKind, lazy_loader)
_PARSER_TABLE: dict[str, tuple[DocKind, callable]] = {
    "md":       ("md",   _lazy_md),
    "markdown": ("md",   _lazy_md),
    "txt":      ("md",   _lazy_md),
    "pdf":      ("pdf",  _lazy_pdf),
    "docx":     ("docx", _lazy_docx),
    "pptx":     ("pptx", _lazy_pptx),
    "html":     ("html", _lazy_html),
    "htm":      ("html", _lazy_html),
}

# Per-backend override tables.  Each maps suffix -> (DocKind, lazy_loader).
# The DEFAULT backend uses _PARSER_TABLE directly; named backends override
# specific suffixes.
_BACKEND_PARSERS: dict[str, dict[str, tuple[DocKind, callable]]] = {
    # Docling: PDF-only override.  Lazy import fails fast if not installed.
    ParserBackend.DOCLING.value: {
        "pdf": ("pdf", lambda: _require_docling()),
    },
}

# Custom backends registered by plugins at import time.
_CUSTOM_BACKENDS: dict[str, dict[str, tuple[DocKind, callable]]] = {}


def _require_docling():
    """Lazy-load the docling parser module, or raise with install instructions."""
    try:
        from . import docling as p
        return p
    except ImportError as exc:
        raise ImportError(
            "docling parser backend requires the 'docling' package. "
            "Install it with: uv add docling"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_parser_backend(
    name: str, overrides: dict[str, tuple[DocKind, callable]],
) -> None:
    """Register a custom parser backend that overrides specific formats.

    **Must be called at import time** (module top-level or package
    ``__init__``), not dynamically at runtime.  On Windows,
    ``ProcessPoolExecutor`` workers start fresh processes that only
    see registrations made during module import.

    Example::

        register_parser_backend("my_parser", {"pdf": ("pdf", _lazy_my_pdf)})
    """
    _CUSTOM_BACKENDS[name] = overrides


def available_backends() -> list[str]:
    """Return all known backend names (enum + custom)."""
    return sorted(
        [b.value for b in ParserBackend]
        + list(_CUSTOM_BACKENDS)
    )


def validate_backend(backend: str | ParserBackend) -> None:
    """Raise ``ValueError`` if *backend* is unknown.

    Call this before ingest starts so the user gets a clear error
    before any files are parsed.
    """
    key = backend.value if isinstance(backend, ParserBackend) else backend
    if key == ParserBackend.DEFAULT.value:
        return
    if key not in _BACKEND_PARSERS and key not in _CUSTOM_BACKENDS:
        raise ValueError(
            f"unknown parser backend {key!r}; "
            f"available: {available_backends()}"
        )


def parse_file(
    path: Path,
    *,
    parser_backend: str | ParserBackend = ParserBackend.DEFAULT,
) -> tuple[DocKind, ParseResult]:
    """Dispatch a source file to the right parser.

    ``parser_backend`` selects an override table.  Unknown backends
    raise ``ValueError``.  ``"default"`` always works.
    """
    key = parser_backend.value if isinstance(parser_backend, ParserBackend) else parser_backend
    validate_backend(key)

    suffix = path.suffix.lower().lstrip(".")
    overrides = _BACKEND_PARSERS.get(key) or _CUSTOM_BACKENDS.get(key) or {}
    entry = overrides.get(suffix) or _PARSER_TABLE.get(suffix)
    if entry is None:
        raise ValueError(f"unsupported file type: {path.suffix}")
    kind, loader = entry
    return kind, loader().parse(path)
