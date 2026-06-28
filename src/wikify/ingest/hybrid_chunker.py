"""Universal markdown -> chunks via Docling's HybridChunker.

Replaces the section/paragraph splitter and the Docling-direct path with
one tokenizer-aware chunker that runs on every parser's markdown output.
The conversion path is deterministic from saved markdown alone, which
makes ``corpus rechunk`` a pure function of disk state.

Pipeline shape::

    markdown (str)
      -> DocumentConverter().convert_string(format=InputFormat.MD)
      -> DoclingDocument
      -> HybridChunker(tokenizer=...).chunk(dl_doc=doc)
      -> our Chunk dataclasses

The tokenizer is the active embedding model's tokenizer so chunks are
sized in tokens, not characters; the merge pass then joins undersized
adjacent chunks that share heading context. Net effect: longer chunks
when the source supports it, no nano-fragments, headings preserved.

Char spans are recovered by ``markdown.find(chunk.text)`` for the rare
caller that still needs offsets; equation binding uses text-match
(see ``bind_equations_to_chunks(use_text_match=True)``) so the offset
is best-effort, not load-bearing.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..models import Chunk
from .abstract_tagger import tag_abstracts
from .boilerplate import is_boilerplate
from .chunker import _is_boilerplate_chunk
from .config import MIN_CHUNK_ALNUM
from .non_prose import classify_chunk_kind

_CHUNKER: Any = None
_CHUNKER_KEY: tuple[str, int] | None = None


def _alnum_count(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


def _chunk_id(doc_id: str, ord_: int, text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{doc_id}__c{ord_:04d}_{h}"


def _build_chunker(model_id: str, max_tokens: int):
    """Lazily build a HybridChunker bound to the embedder's tokenizer.

    Cached per ``(model_id, max_tokens)`` so the converter and
    tokenizer don't reload on every call.
    """
    global _CHUNKER, _CHUNKER_KEY
    key = (model_id, max_tokens)
    if _CHUNKER is not None and _CHUNKER_KEY == key:
        return _CHUNKER
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )
    from transformers import AutoTokenizer

    tok = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True,
        ),
        max_tokens=max_tokens,
    )
    _CHUNKER = HybridChunker(tokenizer=tok, merge_peers=True)
    _CHUNKER_KEY = key
    return _CHUNKER


def _markdown_to_doc(markdown: str):
    """Materialise a DoclingDocument from a raw markdown string.

    Goes through ``DocumentConverter.convert_string`` so the output is
    structurally identical to ingesting an actual ``.md`` file. The
    converter does heading detection, table parsing, and code-block
    handling on the markdown itself; HybridChunker then chunks that
    structured representation.
    """
    import io

    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.document_converter import DocumentConverter

    stream = DocumentStream(
        name="doc.md",
        stream=io.BytesIO(markdown.encode("utf-8")),
    )
    converter = DocumentConverter(allowed_formats=[InputFormat.MD])
    return converter.convert(stream).document


def _extract_headings(meta) -> list[str]:
    if meta is None:
        return []
    export = getattr(meta, "export_json_dict", None)
    if export is None:
        return []
    try:
        data = export()
    except Exception:
        return []
    headings = data.get("headings") or []
    return [str(h) for h in headings if h]


def _load_cached_doc(cache_path):
    """Load a previously persisted DoclingDocument JSON, or None on miss.

    The cache is written by the Docling parser at ingest time; rechunk
    looks for it before re-parsing markdown. Returns ``None`` (silent
    fallback) when the file is missing, malformed, or Docling is
    unavailable, so the markdown -> DoclingDocument path always works.
    """
    if cache_path is None:
        return None
    try:
        from pathlib import Path as _Path
        path = _Path(cache_path)
        if not path.is_file():
            return None
        from docling_core.types.doc.document import DoclingDocument
        return DoclingDocument.load_from_json(path)
    except Exception:
        return None


def chunk_with_hybrid(
    doc_id: str,
    markdown: str,
    *,
    embed_model_id: str | None = None,
    max_tokens: int | None = None,
    cached_doc_path=None,
) -> list[Chunk]:
    """Chunk *markdown* with Docling's HybridChunker.

    Returns ``Chunk`` records with the same shape as our prior chunker:
    ``id, doc_id, ord, text, char_span, section_path, section_type,
    is_boilerplate``. Equation ids are populated downstream by
    ``bind_equations_to_chunks(use_text_match=True)``.

    Boilerplate filtering: the same hard filter
    (``_is_boilerplate_chunk``) and soft flag (``is_boilerplate``) the
    legacy chunker applied still run here, so existing behaviour for
    publisher-license blocks is preserved.

    When ``cached_doc_path`` is set and exists, the ``DoclingDocument``
    is loaded from that JSON file, skipping the markdown ->
    DoclingDocument re-parse (the dominant cost). Missing or
    malformed cache silently falls through to the markdown path.
    """
    if not markdown.strip():
        return []
    from ..embedding import active_embed_max_tokens, active_embed_model_id
    if embed_model_id is None:
        embed_model_id = active_embed_model_id()
    if max_tokens is None:
        max_tokens = active_embed_max_tokens()
    chunker = _build_chunker(embed_model_id, max_tokens)
    doc = _load_cached_doc(cached_doc_path)
    if doc is None:
        doc = _markdown_to_doc(markdown)

    chunks: list[Chunk] = []
    ord_ = 0
    for dc in chunker.chunk(dl_doc=doc):
        text = (dc.text or "").strip()
        if not text:
            continue
        if _alnum_count(text) < MIN_CHUNK_ALNUM:
            continue
        if _is_boilerplate_chunk(text):
            continue
        headings = _extract_headings(getattr(dc, "meta", None))
        section_path = headings or ["body"]
        section_type = classify_chunk_kind(text, section_path)

        # Best-effort char offset. Docling's markdown reader normalises
        # some whitespace, anchors, and table layout, so an exact
        # ``markdown.find(text)`` misses on long chunks. Prefix match on
        # the first 80 chars lands within the right region whenever the
        # chunk's leading sentence survives the normalisation, which is
        # the common case. Equation/citation binding uses text-match so
        # the offset is best-effort, not load-bearing.
        head = text[:80]
        offset = markdown.find(head) if head else -1
        if offset < 0:
            offset = 0
        char_span = (offset, offset + len(text))

        chunks.append(
            Chunk(
                id=_chunk_id(doc_id, ord_, text),
                doc_id=doc_id,
                ord=ord_,
                text=text,
                char_span=char_span,
                section_path=section_path,
                section_type=section_type,
            )
        )
        ord_ += 1

    for c in chunks:
        c.is_boilerplate = is_boilerplate(c.text, c.section_path)
        c.section_type = classify_chunk_kind(
            c.text,
            c.section_path,
            is_boilerplate=c.is_boilerplate,
        )

    tag_abstracts(chunks)
    return chunks
