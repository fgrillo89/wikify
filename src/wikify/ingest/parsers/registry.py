"""Dispatch a source file to the right parser based on suffix.

Parser selection uses an enum + factory pattern.  The built-in table
covers all currently implemented formats (md, pdf, docx, pptx, html).
To add a new backend (e.g. docling):

1. Create a parser module in this package (``docling.py``) that exposes
   ``parse(path) -> ParseResult`` and ``supported_extensions() -> set[str]``.
2. Add a member to ``ParserBackend`` with a ``_overrides()`` method that
   returns ``{suffix: (DocKind, lazy_loader)}``.
3. Select it via ``--parser <name>`` on the CLI.

``validate_backend()`` is called before ingest starts and raises if a
backend's parser module cannot be imported -- no silent partial corpus.
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
# Parser backend enum + factory
#
# Only implemented backends appear here.  Adding a future backend
# (e.g. docling) means adding an enum member with an _overrides()
# method that imports the parser module.  If the module is missing,
# validate_backend() will raise before ingest starts.
# ---------------------------------------------------------------------------


class ParserBackend(str, Enum):
    """Implemented parser backends.

    Each member's ``_overrides()`` returns a suffix override table.
    DEFAULT uses the built-in ``_PARSER_TABLE`` for all formats.
    """

    DEFAULT = "default"

    def _overrides(self) -> dict[str, tuple[DocKind, callable]]:
        """Return suffix override table for this backend.

        DEFAULT returns empty (uses the built-in table).  Future
        backends import their parser module here; ImportError
        propagates immediately via ``validate_backend()``.
        """
        if self is ParserBackend.DEFAULT:
            return {}
        # Future backends: add branches here.
        #   if self is ParserBackend.DOCLING:
        #       from . import docling as p
        #       return {"pdf": ("pdf", lambda: p)}
        raise NotImplementedError(f"no override table for {self.value!r}")


# ---------------------------------------------------------------------------
# Default parser table
#
# One row per supported suffix.  Lazy imports keep heavy dependencies
# (pymupdf, python-docx, ...) out of the module top level.
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

# Custom backends registered by plugins at import time.
_CUSTOM_BACKENDS: dict[str, dict[str, tuple[DocKind, callable]]] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Backends that use GPU models and must not be parallelized across
# worker processes (each worker would load its own GPU model copy).
_GPU_BACKENDS: set[str] = set()


def register_parser_backend(
    name: str,
    overrides: dict[str, tuple[DocKind, callable]],
    *,
    gpu: bool = False,
) -> None:
    """Register a custom parser backend that overrides specific formats.

    **Must be called at import time** (module top-level or package
    ``__init__``), not dynamically at runtime.  On Windows,
    ``ProcessPoolExecutor`` workers start fresh processes that only
    see registrations made during module import.

    Set ``gpu=True`` for backends that load GPU models (forces
    single-worker mode in the ingest pipeline).
    """
    _CUSTOM_BACKENDS[name] = overrides
    if gpu:
        _GPU_BACKENDS.add(name)


def backend_requires_single_worker(name: str) -> bool:
    """True if this backend uses GPU models and needs single-threaded ingest."""
    return name in _GPU_BACKENDS


def available_backends() -> list[str]:
    """Return all implemented backend names (enum + custom)."""
    return sorted(
        [b.value for b in ParserBackend]
        + list(_CUSTOM_BACKENDS)
    )


def supported_extensions(
    backend: str | ParserBackend | None = None,
) -> set[str]:
    """Return every source-file extension (with leading ``.``) the
    pipeline will parse, for a given backend.

    The set is the union of the built-in format table with the
    backend's overrides — overrides can only widen the set (by
    binding existing suffixes to a different parser); they never
    remove formats.

    When ``backend`` is ``None`` the built-in table alone is
    returned. Used by ``iter_sources`` to filter the input tree and
    by the CLI to surface the accepted formats to the user.
    """
    exts = {f".{s}" for s in _PARSER_TABLE}
    if backend is None:
        return exts
    key = backend.value if isinstance(backend, ParserBackend) else backend
    if key == ParserBackend.DEFAULT.value:
        return exts
    try:
        overrides = _resolve_backend(key)
    except ValueError:
        return exts
    exts.update(f".{s}" for s in overrides)
    return exts


def _resolve_backend(key: str) -> dict[str, tuple[DocKind, callable]]:
    """Resolve a backend key to its suffix override table.

    Raises ``ValueError`` if the backend is unknown.  For enum
    backends, eagerly imports the parser module so missing deps
    surface immediately.  Custom backends (registered via
    ``register_parser_backend``) are trusted at registration time;
    their loaders are invoked lazily during ``parse_file``.
    """
    # Custom backends are trusted at registration time.
    if key in _CUSTOM_BACKENDS:
        return _CUSTOM_BACKENDS[key]

    # Resolve via enum.  This calls _overrides() which imports the
    # parser module -- ImportError becomes a clear ValueError.
    try:
        backend = ParserBackend(key)
    except ValueError:
        raise ValueError(
            f"unknown parser backend {key!r}; "
            f"available: {available_backends()}"
        ) from None

    try:
        return backend._overrides()
    except (ImportError, NotImplementedError) as exc:
        raise ValueError(
            f"parser backend {key!r} is not installed: {exc}"
        ) from exc


def validate_backend(backend: str | ParserBackend) -> None:
    """Raise ``ValueError`` if *backend* is unknown or not installed.

    Call this before ingest starts so the user gets a clear error
    before any files are parsed.  For non-default backends, eagerly
    resolves the override table (importing parser modules) to surface
    missing dependencies immediately.
    """
    key = backend.value if isinstance(backend, ParserBackend) else backend
    if key == ParserBackend.DEFAULT.value:
        return
    _resolve_backend(key)  # raises on missing module or unknown key


def parse_file(
    path: Path,
    *,
    parser_backend: str | ParserBackend = ParserBackend.DEFAULT,
    skip_metadata: bool = False,
) -> tuple[DocKind, ParseResult]:
    """Dispatch a source file to the right parser.

    ``parser_backend`` selects an override table.  Unknown or
    uninstalled backends raise ``ValueError``.

    ``skip_metadata`` is forwarded to PDF parsers (``pdf``,
    ``marker_pdf``, ``docling_pdf``) that support it; non-PDF parsers
    ignore it silently.  The ingest DAG sets this to ``True`` during
    pass 3 (content parse) so metadata fusion can run in pass 4 with
    DOI-resolved context from pass 2.
    """
    key = parser_backend.value if isinstance(parser_backend, ParserBackend) else parser_backend
    if key == ParserBackend.DEFAULT.value:
        overrides: dict[str, tuple[DocKind, callable]] = {}
    else:
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
