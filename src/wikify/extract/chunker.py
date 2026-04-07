"""Section-aware semantic chunking."""

from __future__ import annotations

import re
import uuid

import tiktoken

from wikify.core.config import settings
from wikify.extract.section_classifier import classify_section_path
from wikify.core.store.models import Chunk

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
    - Fallback: if no conclusion chunk is produced, the last non-references/
      non-acknowledgments section is re-typed as "conclusion" (papers without
      explicit headings still have concluding paragraphs).

    When the section_tree was built from PDF TOC (has page-mapped entries),
    "root" sections get reassigned to the nearest TOC section title.
    """
    sections = _split_into_sections(md_text)

    # If section_tree has TOC-sourced entries, build a fallback map
    # to rescue chunks stuck in "root" (no heading detected by pymupdf4llm).
    toc_titles = _extract_toc_titles(section_tree) if section_tree.get("source") == "toc" else []

    if toc_titles and any(sp == "root" for sp, _ in sections):
        sections = _reassign_root_sections(sections, toc_titles)

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
                    section_type=classify_section_path(section_path).value,
                    content=text,
                    token_count=token_count,
                    chunk_index=idx,
                    has_citations=bool(re.search(r"\[[\w\s,\.]+\d{4}\]|\[\d+\]", text)),
                    has_equations=bool(re.search(r"\$\$.*?\$\$|\\\[.*?\\\]", text, re.DOTALL)),
                )
            )

    _apply_conclusion_fallback(chunks)
    return chunks


# Section types that are never the "concluding body" of a paper.
_NON_CONCLUSION_TYPES = frozenset({"references", "acknowledgments", "appendix", "abstract"})


def _apply_conclusion_fallback(chunks: list[Chunk]) -> None:
    """If no chunk is typed 'conclusion', retype the last substantive section.

    Mutates chunks in-place.  Skips trailing references / acknowledgments /
    appendix sections, then marks the last remaining section as 'conclusion'.
    This handles papers where PDF extraction produced no heading text.
    """
    has_conclusion = any(c.section_type == "conclusion" for c in chunks)
    if has_conclusion:
        return

    # Walk backwards through unique section_paths (preserving order).
    seen: set[str] = set()
    ordered_paths: list[str] = []
    for c in reversed(chunks):
        if c.section_path not in seen:
            seen.add(c.section_path)
            ordered_paths.append(c.section_path)

    # Find the last section path whose type is not a trailing non-body section.
    target_path: str | None = None
    for path in ordered_paths:
        st = classify_section_path(path).value
        if st not in _NON_CONCLUSION_TYPES:
            target_path = path
            break

    if target_path is None:
        return  # All sections are references/acks — nothing useful to promote.

    for c in chunks:
        if c.section_path == target_path:
            c.section_type = "conclusion"


def migrate_section_types() -> dict[str, int]:
    """Reclassify section_type for all existing DB chunks.

    Re-runs ``classify_section_path`` on every chunk's ``section_path``
    and applies the conclusion fallback per paper.  Updates the DB in-place.

    Returns:
        A dict with keys ``total``, ``reclassified``, ``conclusion_fallbacks``.
    """
    from collections import defaultdict

    from sqlmodel import select

    from wikify.core.store.db import get_session
    from wikify.core.store.models import Chunk

    with get_session() as session:
        all_chunks: list[Chunk] = list(session.exec(select(Chunk)).all())

    # Group by paper
    by_paper: dict[str, list[Chunk]] = defaultdict(list)
    for c in all_chunks:
        by_paper[c.paper_id].append(c)

    reclassified = 0
    conclusion_fallbacks = 0

    for paper_id, paper_chunks in by_paper.items():
        # Re-run classifier on each chunk
        for c in paper_chunks:
            new_type = classify_section_path(c.section_path).value
            if new_type != c.section_type:
                c.section_type = new_type
                reclassified += 1

        # Apply conclusion fallback for this paper's chunks
        had_conclusion_before = any(c.section_type == "conclusion" for c in paper_chunks)
        _apply_conclusion_fallback(paper_chunks)
        has_conclusion_after = any(c.section_type == "conclusion" for c in paper_chunks)
        if not had_conclusion_before and has_conclusion_after:
            conclusion_fallbacks += 1
            # Count all the chunks that were changed by the fallback
            reclassified += sum(
                1
                for c in paper_chunks
                if c.section_type == "conclusion"
                # (already mutated in place above)
            )

    # Bulk-write updates back to DB
    with get_session() as session:
        for c in all_chunks:
            session.add(c)
        session.commit()

    return {
        "total": len(all_chunks),
        "reclassified": reclassified,
        "conclusion_fallbacks": conclusion_fallbacks,
    }


def _extract_toc_titles(tree: dict) -> list[str]:
    """Extract section titles from a TOC-sourced section tree (DFS order)."""
    titles: list[str] = []
    for child in tree.get("children", []):
        if child.get("title"):
            titles.append(child["title"])
        titles.extend(_extract_toc_titles(child))
    return titles


def _reassign_root_sections(
    sections: list[tuple[str, str]],
    toc_titles: list[str],
) -> list[tuple[str, str]]:
    """Reassign 'root' sections using TOC titles matched against content.

    When pymupdf4llm fails to detect headings but the PDF has a TOC,
    we scan the root text for TOC title strings and split accordingly.
    """
    result: list[tuple[str, str]] = []
    # Lowercase TOC titles for matching, but keep originals for section paths
    toc_lower = [(t, t.lower()) for t in toc_titles]

    for section_path, text in sections:
        if section_path != "root":
            result.append((section_path, text))
            continue

        # Try to find TOC titles within the root text and split
        lines = text.split("\n")
        current_path = "root"
        current_lines: list[str] = []

        for line in lines:
            line_lower = line.strip().lower()
            matched_title = None
            for orig, lower in toc_lower:
                # Match if the line is (approximately) just the title
                if line_lower and lower and (line_lower == lower or line_lower.startswith(lower)):
                    matched_title = orig
                    break

            if matched_title:
                # Flush current section
                if current_lines:
                    joined = "\n".join(current_lines).strip()
                    if joined:
                        result.append((current_path, joined))
                current_path = matched_title
                current_lines = []
            else:
                current_lines.append(line)

        # Flush final section
        if current_lines:
            joined = "\n".join(current_lines).strip()
            if joined:
                result.append((current_path, joined))

    return result


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
