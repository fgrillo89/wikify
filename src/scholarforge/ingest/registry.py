"""Dispatcher: file extension -> parser."""

from pathlib import Path

from rich.console import Console

console = Console()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}


def ingest_path(path: Path) -> int:
    """Ingest a file or directory. Returns count of documents ingested."""
    if path.is_file():
        return _ingest_file(path)
    elif path.is_dir():
        count = 0
        for ext in SUPPORTED_EXTENSIONS:
            for file in sorted(path.rglob(f"*{ext}")):
                count += _ingest_file(file)
        return count
    return 0


def _ingest_file(path: Path) -> int:
    """Ingest a single file based on extension."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from scholarforge.ingest.pdf import ingest_pdf

        return ingest_pdf(path)
    elif ext == ".docx":
        console.print(f"[yellow]DOCX ingestion not yet implemented:[/yellow] {path.name}")
        return 0
    elif ext == ".pptx":
        console.print(f"[yellow]PPTX ingestion not yet implemented:[/yellow] {path.name}")
        return 0
    else:
        console.print(f"[yellow]Unsupported format:[/yellow] {path.name}")
        return 0
