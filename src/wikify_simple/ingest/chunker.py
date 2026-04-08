"""Markdown -> [Chunk]. Section-aware, target ~400 tokens per chunk.

Token estimate is the cheap 4-char-per-token rule of thumb (consistent with
infra/tokens.py). Chunks never cross a section boundary.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..models import Chunk

_TARGET_CHARS = 1600  # ~400 tokens at 4 chars/token
_MIN_CHARS = 200  # don't emit a chunk smaller than this unless section is small


def chunk_document(
    doc_id: str,
    body: str,
    sections: Iterable[tuple[list[str], int, int]],
) -> list[Chunk]:
    chunks: list[Chunk] = []
    ord_ = 0
    for path, start, end in sections:
        section_text = body[start:end]
        for sub_start, sub_end in _split_section(section_text):
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
                )
            )
            ord_ += 1
    return chunks


def _split_section(text: str) -> list[tuple[int, int]]:
    """Greedy paragraph-aware split into ~_TARGET_CHARS chunks."""
    if len(text) <= _TARGET_CHARS:
        return [(0, len(text))]
    out: list[tuple[int, int]] = []
    paragraphs = list(_paragraph_spans(text))
    cur_start = 0
    cur_len = 0
    for ps, pe in paragraphs:
        plen = pe - ps
        if cur_len + plen > _TARGET_CHARS and cur_len >= _MIN_CHARS:
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
