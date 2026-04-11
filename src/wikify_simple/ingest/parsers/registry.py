"""Dispatch a source file to the right parser based on suffix."""

from dataclasses import dataclass, field
from pathlib import Path

from wikify_simple.models import DocImage, DocKind


@dataclass
class ParseResult:
    markdown: str
    sections: list[tuple[list[str], int, int]]  # (heading path, char start, char end)
    images: list[DocImage] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    title: str = ""


def parse_file(path: Path) -> tuple[DocKind, ParseResult]:
    suffix = path.suffix.lower().lstrip(".")
    match suffix:
        case "md" | "markdown" | "txt":
            from . import markdown as p

            return "md", p.parse(path)
        case "pdf":
            from . import pdf as p

            return "pdf", p.parse(path)
        case "docx":
            from . import docx as p

            return "docx", p.parse(path)
        case "pptx":
            from . import pptx as p

            return "pptx", p.parse(path)
        case "html" | "htm":
            from . import html as p

            return "html", p.parse(path)
        case _:
            raise ValueError(f"unsupported file type: {path.suffix}")
