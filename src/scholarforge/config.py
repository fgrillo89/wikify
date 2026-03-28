"""ScholarForge configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "SCHOLARFORGE_"}

    # Paths
    data_dir: Path = Path("data")
    figures_dir: Path = Path("data/figures")
    cache_dir: Path = Path("data/cache")
    db_path: Path = Path("data/papers.db")
    chromadb_dir: Path = Path("data/chromadb")
    graph_path: Path = Path("data/graph.graphml")

    # LLM
    llm_model: str = "claude-sonnet-4-20250514"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Chunking
    chunk_target_tokens: int = 600
    chunk_max_tokens: int = 800
    chunk_overlap_tokens: int = 50

    # Zotero (optional)
    zotero_library_id: str = ""
    zotero_api_key: str = ""
    zotero_library_type: str = "user"

    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        for d in [self.data_dir, self.figures_dir, self.cache_dir, self.chromadb_dir]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
