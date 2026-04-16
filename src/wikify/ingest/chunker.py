"""Markdown -> [Chunk]. Section-as-chunk for long-context embedders.

Key behaviours:
  - Never split across section boundaries
  - Emit a whole section as a single chunk when its length fits the embedder
  - Only split sections that exceed ``max_chunk_chars()``; split at paragraph
    boundaries greedily into ``TARGET_CHUNK_CHARS`` pieces
  - No inter-chunk overlap: section-level chunks carry their own local context
  - Section type classification (abstract, methods, results, etc.)
  - Conclusion fallback: if no section is typed "conclusion", promote the
    last substantive (non-references/acks/appendix) section
"""

import hashlib
import re
from collections.abc import Iterable

from ..models import Chunk
from .config import (
    MIN_CHUNK_ALNUM,
    MIN_CHUNK_CHARS,
    TARGET_CHUNK_CHARS,
    max_chunk_chars,
)
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
            # Drop chunks whose stripped text is essentially markdown
            # noise (e.g. ``"##"`` or ``"**\n\n## _"``). These slip
            # through when section detection produces an empty span
            # between two adjacent headings. Counted as alphanumeric
            # characters because the chunker shouldn't reject short
            # but valid chunks like equations or single sentences.
            if _alnum_count(text) < MIN_CHUNK_ALNUM:
                continue
            # Hard cap on chunk size: split anything above the active
            # embedder's context window into sentence-bounded pieces so the
            # tokenizer never silently truncates a chunk.
            for piece_start, piece_end in _split_oversize(text):
                piece = text[piece_start:piece_end].strip()
                if not piece or _alnum_count(piece) < MIN_CHUNK_ALNUM:
                    continue
                absolute = (start + sub_start, start + sub_end)
                cid = _chunk_id(doc_id, ord_, piece)
                chunks.append(
                    Chunk(
                        id=cid,
                        doc_id=doc_id,
                        ord=ord_,
                        text=piece,
                        char_span=absolute,
                        section_path=list(path),
                        section_type=section_type,
                    )
                )
                ord_ += 1

    _apply_conclusion_fallback(chunks)
    return chunks


_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def _alnum_count(text: str) -> int:
    return len(_ALNUM_RE.findall(text))


# Sentence-end pattern: period / question mark / exclamation followed by
# whitespace and an uppercase / digit start. Catches the common cases
# without dragging in a heavyweight sentence tokenizer.
_SENT_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[0-9])")


def _split_oversize(text: str) -> list[tuple[int, int]]:
    """Split a chunk text whose length exceeds ``max_chunk_chars()``.

    Returns ``[(0, len(text))]`` for normal-sized chunks (the common
    path). Oversized chunks are sliced at sentence boundaries; if no
    sentence boundary is available the slice falls back to the nearest
    word boundary so we never produce mid-word truncation.
    """
    cap = max_chunk_chars()
    if len(text) <= cap:
        return [(0, len(text))]
    out: list[tuple[int, int]] = []
    cursor = 0
    n = len(text)
    while cursor < n:
        target = min(cursor + cap, n)
        if target == n:
            out.append((cursor, n))
            break
        window = text[cursor:target]
        boundaries = list(_SENT_BOUNDARY_RE.finditer(window))
        if boundaries and boundaries[-1].end() >= MIN_CHUNK_CHARS:
            cut = cursor + boundaries[-1].end()
        else:
            space = text.rfind(" ", cursor + MIN_CHUNK_CHARS, target)
            cut = space + 1 if space > cursor else target
        out.append((cursor, cut))
        cursor = cut
    return out


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
    """Emit the whole section as one chunk when it fits; split only if oversize.

    With a long-context embedder (e.g. nomic v1.5 at 8192 tokens) most sections
    fit in a single chunk. Only split when the section exceeds the embedder's
    window; split at paragraph boundaries greedily into ``TARGET_CHUNK_CHARS``
    pieces. No overlap — section chunks carry enough local context on their
    own, and the pipeline's neighbour retrieval gives callers the rest.
    """
    cap = max_chunk_chars()
    if len(text) <= cap:
        return [(0, len(text))]
    out: list[tuple[int, int]] = []
    paragraphs = list(_paragraph_spans(text))
    cur_start = 0
    cur_len = 0

    for ps, pe in paragraphs:
        plen = pe - ps
        if cur_len + plen > TARGET_CHUNK_CHARS and cur_len >= MIN_CHUNK_CHARS:
            out.append((cur_start, ps))
            cur_start = ps
            cur_len = plen
        else:
            cur_len += plen
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
