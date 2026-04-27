"""Ingest DAG: four-phase per-source pipeline + step functions.

Today's serial ``_parse_worker`` loop does four things back-to-back for
every source: open the PDF, hit CrossRef / doi.org, run Marker/Docling,
fuse metadata.  Two of those (DOI resolution and content parsing) are
on independent resources — one is network-bound, the other GPU-bound —
so running them sequentially wastes wall-clock time.

The ingest DAG splits the work into four passes so we can overlap
passes 2 and 3 in one ``kind="mixed"`` wave (see ``dag.run_dag``):

1. ``metadata_probe`` (fast, threads):  open each PDF once with fitz,
   read XMP + /Info, parse the filename, scan the cover/last pages for
   a DOI.  Publishes a per-doc probe dict into ``ctx`` and collects a
   deduplicated list of DOIs to resolve.  Non-PDF sources get an empty
   probe (their parsers own metadata extraction).

2. ``doi_resolve`` (async, part of the mixed wave):  batch-resolve all
   probed DOIs through ``util.doi_resolver.resolve_many`` — CrossRef
   batch + doi.org fallback, with SQLite cache at
   ``<corpus>/.citestore.db``.  Publishes ``resolved_metadata`` into
   ctx.  Runs concurrently with pass 3.

3. ``content_parse`` (process pool, part of the mixed wave):  the same
   ProcessPoolExecutor fan-out the serial loop used, but every parse
   call forwards ``skip_metadata=True`` so the expensive PDF-reopen
   inside ``assemble_pdf_metadata`` is deferred to pass 4.  Docs are
   persisted to disk with placeholder metadata; receipts are collected
   into ctx.

4. ``fuse_metadata`` (threads, sequential step):  for every PDF doc
   written in pass 3, load the markdown body back, call
   ``assemble_pdf_metadata(path, md_text, resolved=resolved_metadata
   [xmp_doi])``, merge the result over ``doc.metadata`` (preserving
   parser-specific keys), re-persist the Document JSON and enriched
   markdown.  Non-PDFs skip this — their metadata was already produced
   by their parser in pass 3.

The ingest DAG runs end-to-end before the refresh DAG (both in
``pipeline.ingest_corpus``); they share the ``ctx`` dict so timings
line up on one report.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from ..api import Corpus
from .dag import Step, Wave

# ---------------------------------------------------------------------------
# Per-source records threaded through passes
# ---------------------------------------------------------------------------


class _Probe:
    """Lightweight probe of a single PDF source, produced in pass 1.

    Not a dataclass because it holds a ``fitz`` handle transiently only
    during ``_ingest_metadata_probe``; by the time the probe is stored
    in ``ctx`` the handle is closed and only plain data remains.
    """

    __slots__ = ("path", "xmp_doi", "md_doi_candidate")

    def __init__(
        self,
        *,
        path: Path,
        xmp_doi: str,
        md_doi_candidate: str,
    ) -> None:
        self.path = path
        self.xmp_doi = xmp_doi
        self.md_doi_candidate = md_doi_candidate


# ---------------------------------------------------------------------------
# Pass 1 — metadata probe
# ---------------------------------------------------------------------------


def _ingest_metadata_probe(ctx: dict) -> None:
    """Open each source once, collect candidate DOIs for batch resolution.

    PDFs: open with fitz, read the XMP ``prism:doi`` field, and fall back
    to a raw-page regex scan when XMP is silent.
    DOCX: open with ``python-docx``, concat body paragraphs + the core
    properties (``subject`` / ``description`` / ``keywords``), and regex
    the first 10 KB for a DOI. Less aggressive than the PDF path because
    docx rarely holds a DOI outside the body text.

    Results are published into ``ctx`` as:

    - ``probes``:  ``{path_str: _Probe}`` for every source we probed.
    - ``dois_to_resolve``:  deduplicated lowercase DOI strings.

    Sources we can't probe (unreadable, missing, or other formats) get
    an empty probe so pass 4 still sees the key.
    """
    from .metadata import extract_doi, extract_pdf_doi_fallback
    from .xmp import read_xmp

    sources: list[Path] = ctx["sources_to_parse"]
    probes: dict[str, _Probe] = {}
    dois: list[str] = []

    for src in sources:
        ext = src.suffix.lower()
        xmp_doi = ""
        md_doi = ""

        if ext == ".pdf":
            try:
                import fitz  # pymupdf
            except Exception:  # noqa: BLE001 - pymupdf missing entirely
                probes[str(src)] = _Probe(path=src, xmp_doi="", md_doi_candidate="")
                continue
            try:
                doc = fitz.open(str(src))
            except Exception:  # noqa: BLE001 - broken PDF
                probes[str(src)] = _Probe(path=src, xmp_doi="", md_doi_candidate="")
                continue
            try:
                xmp = read_xmp(doc) or {}
                raw = xmp.get("doi") or ""
                if raw:
                    xmp_doi = extract_doi(raw) or ""
            finally:
                doc.close()
            # Raw-page DOI scan (same helper used as a per-page fallback).
            # This is what lets pass 4 skip the re-scan: we've done it once
            # here and pass 2 can resolve against the resulting DOI.
            if not xmp_doi:
                md_doi = extract_pdf_doi_fallback(src) or ""
        elif ext == ".docx":
            md_doi = _probe_docx_doi(src)
        else:
            # Other formats (html, md, pptx): parser owns its own DOI
            # extraction; pass 2 will miss them here, but the refresh
            # DAG's bibliography step still resolves anything with a DOI.
            pass

        probes[str(src)] = _Probe(
            path=src,
            xmp_doi=xmp_doi,
            md_doi_candidate=md_doi,
        )
        for cand in (xmp_doi, md_doi):
            if cand:
                dois.append(cand.lower())

    ctx["probes"] = probes
    ctx["dois_to_resolve"] = list(dict.fromkeys(dois))


def _probe_docx_doi(path: Path) -> str:
    """Return the first DOI in a docx body + core properties, or ``""``.

    Scans a bounded 10 KB window of paragraph text for speed — DOIs on
    research papers are printed on page 1 in every house style we've
    seen. Also checks ``core_properties.subject/description/keywords``
    in case a publisher template stashes the DOI there.
    """
    from .metadata import extract_doi

    try:
        from docx import Document
    except Exception:  # noqa: BLE001 - python-docx missing
        return ""
    try:
        doc = Document(str(path))
    except Exception:  # noqa: BLE001 - broken docx
        return ""

    # Short-circuit on core properties first — cheap and sometimes decisive.
    props = doc.core_properties
    for field in ("subject", "description", "keywords"):
        val = getattr(props, field, None) or ""
        if not val:
            continue
        found = extract_doi(val)
        if found:
            return found

    # Bounded body scan.
    buf: list[str] = []
    budget = 10_000
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        buf.append(text)
        budget -= len(text)
        if budget <= 0:
            break
    return extract_doi("\n".join(buf)) or ""


# ---------------------------------------------------------------------------
# Pass 2 — DOI batch resolution (async step in the mixed wave)
# ---------------------------------------------------------------------------


async def _ingest_doi_resolve(ctx: dict) -> None:
    """Resolve every probed DOI in one batch.

    Delegates to ``util.doi_resolver.resolve_many`` via
    ``asyncio.to_thread`` — the resolver owns its own event loop and
    we don't want a nested ``asyncio.run``.  Publishes
    ``resolved_metadata: {lower_doi: record}`` into ctx.  Empty dict
    when nothing to resolve; pass 4 falls back to the per-doc
    resolution chain.
    """
    from ..util.doi_resolver import resolve_many

    dois: list[str] = ctx.get("dois_to_resolve") or []
    if not dois:
        ctx["resolved_metadata"] = {}
        return
    paths: Corpus = ctx["paths"]
    cache_path = paths.root / ".citestore.db"
    resolved = await asyncio.to_thread(
        resolve_many, dois, cache_path=cache_path,
    )
    ctx["resolved_metadata"] = resolved


# ---------------------------------------------------------------------------
# Pass 3 — content parse (sync step in the mixed wave, process-pool inside)
# ---------------------------------------------------------------------------


def _ingest_content_parse(ctx: dict) -> None:
    """Stream-parse every source through the existing worker pool.

    Uses ``pipeline._stream_parse_and_persist`` with ``skip_metadata=True``
    for PDFs — the content is written to disk but metadata fusion is
    deferred to pass 4.  Non-PDF sources keep their inline metadata.
    Publishes ``receipts`` into ctx.
    """
    from .pipeline import _stream_parse_and_persist

    sources: list[Path] = ctx["sources_to_parse"]
    paths: Corpus = ctx["paths"]
    parser_backend: str = ctx.get("parser_backend", "default")
    max_workers = ctx.get("max_workers")

    receipts = _stream_parse_and_persist(
        sources,
        paths,
        max_workers,
        parser_backend,
        skip_metadata=True,
    )
    ctx["receipts"] = receipts


# ---------------------------------------------------------------------------
# Pass 4 — fuse metadata (sequential, sync)
# ---------------------------------------------------------------------------


def _ingest_fuse_metadata(ctx: dict) -> None:
    """Run ``assemble_pdf_metadata`` against the persisted markdown.

    For every receipt whose source is a PDF: load the markdown body
    back from ``markdown/{doc_id}.md`` (stripping frontmatter + edges),
    call ``assemble_pdf_metadata`` with the pre-resolved DOI record,
    merge the result into ``doc.metadata`` (preserving parser-specific
    keys), re-persist the Document JSON and enriched markdown.

    Non-PDF sources are skipped — their parsers owned metadata from
    pass 3.
    """
    from wikify.corpus.chunks import _doc_from_dict, atomic_write_text
    from wikify.corpus.doc_markdown import write_doc_markdown

    from .metadata import assemble_pdf_metadata
    from .pipeline import _read_body_from_doc_markdown

    paths: Corpus = ctx["paths"]
    receipts = ctx.get("receipts") or []
    probes: dict[str, _Probe] = ctx.get("probes") or {}
    resolved_by_doi: dict[str, dict] = ctx.get("resolved_metadata") or {}

    n_fused = 0
    for receipt in receipts:
        src_path = Path(receipt.src_path)
        if src_path.suffix.lower() != ".pdf":
            continue
        md_path = paths.markdown_dir / f"{receipt.doc_id}.md"
        doc_json = paths.docs_dir / f"{receipt.doc_id}.json"
        if not md_path.exists() or not doc_json.exists():
            continue
        body = _read_body_from_doc_markdown(md_path)

        probe = probes.get(str(src_path))
        resolved_record: dict | None = None
        doi_hint = ""
        if probe is not None:
            for cand in (probe.xmp_doi, probe.md_doi_candidate):
                if not cand:
                    continue
                # First non-empty probe DOI is the hint — it skips the
                # raw-PDF fallback scan in pass 4 even if CrossRef missed.
                if not doi_hint:
                    doi_hint = cand
                if cand.lower() in resolved_by_doi:
                    rec = resolved_by_doi[cand.lower()]
                    if rec:
                        resolved_record = rec
                        break

        try:
            new_metadata = assemble_pdf_metadata(
                src_path, body, resolved=resolved_record, doi_hint=doi_hint,
            )
        except Exception as exc:  # noqa: BLE001 - per-doc, keep going
            print(f"[ingest] fuse FAIL {receipt.doc_id}: {exc}", file=sys.stderr)
            continue

        # Merge new fused metadata into the persisted doc; preserve any
        # parser-specific keys already written (e.g. _docling_chunks was
        # popped by the worker before write, but future keys may stay).
        doc_data = json.loads(doc_json.read_text(encoding="utf-8"))
        merged = dict(doc_data.get("metadata") or {})
        merged.update(new_metadata)
        doc_data["metadata"] = merged
        if new_metadata.get("title"):
            doc_data["title"] = new_metadata["title"]

        atomic_write_text(doc_json, json.dumps(doc_data))

        # Rewrite the enriched markdown (frontmatter + edges) so
        # the on-disk file reflects the fused metadata.  Go through the
        # canonical writer so field order and trailer stay consistent.
        doc = _doc_from_dict(doc_data)
        write_doc_markdown(paths, doc, body)

        # Rebuild the topic-extraction keyword hint for this receipt
        # now that metadata carries the publisher-supplied keywords.
        kw = merged.get("keywords")
        if isinstance(kw, list):
            receipt.declared_keywords = kw

        n_fused += 1

    if n_fused:
        print(f"[ingest] fuse metadata: {n_fused} PDFs", file=sys.stderr)


# ---------------------------------------------------------------------------
# DAG declaration
# ---------------------------------------------------------------------------


INGEST_DAG: list[Wave] = [
    Wave(
        label="probe",
        steps=[Step("metadata_probe", _ingest_metadata_probe)],
    ),
    Wave(
        label="resolve+parse",
        steps=[
            Step("doi_resolve", _ingest_doi_resolve),
            Step("content_parse", _ingest_content_parse),
        ],
        kind="mixed",
    ),
    Wave(
        label="fuse",
        steps=[Step("fuse_metadata", _ingest_fuse_metadata)],
    ),
]


# ---------------------------------------------------------------------------
# Orchestration helper — called from pipeline.ingest_corpus
# ---------------------------------------------------------------------------


def run_ingest_dag(
    sources: list[Path],
    paths: Corpus,
    *,
    max_workers: int | None,
    parser_backend: str,
    timings: dict[str, float],
) -> list:
    """Build the shared ctx, run ``INGEST_DAG``, return the receipts.

    Thin wrapper so ``pipeline.ingest_corpus`` stays readable.  The
    ctx dict is the single source of truth across passes; every
    ``_ingest_*`` reads its inputs from ctx and publishes outputs back.
    """
    from .dag import run_dag

    ctx: dict = dict(
        sources_to_parse=sources,
        paths=paths,
        parser_backend=parser_backend,
        max_workers=max_workers,
    )
    run_dag(INGEST_DAG, ctx, timings=timings)
    return ctx.get("receipts") or []


