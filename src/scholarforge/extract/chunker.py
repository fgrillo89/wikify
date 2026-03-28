"""Section-aware semantic chunking."""

from __future__ import annotations

import re
import uuid

import tiktoken

from scholarforge.config import settings
from scholarforge.store.models import Chunk

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def chunk_sections(md_text: str, section_tree: dict, paper_id: str) -> list[Chunk]:
    """Split markdown text into section-aware chunks.

    Rules:
    - Never split across section boundaries.
    - Target chunk size: settings.chunk_target_tokens.
    - Keep equations and citations intact (don't split mid-line).
    - Overlap between consecutive chunks within a section.
    """
    sections = _split_into_sections(md_text)
    chunks = []

    for section_path, section_text in sections:
        section_chunks = _chunk_text(
            section_text,
            target=settings.chunk_target_tokens,
            max_tokens=settings.chunk_max_tokens,
            overlap=settings.chunk_overlap_tokens,
        )
        for idx, text in enumerate(section_chunks):
            token_count = count_tokens(text)
            chunks.append(
                Chunk(
                    id=str(uuid.uuid4()),
                    paper_id=paper_id,
                    section_path=section_path,
                    content=text,
                    token_count=token_count,
                    chunk_index=idx,
                    has_citations=bool(re.search(r"\[[\w\s,\.]+\d{4}\]|\[\d+\]", text)),
                    has_equations=bool(re.search(r"\$\$.*?\$\$|\\\[.*?\\\]", text, re.DOTALL)),
                )
            )

    return chunks


def _split_into_sections(md_text: str) -> list[tuple[str, str]]:
    """Split markdown into (section_path, text) pairs."""
    lines = md_text.split("\n")
    sections: list[tuple[str, str]] = []
    current_path = "root"
    current_lines: list[str] = []
    heading_stack: list[tuple[int, str]] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            # Flush current section
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append((current_path, text))
                current_lines = []

            # Parse heading level and title
            level = 0
            for ch in stripped:
                if ch == "#":
                    level += 1
                else:
                    break
            heading = stripped[level:].strip()

            # Update heading stack
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))

            current_path = ".".join(h for _, h in heading_stack)
        else:
            current_lines.append(line)

    # Flush last section
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_path, text))

    return sections


def _chunk_text(
    text: str,
    target: int,
    max_tokens: int,
    overlap: int,
) -> list[str]:
    """Split text into chunks respecting paragraph and line boundaries."""
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_tokens = count_tokens(para)

        if current_tokens + para_tokens > max_tokens and current:
            chunks.append("\n\n".join(current))
            # Overlap: keep last paragraph if it fits
            if overlap > 0 and current:
                last = current[-1]
                if count_tokens(last) <= overlap:
                    current = [last]
                    current_tokens = count_tokens(last)
                else:
                    current = []
                    current_tokens = 0
            else:
                current = []
                current_tokens = 0

        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text]
