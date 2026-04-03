"""Public ingestion API."""

from wikify.ingest.corpus_refresh import refresh_corpus
from wikify.ingest.html import ingest_html
from wikify.ingest.markdown import ingest_markdown
from wikify.ingest.service import SUPPORTED_EXTENSIONS, ingest_file, ingest_path

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ingest_file",
    "ingest_html",
    "ingest_markdown",
    "ingest_path",
    "refresh_corpus",
]
