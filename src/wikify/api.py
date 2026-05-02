"""Bundle + Corpus context types — the only module that knows where things live on disk.

Two frozen dataclasses:

- :class:`Corpus` — input corpus path conventions (read-only during a wiki run).
- :class:`Bundle` — wiki-bundle layout (``run/``, ``work/``, ``wiki/``,
  ``derived/``).

``Bundle.open(path)`` requires ``run/state.json`` and raises
``FileNotFoundError`` otherwise. ``run init`` constructs the dataclass
directly with ``Bundle(root=...)`` while it is materialising the layout
(``state.json`` does not exist yet at that point); every other caller
goes through ``Bundle.open``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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

    @property
    def sqlite_path(self) -> Path:
        """SQLite query store path: <corpus_root>/wikify.db."""
        return self.root / "wikify.db"

    def ensure(self) -> None:
        for p in (self.markdown_dir, self.images_dir, self.chunks_dir, self.docs_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Bundle:
    """Wiki-bundle context. Strict — see :func:`open` for the marker check."""

    root: Path

    @classmethod
    def open(cls, path: Path | str) -> Bundle:
        """Open a bundle directory. Raises ``FileNotFoundError`` if the
        directory is missing or has no ``run/state.json``.

        ``run init`` is the only caller permitted to skip this check; it
        constructs ``Bundle(root=...)`` directly while it is creating
        ``state.json`` for the first time.
        """
        root = Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"bundle directory not found: {root}")
        if not (root / "run" / "state.json").is_file():
            raise FileNotFoundError(
                f"bundle at {root} has no run/state.json; not a wiki bundle"
            )
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
        """Create every directory that a fresh run needs."""
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
