"""Integration test for the four-phase ingest DAG.

Exercises ``ingest_corpus`` end-to-end against the tiny PDF fixture and
checks that:

- the ingest DAG shape matches the three documented waves
  (``probe`` -> ``resolve+parse`` mixed -> ``fuse``);
- the timing report records every ingest wave label;
- ``doi_resolve`` and ``content_parse`` are the two steps under the
  ``resolve+parse`` wave;
- the final on-disk metadata matches what
  ``assemble_pdf_metadata`` produces against the persisted markdown
  (the contract between pass 3 content parse and pass 4 metadata
  fusion).

The DOI resolver is stubbed so the test is network-free but still
drives the real mixed-wave plumbing (async step + process-pool step
gathered on one event loop).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.ingest.ingest_steps import INGEST_DAG
from wikify.ingest.metadata import assemble_pdf_metadata
from wikify.ingest.pipeline import _read_body_from_doc_markdown, ingest_corpus
from wikify.store.corpus import list_documents

FIXTURE_PDF = (
    Path(__file__).resolve().parents[1] / "fixtures" / "tiny" / "sample.pdf"
)


def _stub_resolve_many(dois, *, cache_path, **_kwargs):
    # Return empty records for any DOI the probe finds.  We don't care
    # about CrossRef content here — only that the resolver is wired
    # into the mixed wave and produces ctx["resolved_metadata"].
    return {d.lower(): {} for d in dois if d}


@pytest.fixture(autouse=True)
def _patch_doi_resolver(monkeypatch):
    monkeypatch.setattr(
        "wikify.util.doi_resolver.resolve_many", _stub_resolve_many,
    )


def test_ingest_dag_end_to_end(tmp_path, monkeypatch):
    if not FIXTURE_PDF.exists():
        pytest.skip(f"fixture PDF missing: {FIXTURE_PDF}")

    # --- Static DAG shape -----------------------------------------------
    assert [w.label for w in INGEST_DAG] == ["probe", "resolve+parse", "fuse"]
    assert [w.kind for w in INGEST_DAG] == ["threads", "mixed", "threads"]

    mixed_wave = next(w for w in INGEST_DAG if w.label == "resolve+parse")
    mixed_step_names = [s.name for s in mixed_wave.steps]
    assert mixed_step_names == ["doi_resolve", "content_parse"]

    # --- Capture timings so we can assert waves actually ran -----------
    captured: list[dict] = []
    from wikify.ingest import pipeline as pipeline_mod

    original_print = pipeline_mod._print_timings

    def _capture(timings, t0):
        captured.append(dict(timings))
        return original_print(timings, t0)

    monkeypatch.setattr(pipeline_mod, "_print_timings", _capture)

    # --- Set up a tiny corpus around the fixture PDF -------------------
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    pdf_copy = sources_dir / "sample.pdf"
    pdf_copy.write_bytes(FIXTURE_PDF.read_bytes())
    corpus_dir = tmp_path / "corpus"

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # --- Ingest DAG produced exactly one doc ---------------------------
    docs = list_documents(paths)
    assert len(docs) == 1
    doc = docs[0]

    # --- Timing report carries every ingest wave label -----------------
    # ingest_corpus prints twice (refresh block + outer ingest block);
    # merge both so we can assert in one pass.
    merged: dict[str, float] = {}
    for block in captured:
        merged.update(block)
    for label in ("probe", "resolve+parse", "fuse"):
        assert label in merged, (
            f"wave {label!r} missing; recorded: {sorted(merged)}"
        )
    # Refresh DAG still ran behind the ingest DAG.
    assert any(k.startswith("wave ") for k in merged)

    # --- Final metadata matches assemble_pdf_metadata on the persisted
    # markdown.  This is the guarantee that pass 4 didn't skip any PDF
    # and that the pass-3/pass-4 split is semantically equivalent to
    # the legacy single-shot call.
    md_path = paths.markdown_dir / f"{doc.id}.md"
    assert md_path.exists()
    body = _read_body_from_doc_markdown(md_path)
    expected = assemble_pdf_metadata(pdf_copy, body)
    for key in ("title", "authors", "year", "summary"):
        assert doc.metadata.get(key) == expected.get(key), (
            f"metadata mismatch on {key!r}: "
            f"on-disk={doc.metadata.get(key)!r} "
            f"expected={expected.get(key)!r}"
        )
