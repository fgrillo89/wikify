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

    Override tables are declared in the module-level ``_OVERRIDES`` data
    table below; ``overrides()`` is a thin lookup. This matches
    CLAUDE.md's "one explicit data table over scattered branches"
    architectural rule and makes adding a backend a single-dict-entry
    edit instead of a new ``if`` branch.

    DEFAULT is the best-quality configuration: Docling for PDFs +
    DOCX / PPTX / HTML (uniform structural extraction, in-tree
    Granite-Docling formula head), built-in markdown reader for
    ``.md`` / ``.markdown`` / ``.txt``. The DEFAULT was previously
    Marker for PDFs; the swap landed after Stage B1.5 of the parser
    probe showed Docling's median wall-clock is within ~13% of
    Marker on real-world papers (n=20) and Docling's structural
    FormulaItem extraction produces materially cleaner LaTeX. LITE
    is the lightweight escape hatch (pymupdf4llm + python-docx +
    python-pptx + trafilatura) for CI, tests, and low-resource
    environments. MARKER and DOCLING are single-parser overrides
    for users who want one parser everywhere.
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
        return _OVERRIDES[self]


# ---------------------------------------------------------------------------
# Backend override table. One row per ParserBackend member: the suffix
# overrides that backend binds on top of ``_PARSER_TABLE``. Adding or
# retargeting a backend is a single-row change here.
# ---------------------------------------------------------------------------


_OVERRIDES: dict[ParserBackend, dict[str, tuple[DocKind, Callable]]] = {
    ParserBackend.LITE: {},
    ParserBackend.DEFAULT: {
        "pdf":  ("pdf",  _lazy_docling),
        "docx": ("docx", _lazy_docling),
        "pptx": ("pptx", _lazy_docling),
        "html": ("html", _lazy_docling),
        "htm":  ("html", _lazy_docling),
    },
    ParserBackend.MARKER: {
        "pdf":  ("pdf",  _lazy_marker),
    },
    ParserBackend.DOCLING: {
        "pdf":  ("pdf",  _lazy_docling),
        "docx": ("docx", _lazy_docling),
        "pptx": ("pptx", _lazy_docling),
        "html": ("html", _lazy_docling),
        "htm":  ("html", _lazy_docling),
    },
}
# Defence against silent drift: every enum member must have a row.
assert set(_OVERRIDES) == set(ParserBackend), (
    f"_OVERRIDES missing entries for "
    f"{set(ParserBackend) - set(_OVERRIDES)}"
)


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
    backend: str | ParserBackend,
) -> set[str]:
    """Return every source-file extension (with leading ``.``) the pipeline
    will parse for ``backend``.

    Union of the built-in format table with the backend's overrides.
    Overrides can only widen the set (by binding existing suffixes to a
    different parser); they never remove formats. Raises ``ValueError``
    with the list of available backends on an unknown key — a typo at
    the CLI boundary must surface, not silently degrade to the built-in
    set.
    """
    key = backend.value if isinstance(backend, ParserBackend) else backend
    overrides = _resolve_backend(key)
    exts = {f".{s}" for s in _PARSER_TABLE}
    exts.update(f".{s}" for s in overrides)
    return exts


def _resolve_backend(key: str) -> dict[str, tuple[DocKind, Callable]]:
    """Resolve a backend key to its suffix override table.

    Raises ``ValueError`` for unknown backends.
    """
    try:
        backend = ParserBackend(key)
    except ValueError:
        raise ValueError(
            f"unknown parser backend {key!r}; "
            f"available: {available_backends()}"
        ) from None
    return backend.overrides()


def validate_backend(backend: str | ParserBackend) -> None:
    """Raise ``ValueError`` if *backend* is unknown or not installed.

    Call this before ingest starts so the user gets a clear error
    before any files are parsed. Invokes each lazy loader in the
    backend's override table to force the parser-module import, so
    a missing dependency (e.g. ``marker-pdf`` not installed) surfaces
    here rather than per-file during ingest.
    """
    key = backend.value if isinstance(backend, ParserBackend) else backend
    overrides = _resolve_backend(key)  # raises on unknown key
    for _suffix, (_kind, loader) in overrides.items():
        try:
            loader()
        except ImportError as exc:
            raise ValueError(
                f"parser backend {key!r} is not installed: {exc}"
            ) from exc


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
