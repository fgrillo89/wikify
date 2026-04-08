"""The only module that knows where things live on disk."""

from __future__ import annotations

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

    def ensure(self) -> None:
        for p in (self.markdown_dir, self.images_dir, self.chunks_dir, self.docs_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class BundlePaths:
    root: Path

    @property
    def concepts_dir(self) -> Path:
        return self.root / "concepts"

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
    def calls_path(self) -> Path:
        return self.root / "_calls.jsonl"

    def ensure(self) -> None:
        self.concepts_dir.mkdir(parents=True, exist_ok=True)
        self.people_dir.mkdir(parents=True, exist_ok=True)
