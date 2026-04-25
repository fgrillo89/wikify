"""Bundle + Corpus context types — the only module that knows where things live on disk.

Three frozen dataclasses replace the legacy ``BundlePaths`` / ``CorpusPaths``:

- :class:`Corpus` — input corpus path conventions (read-only during a wiki run).
- :class:`Bundle` — v2 wiki-bundle layout (``run/``, ``work/``, ``wiki/``,
  ``derived/``). Used by every workstream from W2 onwards.
- :class:`LegacyBundle` — v1 wiki-bundle layout (``_session/``, ``_scratch/``,
  ``_calls.jsonl``, ``articles/``, ...). Used by ``cli/legacy/*`` and by
  ``cli/migrate.py``; deleted in Phase D once legacy bundles are migrated.

Layout enforcement is strict. ``Bundle.open(path)`` requires a v2 marker
(``run/state.json``) and raises :class:`LayoutMismatchError` otherwise.
``LegacyBundle.open(path)`` requires a v1 marker (``_session/`` or any v1
artifact) and raises if the directory looks like v2. Constructing the
dataclass directly with ``Bundle(root=...)`` skips the check (callers that
have already validated the layout, e.g. ``run init``, may bypass).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# v2 markers: presence of any of these means the bundle is v2 layout.
_V2_MARKERS: tuple[str, ...] = (
    "run/state.json",
    "run",  # the directory itself is enough once W2 lands; state.json is the strong marker
)

# v1 markers: presence of any of these means the bundle is v1 layout.
_V1_MARKERS: tuple[str, ...] = (
    "_session/session.json",
    "_session",
    "_scratch",
    "_calls.jsonl",
    "_run.json",
)


class LayoutMismatchError(ValueError):
    """Raised when a bundle directory does not match the expected layout version."""

    def __init__(self, path: Path, expected: str, found: str) -> None:
        super().__init__(
            f"bundle at {path} has layout '{found}', expected '{expected}'"
        )
        self.path = path
        self.expected = expected
        self.found = found


def _detect_layout(root: Path) -> str:
    """Return ``"v2"``, ``"v1"``, or ``"unknown"`` based on on-disk markers.

    A bundle with both v1 and v2 markers is reported as ``"v2"`` — once
    the new layout is in place, the v1 artifacts are read-only legacy
    state that ``migrate inspect`` will surface.
    """
    if (root / "run" / "state.json").is_file():
        return "v2"
    for marker in _V1_MARKERS:
        if (root / marker).exists():
            return "v1"
    if (root / "run").is_dir():
        # New bundle being initialised — treat as v2 even before state.json lands.
        return "v2"
    return "unknown"


@dataclass(frozen=True)
class Corpus:
    """Path conventions for a corpus directory (input to wikification)."""

    root: Path

    @classmethod
    def open(cls, path: Path | str) -> Corpus:
        """Open a corpus directory. The directory must exist."""
        root = Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"corpus directory not found: {root}")
        return cls(root=root)

    @property
    def markdown_dir(self) -> Path:
        return self.root / "markdown"

    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    @property
    def chunks_dir(self) -> Path:
        return self.root / "chunks"

    @property
    def docs_dir(self) -> Path:
        return self.root / "docs"

    @property
    def vectors_path(self) -> Path:
        return self.root / "vectors.npz"

    @property
    def topics_path(self) -> Path:
        return self.root / "topics.json"

    @property
    def images_index_path(self) -> Path:
        return self.root / "images.json"

    @property
    def equations_index_path(self) -> Path:
        return self.root / "equations.json"

    @property
    def persona_path(self) -> Path:
        """Optional cached corpus persona text. The writer falls back to the
        generic persona in ``prompts.registry.compose_writer_prompt`` when
        absent.
        """
        return self.root / "persona.txt"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def library_bib_path(self) -> Path:
        return self.root / "corpus_papers.bib"

    @property
    def references_bib_path(self) -> Path:
        return self.root / "cited_works.bib"

    @property
    def citation_index_path(self) -> Path:
        return self.root / "citations.json"

    @property
    def knowledge_graph_path(self) -> Path:
        return self.root / "knowledge_graph.json"

    def ensure(self) -> None:
        for p in (self.markdown_dir, self.images_dir, self.chunks_dir, self.docs_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Bundle:
    """v2 wiki-bundle context. Strict — see :func:`open` for the marker check."""

    root: Path

    @classmethod
    def open(cls, path: Path | str) -> Bundle:
        """Open a v2 bundle. Raises ``LayoutMismatchError`` on a v1 layout.

        The ``run/state.json`` file is the canonical v2 marker; the
        ``run/`` directory alone counts when a bundle is being initialised.
        """
        root = Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"bundle directory not found: {root}")
        layout = _detect_layout(root)
        if layout != "v2":
            raise LayoutMismatchError(root, expected="v2", found=layout)
        return cls(root=root)

    @property
    def run_dir(self) -> Path:
        return self.root / "run"

    @property
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.run_dir / "lock"

    @property
    def io_dir(self) -> Path:
        return self.run_dir / "io"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def work_index_path(self) -> Path:
        return self.work_dir / "index.md"

    @property
    def work_inbox_dir(self) -> Path:
        return self.work_dir / "inbox"

    @property
    def work_concepts_dir(self) -> Path:
        return self.work_dir / "concepts"

    def work_concept_dir(self, slug: str) -> Path:
        return self.work_concepts_dir / slug

    @property
    def wiki_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def wiki_index_path(self) -> Path:
        return self.wiki_dir / "index.md"

    @property
    def wiki_articles_dir(self) -> Path:
        return self.wiki_dir / "articles"

    @property
    def wiki_people_dir(self) -> Path:
        return self.wiki_dir / "people"

    @property
    def derived_dir(self) -> Path:
        return self.root / "derived"

    @property
    def derived_index_path(self) -> Path:
        return self.derived_dir / "index.json"

    @property
    def derived_graph_path(self) -> Path:
        return self.derived_dir / "graph.json"

    @property
    def derived_vectors_path(self) -> Path:
        return self.derived_dir / "vectors.npz"

    def ensure(self) -> None:
        """Create every v2 directory that a fresh run needs."""
        for p in (
            self.run_dir,
            self.io_dir,
            self.work_dir,
            self.work_inbox_dir,
            self.work_concepts_dir,
            self.wiki_dir,
            self.wiki_articles_dir,
            self.wiki_people_dir,
            self.derived_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class LegacyBundle:
    """v1 wiki-bundle context. Used by ``cli/legacy/*`` and by ``cli/migrate.py``.

    Field set is exactly the legacy ``LegacyBundle`` accessors. New code
    should use :class:`Bundle` instead; this class only exists to keep
    legacy CLI nouns and the migration helper working until Phase C/D.
    """

    root: Path

    @classmethod
    def open(cls, path: Path | str) -> LegacyBundle:
        """Open a v1 bundle. Raises ``LayoutMismatchError`` on a v2 layout."""
        root = Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"bundle directory not found: {root}")
        layout = _detect_layout(root)
        if layout == "v2":
            raise LayoutMismatchError(root, expected="v1", found="v2")
        return cls(root=root)

    @property
    def articles_dir(self) -> Path:
        return self.root / "articles"

    @property
    def people_dir(self) -> Path:
        return self.root / "people"

    @property
    def graph_path(self) -> Path:
        return self.root / "_wiki_graph.json"

    @property
    def wiki_vectors_path(self) -> Path:
        return self.root / "_wiki_vectors.npz"

    @property
    def run_path(self) -> Path:
        return self.root / "_run.json"

    @property
    def run_history_path(self) -> Path:
        return self.root / "_run_history.jsonl"

    @property
    def calls_path(self) -> Path:
        return self.root / "_calls.jsonl"

    @property
    def meta_dir(self) -> Path:
        return self.root / "_meta"

    @property
    def session_dir(self) -> Path:
        return self.root / "_session"

    @property
    def session_path(self) -> Path:
        return self.session_dir / "session.json"

    @property
    def session_checkpoints_dir(self) -> Path:
        return self.session_dir / "checkpoints"

    @property
    def session_lock_path(self) -> Path:
        return self.session_dir / "session.lock"

    @property
    def scratch_dir(self) -> Path:
        return self.root / "_scratch"

    def ensure(self) -> None:
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
