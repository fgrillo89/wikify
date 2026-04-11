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

        The persona is generated once via ``wikify-simple persona-generate``
        and read by ``distill.pipeline.run`` if present. If the file does
        not exist, the writer falls back to a generic domain-expert persona
        baked into ``prompts.registry.compose_writer_prompt``.
        """
        return self.root / "persona.txt"

    @property
    def sampler_index_path(self) -> Path:
        return self.root / "sampler_index.json"

    @property
    def pagerank_path(self) -> Path:
        return self.root / "pagerank.json"

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
        return self.root / "_graph.json"

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

    def ensure(self) -> None:
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
