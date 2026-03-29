"""ScholarForge configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "SCHOLARFORGE_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # Library: scopes all data to a named library (for multi-domain research)
    library: str = "default"

    # Base data directory (libraries are subdirectories of this)
    data_root: Path = Path("data")

    # LLM
    llm_model: str = "claude-sonnet-4-20250514"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Chunking
    chunk_target_tokens: int = 600
    chunk_max_tokens: int = 800
    chunk_overlap_tokens: int = 50

    # Output
    default_journal: str = ""  # Journal name for formatting (empty = generic)
    output_style: str = "numbered"  # "numbered" or "author_year"

    # Zotero (optional)
    zotero_library_id: str = ""
    zotero_api_key: str = ""
    zotero_library_type: str = "user"

    @property
    def data_dir(self) -> Path:
        """Library-scoped data directory."""
        if self.library == "default":
            return self.data_root
        return self.data_root / "libraries" / self.library

    @property
    def figures_dir(self) -> Path:
        return self.data_dir / "figures"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "papers.db"

    @property
    def chromadb_dir(self) -> Path:
        return self.data_dir / "chromadb"

    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        for d in [self.data_dir, self.figures_dir, self.cache_dir, self.chromadb_dir]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
