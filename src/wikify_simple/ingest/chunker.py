"""Markdown -> [Chunk]. Section-aware, target ~400 tokens per chunk.

Key behaviours:
  - Never split across section boundaries
  - Section type classification (abstract, methods, results, etc.)
  - Conclusion fallback: if no section is typed "conclusion", promote the
    last substantive (non-references/acks/appendix) section
  - Paragraph-level overlap between consecutive chunks within a section
  - Token estimate uses the 4-char rule (consistent with infra/tokens.py)
"""

import hashlib
import re
from collections.abc import Iterable

from ..models import Chunk
from .config import MIN_CHUNK_CHARS, OVERLAP_CHARS, TARGET_CHUNK_CHARS
from .section_classifier import SectionType, classify_section_path

# Section types that are never the "concluding body" of a paper.
_NON_CONCLUSION_TYPES = frozenset(
    {
        SectionType.REFERENCES.value,
        SectionType.ACKNOWLEDGMENTS.value,
        SectionType.APPENDIX.value,
        SectionType.ABSTRACT.value,
    }
)

# Regex for quick detection of citations and equations in chunk text.
_CITATION_RE = re.compile(r"\[[\w\s,\.]+\d{4}\]|\[\d+\]")
_EQUATION_RE = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]", re.DOTALL)


def chunk_document(
    doc_id: str,
    body: str,
    sections: Iterable[tuple[list[str], int, int]],
) -> list[Chunk]:
    chunks: list[Chunk] = []
    ord_ = 0
    for path, start, end in sections:
        section_text = body[start:end]
        section_type = classify_section_path(path).value
        sub_chunks = _split_section(section_text)
        for sub_start, sub_end in sub_chunks:
            text = section_text[sub_start:sub_end].strip()
            if not text:
                continue
            absolute = (start + sub_start, start + sub_end)
            cid = _chunk_id(doc_id, ord_, text)
            chunks.append(
                Chunk(
                    id=cid,
                    doc_id=doc_id,
                    ord=ord_,
                    text=text,
                    char_span=absolute,
                    section_path=list(path),
                    section_type=section_type,
                )
            )
            ord_ += 1

    _apply_conclusion_fallback(chunks)
    return chunks


def _apply_conclusion_fallback(chunks: list[Chunk]) -> None:
    """If no chunk is typed 'conclusion', retype the last substantive section.

    Handles papers where PDF extraction produced no conclusion heading.
    Only applies when the paper has at least 3 distinct section paths
    (otherwise heading detection failed too badly to trust the fallback).
    """
    if any(c.section_type == SectionType.CONCLUSION.value for c in chunks):
        return

    # Walk backwards through unique section paths.
    seen: set[str] = set()
    ordered_paths: list[str] = []
    for c in reversed(chunks):
        key = "/".join(c.section_path)
        if key not in seen:
            seen.add(key)
            ordered_paths.append(key)

    # Don't apply fallback if heading detection largely failed (too few sections).
    if len(ordered_paths) < 3:
        return

    # Find the last section path whose type is not a trailing non-body section.
    target_key: str | None = None
    for path_key in ordered_paths:
        for c in chunks:
            if "/".join(c.section_path) == path_key:
                if c.section_type not in _NON_CONCLUSION_TYPES:
                    target_key = path_key
                break
        if target_key is not None:
            break

    if target_key is None:
        return

    for c in chunks:
        if "/".join(c.section_path) == target_key:
            c.section_type = SectionType.CONCLUSION.value


def _split_section(text: str) -> list[tuple[int, int]]:
    """Greedy paragraph-aware split into ~_TARGET_CHARS chunks with overlap."""
    if len(text) <= TARGET_CHUNK_CHARS:
        return [(0, len(text))]
    out: list[tuple[int, int]] = []
    paragraphs = list(_paragraph_spans(text))
    cur_start = 0
    cur_len = 0
    last_para: tuple[int, int] | None = None

    for ps, pe in paragraphs:
        plen = pe - ps
        if cur_len + plen > TARGET_CHUNK_CHARS and cur_len >= MIN_CHUNK_CHARS:
            out.append((cur_start, ps))
            # Overlap: include the last paragraph of the previous chunk
            # in the next chunk, if it fits within the overlap budget.
            if last_para is not None and (last_para[1] - last_para[0]) <= OVERLAP_CHARS:
                cur_start = last_para[0]
                cur_len = (ps - last_para[0]) + plen
            else:
                cur_start = ps
                cur_len = plen
        else:
            cur_len += plen
        last_para = (ps, pe)
    if cur_start < len(text):
        out.append((cur_start, len(text)))
    return out


def _paragraph_spans(text: str) -> Iterable[tuple[int, int]]:
    i = 0
    n = len(text)
    while i < n:
        j = text.find("\n\n", i)
        if j < 0:
            yield (i, n)
            return
        yield (i, j + 2)
        i = j + 2


def _chunk_id(doc_id: str, ord_: int, text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{doc_id}__c{ord_:04d}__{h}"
