"""Dispatch a source file to the right parser based on suffix.

Parser selection is a single ``ParserBackend`` enum. Each member
returns its own override table; ``DEFAULT`` adds no overrides, so
every suffix goes to the built-in ``_PARSER_TABLE``. Other members
replace the parser for specific suffixes (e.g. ``MARKER`` replaces
``.pdf``).

To add a new backend:

1. Create the parser module (e.g. ``my_parser.py``) exposing
   ``parse(path) -> ParseResult`` and ``supported_extensions() -> set[str]``.
2. Add a ``ParserBackend`` member plus a branch in ``overrides()`` that
   returns ``{suffix: (DocKind, lazy_loader)}``.
3. Flag ``is_gpu`` if the backend holds GPU models; that pins ingest
   to a single worker.

``validate_backend()`` runs before ingest starts and raises if the
selected backend's parser module cannot be imported.
"""

from __future__ import annotations

from collections.abc import Callable
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
# Lazy loaders — cheap trampolines whose body does the heavy import.
# Keeping these as module-level functions lets the enum's ``overrides()``
# method reference them without importing the parser until the backend
# is actually used.
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


def _lazy_marker():
    from . import marker_pdf as p
    return p


def _lazy_docling():
    from . import docling as p
    return p


# ---------------------------------------------------------------------------
# Built-in format table. Every suffix the pipeline recognises is listed
# here with its default DocKind and lazy loader; backend overrides can
# bind these suffixes to a different parser but never remove them.
# ---------------------------------------------------------------------------


_PARSER_TABLE: dict[str, tuple[DocKind, Callable]] = {
    "md":       ("md",   _lazy_md),
    "markdown": ("md",   _lazy_md),
    "txt":      ("md",   _lazy_md),
    "pdf":      ("pdf",  _lazy_pdf),
    "docx":     ("docx", _lazy_docx),
    "pptx":     ("pptx", _lazy_pptx),
    "html":     ("html", _lazy_html),
    "htm":      ("html", _lazy_html),
}


# ---------------------------------------------------------------------------
# The single parser-backend registry.
# ---------------------------------------------------------------------------


class ParserBackend(str, Enum):
    """Every parser backend the pipeline knows about.

    Members carry their own override table via ``overrides()``. The
    ``is_gpu`` property marks backends that hold GPU models — ingest
    pins those to a single worker to avoid N copies of the model
    across a process pool.

    DEFAULT is the best-quality configuration: Marker for PDFs,
    Docling for DOCX / PPTX / HTML, built-in markdown reader for
    ``.md`` / ``.markdown`` / ``.txt``. LITE is the lightweight
    escape hatch (pymupdf4llm + python-docx + python-pptx +
    trafilatura) for CI, tests, and low-resource environments.
    MARKER and DOCLING are single-format overrides for users who
    want one parser everywhere.
    """

    DEFAULT = "default"
    LITE = "lite"
    MARKER = "marker"
    DOCLING = "docling"

    @property
    def is_gpu(self) -> bool:
        """GPU-bound backends must not be parallelised across worker processes."""
        return self in {
            ParserBackend.DEFAULT,
            ParserBackend.MARKER,
            ParserBackend.DOCLING,
        }

    def overrides(self) -> dict[str, tuple[DocKind, Callable]]:
        """Return ``{suffix: (DocKind, lazy_loader)}`` for this backend."""
        if self is ParserBackend.LITE:
            return {}
        if self is ParserBackend.DEFAULT:
            return {
                "pdf":  ("pdf",  _lazy_marker),
                "docx": ("docx", _lazy_docling),
                "pptx": ("pptx", _lazy_docling),
                "html": ("html", _lazy_docling),
                "htm":  ("html", _lazy_docling),
            }
        if self is ParserBackend.MARKER:
            return {"pdf": ("pdf", _lazy_marker)}
        if self is ParserBackend.DOCLING:
            return {
                "pdf":  ("pdf",  _lazy_docling),
                "docx": ("docx", _lazy_docling),
                "pptx": ("pptx", _lazy_docling),
                "html": ("html", _lazy_docling),
                "htm":  ("html", _lazy_docling),
            }
        # Unreachable; all members handled above. The NotImplementedError
        # surfaces as a clear ValueError via ``_resolve_backend`` if a new
        # member is added without a branch here.
        raise NotImplementedError(f"no overrides defined for {self}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def available_backends() -> list[str]:
    """Return all implemented backend names."""
    return sorted(b.value for b in ParserBackend)


def backend_requires_single_worker(key: str | ParserBackend) -> bool:
    """True if this backend uses GPU models and needs single-threaded ingest."""
    try:
        b = key if isinstance(key, ParserBackend) else ParserBackend(key)
    except ValueError:
        return False
    return b.is_gpu


def supported_extensions(
    backend: str | ParserBackend | None = None,
) -> set[str]:
    """Return every source-file extension (with leading ``.``) the pipeline
    will parse, for a given backend.

    Union of the built-in format table with the backend's overrides.
    Overrides can only widen the set (by binding existing suffixes to a
    different parser); they never remove formats.

    When ``backend`` is ``None`` the built-in table alone is returned.
    Used by ``iter_sources`` to filter the input tree and by the CLI to
    surface the accepted formats to the user.
    """
    exts = {f".{s}" for s in _PARSER_TABLE}
    if backend is None:
        return exts
    try:
        b = backend if isinstance(backend, ParserBackend) else ParserBackend(backend)
    except ValueError:
        return exts
    exts.update(f".{s}" for s in b.overrides())
    return exts


def _resolve_backend(key: str) -> dict[str, tuple[DocKind, Callable]]:
    """Resolve a backend key to its suffix override table.

    Raises ``ValueError`` for unknown or uninstalled backends. Calls
    ``overrides()`` eagerly so missing parser-module imports surface
    immediately via ``validate_backend``.
    """
    try:
        backend = ParserBackend(key)
    except ValueError:
        raise ValueError(
            f"unknown parser backend {key!r}; "
            f"available: {available_backends()}"
        ) from None
    try:
        return backend.overrides()
    except (ImportError, NotImplementedError) as exc:
        raise ValueError(
            f"parser backend {key!r} is not installed: {exc}"
        ) from exc


def validate_backend(backend: str | ParserBackend) -> None:
    """Raise ``ValueError`` if *backend* is unknown or not installed.

    Call this before ingest starts so the user gets a clear error
    before any files are parsed. Eagerly resolves the override table
    (importing parser modules) to surface missing dependencies
    immediately.
    """
    key = backend.value if isinstance(backend, ParserBackend) else backend
    _resolve_backend(key)  # raises on missing module or unknown key


def parse_file(
    path: Path,
    *,
    parser_backend: str | ParserBackend = ParserBackend.LITE,
    skip_metadata: bool = False,
) -> tuple[DocKind, ParseResult]:
    """Dispatch a source file to the right parser.

    ``parser_backend`` selects an override table. Unknown or
    uninstalled backends raise ``ValueError``. The default is ``LITE``
    so library callers (tests, scripts) get fast lightweight parsers
    and don't pay for GPU-model initialisation unless they explicitly
    ask for ``DEFAULT`` (or ``MARKER`` / ``DOCLING``). The CLI opts
    into ``DEFAULT`` for end-user ingest.

    ``skip_metadata`` is forwarded to PDF parsers that support it;
    non-PDF parsers ignore it silently. The ingest DAG sets this to
    ``True`` during pass 3 (content parse) so metadata fusion can run
    in pass 4 with DOI-resolved context from pass 2.
    """
    key = parser_backend.value if isinstance(parser_backend, ParserBackend) else parser_backend
    overrides = _resolve_backend(key)
    suffix = path.suffix.lower().lstrip(".")
    entry = overrides.get(suffix) or _PARSER_TABLE.get(suffix)
    if entry is None:
        raise ValueError(f"unsupported file type: {path.suffix}")
    kind, loader = entry
    parser = loader()
    # Only PDF parsers accept skip_metadata; forward when it's set.
    if skip_metadata and suffix == "pdf":
        return kind, parser.parse(path, skip_metadata=True)
    return kind, parser.parse(path)
