"""The only module that knows where things live on disk."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CorpusPaths:
    root: Path

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
    def graph_path(self) -> Path:
        return self.root / "graph.json"

    @property
    def topics_path(self) -> Path:
        return self.root / "topics.json"

    @property
    def images_index_path(self) -> Path:
        return self.root / "images.json"

    @property
    def persona_path(self) -> Path:
        """Path to the optional cached corpus persona text.

        The persona is generated once via ``wikify persona-generate``
        and read by ``distill.pipeline.run`` if present. If the file does
        not exist, the writer falls back to a generic domain-expert persona
        baked into ``prompts.registry.compose_writer_prompt``.
        """
        return self.root / "persona.txt"

    @property
    def explorer_index_path(self) -> Path:
        return self.root / "explorer_index.json"

    @property
    def pagerank_path(self) -> Path:
        return self.root / "pagerank.json"

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
class BundlePaths:
    root: Path

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
    def write_requests_dir(self) -> Path:
        return self.root / "_write_requests"

    @property
    def meta_dir(self) -> Path:
        return self.root / "_meta"

    @property
    def coverage_memory_path(self) -> Path:
        return self.meta_dir / "coverage_memory.json"

    @property
    def prompt_layers_dir(self) -> Path:
        return self.meta_dir / "prompt_layers"

    @property
    def query_log_dir(self) -> Path:
        return self.meta_dir / "query_log"

    @property
    def verbalize_log_path(self) -> Path:
        """Append-only JSONL log of handler reasoning. Populated only when
        a run is invoked with ``verbalize=True``. Each line:
        ``{run_id, when, role, rid, page_id|chunk_id, reasoning}``."""
        return self.meta_dir / "verbalize.jsonl"

    def ensure(self) -> None:
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
