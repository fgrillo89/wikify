"""Public ingestion API."""

from scholarforge.ingest.corpus_refresh import refresh_corpus
from scholarforge.ingest.html import ingest_html
from scholarforge.ingest.markdown import ingest_markdown
from scholarforge.ingest.service import SUPPORTED_EXTENSIONS, ingest_file, ingest_path

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ingest_file",
    "ingest_html",
    "ingest_markdown",
    "ingest_path",
    "refresh_corpus",
]
