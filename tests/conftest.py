"""Shared test fixtures for ScholarForge."""

import pytest

from scholarforge.config import Settings


@pytest.fixture
def tmp_settings(tmp_path):
    """Settings pointing to a temporary directory."""
    return Settings(
        data_dir=tmp_path / "data",
        figures_dir=tmp_path / "data" / "figures",
        cache_dir=tmp_path / "data" / "cache",
        db_path=tmp_path / "data" / "test.db",
        graph_path=tmp_path / "data" / "graph.graphml",
    )
