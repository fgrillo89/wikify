"""Re-run ``assemble_pdf_metadata`` in place on a already-parsed corpus.

Use case: metadata-extraction logic changed (e.g. XMP wiring) but the
underlying markdown + chunks + embeddings are still valid. Full
re-ingest is unnecessary — we just need the new metadata dicts on
disk so ``refresh_corpus`` can regenerate bibs / KG from them.

For each document whose source is a PDF:
  1. Load the parsed markdown body from ``markdown/{doc_id}.md``
     (stripping YAML frontmatter + edges block).
  2. Call ``assemble_pdf_metadata(source_path, body)``.
  3. Overwrite ``Document.metadata`` and re-save the doc JSON.

Non-PDF sources (docx, pptx, html, md) are skipped — they use
parser-specific metadata paths ``assemble_pdf_metadata`` doesn't cover.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from wikify.ingest.metadata import assemble_pdf_metadata
from wikify.ingest.pipeline import _read_body_from_doc_markdown
from wikify.paths import CorpusPaths
from wikify.corpus.chunks import _doc_to_dict, atomic_write_text, list_documents


def reassemble(corpus_dir: Path) -> tuple[int, int, int]:
    paths = CorpusPaths(root=corpus_dir)
    docs = list_documents(paths)
    if not docs:
        print(f"no documents in {corpus_dir}")
        return 0, 0, 0

    n_updated = 0
    n_skipped = 0
    n_errors = 0

    for doc in docs:
        source_path = Path(doc.source_path)
        if source_path.suffix.lower() != ".pdf":
            n_skipped += 1
            continue
        if not source_path.exists():
            print(f"MISSING SOURCE: {source_path}")
            n_errors += 1
            continue

        md_path = paths.markdown_dir / f"{doc.id}.md"
        if not md_path.exists():
            print(f"MISSING MARKDOWN: {md_path}")
            n_errors += 1
            continue

        body = _read_body_from_doc_markdown(md_path)
        try:
            new_metadata = assemble_pdf_metadata(source_path, body)
        except Exception as exc:  # noqa: BLE001 — per-doc error, continue
            print(f"ERROR on {doc.id}: {exc!r}")
            n_errors += 1
            continue

        # Preserve ``_docling_chunks`` and any other parser-specific keys
        # not produced by ``assemble_pdf_metadata``. Merge new over old.
        merged = dict(doc.metadata or {})
        merged.update(new_metadata)
        doc.metadata = merged
        # Keep Document.title synced with the metadata title the
        # assembler chose — the ingest path does this in write_document.
        if merged.get("title"):
            doc.title = merged["title"]

        atomic_write_text(
            paths.docs_dir / f"{doc.id}.json",
            json.dumps(_doc_to_dict(doc)),
        )
        n_updated += 1

    return n_updated, n_skipped, n_errors


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: reassemble_metadata.py <corpus_dir>")
        return 1
    corpus_dir = Path(sys.argv[1])
    if not corpus_dir.exists():
        print(f"corpus_dir does not exist: {corpus_dir}")
        return 1
    n_updated, n_skipped, n_errors = reassemble(corpus_dir)
    print(
        f"\nreassembly complete: {n_updated} updated, "
        f"{n_skipped} skipped (non-pdf), {n_errors} errors"
    )
    return 0 if n_errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
