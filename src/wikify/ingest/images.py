"""Image persistence for wikify.

Parsers emit typed ``RawImage`` records via ``ParseResult.raw_images``
and the pipeline calls ``save_doc_images`` to persist each image as:

    corpus/images/{paper_slug}/{label}.{ext}      # binary
    corpus/images/{paper_slug}/{label}.{ext}.json # sidecar

Where ``label`` is a caption-resolved name (``Fig_1``, ``Table_2``,
``Scheme_1a``) when the figure extractor matched a caption, falling back
to ``p{N}_img{i}`` when no caption could be resolved. The folder slug is a
clean truncation of the source filename (no hash suffix) so paths stay
under the Windows MAX_PATH limit.

Sidecars carry ``{id, caption, alt_text, page, near_chunk_ids,
source_bbox, ...}`` so they can be grepped or loaded back via
``read_doc_images``. The sidecar is the on-disk source of truth for the
post-ingest image inventory.

"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from ..models import Chunk, DocImage
from .parsers.registry import RawImage

__all__ = [
    "save_doc_images",
    "load_sidecars",
    "caption_chunks_for",
    "link_chunks_to_images",
    "rewrite_sidecar_near_chunks",
]

logger = logging.getLogger(__name__)


def save_doc_images(
    doc_id: str,
    image_dir: Path,
    raw_images: list[RawImage],
) -> list[DocImage]:
    """Persist typed RawImage records as binaries + sidecar JSON; return DocImage records."""
    out: list[DocImage] = []
    image_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for i, rec in enumerate(raw_images or []):
        caption = rec.caption or ""
        alt_text = rec.alt_text or ""
        if rec.data:
            ext = (rec.ext or "png").lstrip(".")
            stem = _figure_stem(rec.label, i)
            stem = _disambiguate(stem, used_names)
            used_names.add(stem)
            img_id = f"{doc_id}/{stem}"
            bin_path = image_dir / f"{stem}.{ext}"
            bin_path.write_bytes(rec.data)
            rel_path = str(bin_path)
            _write_sidecar(
                bin_path,
                {
                    "id": img_id,
                    "path": rel_path,
                    "caption": caption,
                    "alt_text": alt_text,
                    "page": rec.page,
                    "near_chunk_ids": [],
                    "source_bbox": list(rec.bbox) if rec.bbox else None,
                    "label": rec.label,
                    "media_type": rec.media_type,
                    "width": rec.width,
                    "height": rec.height,
                    "content_hash": rec.content_hash,
                    "source_url": None,
                },
            )
            out.append(
                DocImage(
                    id=img_id,
                    path=rel_path,
                    caption=caption,
                    alt_text=alt_text,
                    page=rec.page,
                )
            )
        else:
            url = rec.url or ""
            if not url:
                continue
            stem = _figure_stem(rec.label, i)
            stem = _disambiguate(stem, used_names)
            used_names.add(stem)
            img_id = f"{doc_id}/{stem}"
            side = image_dir / f"{stem}.url.json"
            payload = {
                "id": img_id,
                "path": url,
                "caption": caption,
                "alt_text": alt_text,
                "page": rec.page,
                "near_chunk_ids": [],
                "source_bbox": None,
                "label": None,
                "media_type": "figure",
                "source_url": url,
            }
            side.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            out.append(
                DocImage(
                    id=img_id,
                    path=url,
                    caption=caption,
                    alt_text=alt_text,
                    page=rec.page,
                )
            )
    return out


def _write_sidecar(bin_path: Path, payload: dict) -> None:
    side = bin_path.with_suffix(bin_path.suffix + ".json")
    side.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_LABEL_RE = re.compile(
    r"^\s*(?P<kind>fig(?:ure)?|table|scheme)\.?\s*(?P<num>\d+)\s*(?P<sub>[a-z])?",
    re.IGNORECASE,
)


def _figure_stem(label: str | None, index: int) -> str:
    """Build a clean human-readable filename stem from a caption label.

    Normalises ``"Fig. 1"`` / ``"figure 1a"`` / ``"Table 2"`` to
    ``Figure_01``, ``Figure_01a``, ``Table_02``. Falls back to
    ``fig_{index:03d}`` when no label is present and to a sanitised
    version of the raw label when the regex does not match (e.g. an
    unusual caption convention).
    """
    if label:
        m = _LABEL_RE.match(label)
        if m:
            kind = m.group("kind").lower()
            kind = "Figure" if kind.startswith("fig") else kind.capitalize()
            num = int(m.group("num"))
            sub = (m.group("sub") or "").lower()
            return f"{kind}_{num:02d}{sub}"
        safe = re.sub(r"[^\w.-]", "_", label)
        safe = re.sub(r"_+", "_", safe).strip("_.")
        if safe:
            return safe
    return f"fig_{index:03d}"


def _disambiguate(stem: str, used: set[str]) -> str:
    """Append a numeric suffix when two images would collide on stem."""
    if stem not in used:
        return stem
    i = 2
    while f"{stem}_{i}" in used:
        i += 1
    return f"{stem}_{i}"


def load_sidecars(image_dir: Path) -> list[DocImage]:
    """Load DocImage records from sidecar JSONs in ``image_dir``.

    The on-disk sidecar is authoritative: ``store/corpus.read_doc_images``
    uses this to round-trip images without going through the Document
    JSON index.
    """
    out: list[DocImage] = []
    if not image_dir.exists():
        return out
    for p in sorted(image_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            DocImage(
                id=data.get("id", p.stem),
                path=data.get("path", ""),
                caption=data.get("caption", "") or "",
                alt_text=data.get("alt_text", "") or "",
                page=data.get("page"),
                near_chunk_ids=data.get("near_chunk_ids", []) or [],
            )
        )
    return out


# Inline figure-reference patterns. Match "Fig. 1", "Figure 2a", "Table 3",
# "Scheme 4" anywhere in chunk prose. Sub-letter (`a`–`z`) is captured but
# we resolve to the parent figure number — the alias map already covers
# both the bare and sub-lettered forms.
_INLINE_FIGREF_RE = re.compile(
    r"\b(?P<kind>Fig(?:ure)?|Table|Scheme|Sch)\.?\s*(?P<num>\d+)(?P<sub>[a-z])?\b",
    re.IGNORECASE,
)


def _normalize_alias(s: str) -> str:
    """Mirror of ``store.images_index._norm`` — kept inline so this module
    has no dependency on the index layer."""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# Parses both ``"Figure 1"``-style caption prefixes and ``"Figure_01"``-style
# filename stems. The leading ``\W*`` accommodates Unicode glyphs and bullets
# that pymupdf4llm sometimes prepends to figure captions.
_STEM_OR_LABEL_RE = re.compile(
    r"^\W*(?P<kind>fig(?:ure)?|table|scheme|sch)[._\s]*(?P<num>\d+)\s*(?P<sub>[a-z])?",
    re.IGNORECASE,
)


def _expand_aliases(kind: str, num: int, sub: str) -> list[str]:
    """Return the normalized alias forms for one (kind, num, sub) triple.

    Generates ``figure 1``, ``figure 01``, ``fig 1``, ``fig 01`` (and the
    sub-lettered variants) so chunk text using any common form resolves.
    """
    out: set[str] = set()
    for k_word in (kind, kind[:3]):  # "figure"/"fig", "table"/"tab", "scheme"/"sch"
        for n_str in (str(num), f"{num:02d}"):
            out.add(_normalize_alias(f"{k_word} {n_str}"))
            if sub:
                out.add(_normalize_alias(f"{k_word} {n_str}{sub}"))
    return sorted(out)


def _build_alias_map(images: Iterable[DocImage]) -> dict[str, list[str]]:
    """Map normalized alias → list of image ids for one doc.

    For each image we try to derive a (kind, num, sub) triple from the
    *stem first* (e.g. ``Figure_01`` → ``figure``, ``1``), then from the
    *caption* (e.g. ``"Figure 1. Schematic..."``). The stem-first
    ordering matters because most captions don't actually start with the
    label — they jump straight into the description, so the stem is the
    only reliable source of the figure number for those.

    Multiple images can resolve to the same alias when the figure
    extractor produces a duplicate-disambiguated stem (``Figure_01`` and
    ``Figure_01_2`` both denote the same logical figure with the same
    caption — typically a multi-pane figure that the extractor split
    into two image binaries). The previous version used a one-to-one
    map and silently dropped the second image; this returns a list so a
    chunk that says "Fig. 1" links to BOTH binaries. Order is
    insertion order so the first image still wins single-id lookups.
    """
    aliases: dict[str, list[str]] = {}

    def _push(alias: str, img_id: str) -> None:
        bucket = aliases.setdefault(alias, [])
        if img_id not in bucket:
            bucket.append(img_id)

    for im in images:
        stem = (im.id.rsplit("/", 1)[-1] or "").strip()
        if not stem:
            continue
        _push(_normalize_alias(stem), im.id)

        triple: tuple[str, int, str] | None = None
        # 1. Stem-derived: "Figure_01" / "Table_2a" / "Scheme_3"
        m = _STEM_OR_LABEL_RE.match(stem)
        if m:
            kind_raw = m.group("kind").lower()
            kind = "figure" if kind_raw.startswith("fig") else kind_raw
            triple = (kind, int(m.group("num")), (m.group("sub") or "").lower())
        else:
            # 2. Caption-derived: only used when the stem didn't parse
            #    (parser fell back to ``fig_001``-style names).
            caption = (im.caption or "").strip()
            m = _STEM_OR_LABEL_RE.match(caption) if caption else None
            if m:
                kind_raw = m.group("kind").lower()
                kind = "figure" if kind_raw.startswith("fig") else kind_raw
                triple = (kind, int(m.group("num")), (m.group("sub") or "").lower())

        if triple is None:
            continue
        kind, num, sub = triple
        for alias in _expand_aliases(kind, num, sub):
            _push(alias, im.id)
    return aliases


def link_chunks_to_images(
    chunks: Iterable[Chunk],
    images: list[DocImage],
) -> dict[str, list[str]]:
    """Populate ``image.near_chunk_ids`` from inline figure refs in chunks.

    Scans every chunk's text for ``Fig. N``/``Figure N``/``Table N``/
    ``Scheme N`` patterns and, when the parsed label resolves to one of
    ``images``, appends the chunk id to that image's ``near_chunk_ids``.
    Mutates the ``DocImage`` instances in place AND returns a parallel
    ``{image_id: [chunk_id, ...]}`` map so the caller can also rewrite
    sidecar JSONs without re-deriving the data.

    The match is order-preserving and deduplicated per image. Caption
    chunks (``section_path[0] == "__image__"``) are skipped — they ARE
    the image, not a body discussion of it.
    """
    if not images:
        return {}
    aliases = _build_alias_map(images)
    if not aliases:
        return {}
    by_id: dict[str, DocImage] = {im.id: im for im in images}
    near: dict[str, list[str]] = {im.id: [] for im in images}
    seen: dict[str, set[str]] = {im.id: set() for im in images}

    for chunk in chunks:
        sp = list(chunk.section_path or [])
        if sp and sp[0] == "__image__":
            continue
        if not chunk.text:
            continue
        for m in _INLINE_FIGREF_RE.finditer(chunk.text):
            kind_raw = m.group("kind").lower()
            kind = "figure" if kind_raw.startswith("fig") else kind_raw
            num = int(m.group("num"))
            sub = (m.group("sub") or "").lower()
            # Try most specific first (with sub-letter), then parent.
            target_ids: list[str] = []
            for candidate in (
                _normalize_alias(f"{kind} {num}{sub}") if sub else None,
                _normalize_alias(f"{kind} {num}"),
                _normalize_alias(f"{kind[:3]} {num}{sub}") if sub else None,
                _normalize_alias(f"{kind[:3]} {num}"),
            ):
                if not candidate:
                    continue
                bucket = aliases.get(candidate)
                if bucket:
                    target_ids = bucket
                    break
            # Append the chunk to EVERY matched image so duplicate-
            # disambiguated stems (Figure_01 + Figure_01_2 from the
            # same caption) both get linked, not just the first.
            for target_id in target_ids:
                if chunk.id not in seen[target_id]:
                    seen[target_id].add(chunk.id)
                    near[target_id].append(chunk.id)

    # Mutate the DocImage records so the in-memory Document is consistent
    # with the sidecar rewrite the caller will perform.
    for img_id, ids in near.items():
        by_id[img_id].near_chunk_ids = list(ids)
    return near


def rewrite_sidecar_near_chunks(image_dir: Path, near: dict[str, list[str]]) -> None:
    """Patch existing sidecar JSONs to set ``near_chunk_ids`` for each image.

    Reads every ``*.json`` in ``image_dir``, looks up its id in ``near``,
    and rewrites the file with the populated list. No-op for ids absent
    from the map. Used by ``pipeline.py`` after chunks are linked.
    """
    if not image_dir.exists() or not near:
        return
    for p in sorted(image_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        img_id = data.get("id")
        if not img_id or img_id not in near:
            continue
        data["near_chunk_ids"] = list(near[img_id])
        try:
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            continue


def caption_chunks_for(doc_id: str, images: Iterable[DocImage], ord_offset: int) -> list[Chunk]:
    """Wrap image captions as Chunks so the embedder/graph can index them."""
    out: list[Chunk] = []
    ord_ = ord_offset
    for im in images:
        raw = (im.caption or "").strip() or (im.alt_text or "").strip()
        if not raw:
            continue
        text = _normalize_caption(raw)
        if not text:
            continue
        out.append(
            Chunk(
                id=f"{im.id}__caption",
                doc_id=doc_id,
                ord=ord_,
                text=text,
                char_span=(0, len(text)),
                section_path=["__image__", im.id],
            )
        )
        ord_ += 1
    return out


def _normalize_caption(text: str) -> str:
    """Collapse whitespace + drop replacement chars from a raw caption.

    Captions come straight from the PDF figure extractor and often carry
    embedded newlines mid-sentence ("realizations. \\n(a) Memristor"). The
    caption is a single semantic unit — fold all whitespace to single
    spaces so downstream chunking, embedding, and LLM context all see a
    clean sentence.
    """
    # Drop invisible / replacement characters.
    text = re.sub(r"[\ufffd\u200b\u200c\u200d\u2060\ufeff\u00ad]", "", text)
    # Fold every whitespace run (newlines included) to a single space.
    text = re.sub(r"\s+", " ", text).strip()
    return text
