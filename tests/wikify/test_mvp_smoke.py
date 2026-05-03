"""MVP smoke test — the CLI completes a baseline-shaped wiki lifecycle.

Hits every top-level noun in order on a hand-built fixture corpus,
without ever calling an LLM:

    wikify run init   --bundle <b> --corpus <c> --strategy baseline
    wikify work add concept "<title>" ...
    wikify work add evidence <slug> --records <jsonl>
    wikify draft build  <slug> --task create --corpus <c> --model-id ... --tier ...
    [synthetic response.json injected at draft.response_path]
    wikify draft check  <slug>
    wikify wiki commit  <slug>
    wikify render --bundle <b>
    wikify eval   --bundle <b>

Notes:

- The ``corpus sample`` step is replaced by a hard-coded concept title
  because the diverse-sample selector requires a fully embedded corpus
  with graph metrics, which is out of scope for a deterministic, no-LLM
  smoke test. The check is documented in this docstring rather than
  skipped silently.
- ``draft build`` is a deterministic Python step that just compiles a
  ``WriteRequest`` from on-disk evidence; no model is called. The smoke
  test then writes the synthetic ``response.json`` directly. ``draft
  check`` runs the full validator (quote-in-chunk + Wikipedia
  structure) so the lifecycle exercises the gate even without an LLM.
- The lifecycle is fast (well under a second locally) so no ``slow``
  marker — keeping it in the default suite means a regression in any
  CLI noun trips this test on the first run.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.wikify.test_corpus_queries import _make_corpus
from wikify.api import Bundle
from wikify.bundle.draft.artifact import response_path
from wikify.cli import app

runner = CliRunner()

PAGE_TITLE = "Atomic Layer Deposition"
PAGE_SLUG = "atomic-layer-deposition"


def _good_response_payload(chunk_quote: str) -> dict:
    """Mirror the synthetic response from ``test_wiki_commit._good_response_payload``.

    Must satisfy ``WriteResponse`` schema + Wikipedia-structure guards
    + quote-in-chunk gate.
    """
    body = (
        "## Lead\n\n"
        "Atomic Layer Deposition is a vapor-phase thin-film growth technique "
        "characterised by sequential self-limiting surface reactions between "
        "alternating precursor pulses [^e1]. The technique produces conformal "
        "coatings with sub-nanometre thickness control over arbitrarily complex "
        "three-dimensional substrates, which is why it is now central to gate-"
        "stack engineering, memristor fabrication, and area-selective patterning "
        "in advanced semiconductor nodes [^e1].\n\n"
        "## Mechanism\n\n"
        f"The standard ALD cycle exposes the substrate to two precursors in "
        f"separation, each pulse separated by an inert-gas purge that removes "
        f"unreacted molecules and gaseous byproducts [^e1]. {chunk_quote} The "
        f"self-limiting chemistry is what distinguishes ALD from CVD [^e1].\n\n"
        "## Applications\n\n"
        "ALD coats high-aspect-ratio trench structures uniformly because the "
        "vapor-phase precursors reach every surface site [^e1]. The dominant "
        "industrial applications are high-k gate dielectrics, atomic-layer etching, "
        "diffusion barriers in interconnect stacks, and resistive switching layers "
        "in memristive memory cells [^e1]. Area-selective ALD has emerged as a "
        "self-aligned alternative to lithographic patterning [^e1].\n\n"
        "## References\n\n"
        f'[^e1]: paper_0__c0000 (paper_0) > "{chunk_quote}"\n'
    )
    return {
        "schema_version": 1,
        "page_id": PAGE_TITLE,
        "page_kind": "article",
        "body_markdown": body,
        "used_markers": ["e1"],
        "tokens_in": 1000,
        "tokens_out": 200,
    }


def _invoke(*argv: str) -> None:
    """Run a CLI command and assert success."""
    result = runner.invoke(app, list(argv))
    assert result.exit_code == 0, (
        f"CLI {' '.join(argv)} failed with exit_code={result.exit_code}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {getattr(result, 'stderr', '')}"
    )


def test_baseline_lifecycle_runs_end_to_end(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    corpus = _make_corpus(tmp_path / "corpus")

    # 1. run init
    _invoke(
        "run", "init",
        "--bundle", str(bundle_root),
        "--corpus", str(corpus.root),
        "--strategy", "baseline",
    )
    bundle = Bundle.open(bundle_root)
    assert bundle.state_path.is_file()
    assert bundle.events_path.is_file()

    # 2. work add concept (hard-coded title — see module docstring on why
    # we skip ``corpus sample``).
    _invoke(
        "work", "add", "concept", PAGE_TITLE,
        "--run", str(bundle_root),
        "--aliases", '["ALD"]',
    )

    # 3. work add evidence — point at a real chunk in the fixture corpus.
    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        json.dumps({"chunk_id": "paper_0__c0000", "doc_id": "paper_0"}) + "\n",
        encoding="utf-8",
    )
    _invoke(
        "work", "add", "evidence", PAGE_SLUG,
        "--records", str(records_path),
        "--run", str(bundle_root),
    )

    # 4. draft build — deterministic Python; no LLM.
    _invoke(
        "draft", "build", PAGE_SLUG,
        "--task", "create",
        "--corpus", str(corpus.root),
        "--model-id", "claude-sonnet-4-6",
        "--tier", "M",
        "--run", str(bundle_root),
    )
    draft_json_path = bundle.work_concept_dir(PAGE_SLUG) / "draft.json"
    assert draft_json_path.is_file()

    # 5. Inject a synthetic response — what an LLM would write, but we
    # craft it directly so the test stays hermetic.
    draft_payload = json.loads(draft_json_path.read_text(encoding="utf-8"))
    chunk_text = draft_payload["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response_payload = _good_response_payload(quote)
    response_p = response_path(bundle, PAGE_SLUG)
    response_p.write_text(json.dumps(response_payload), encoding="utf-8")

    # 6. draft check — full validation gate.
    _invoke(
        "draft", "check", PAGE_SLUG,
        "--run", str(bundle_root),
    )
    verdict = json.loads(
        (bundle.work_concept_dir(PAGE_SLUG) / "validation.json").read_text(
            encoding="utf-8"
        )
    )
    assert verdict["ok"] is True

    # 7. wiki commit — promotes the response into wiki/articles/<slug>.md.
    _invoke(
        "wiki", "commit", PAGE_SLUG,
        "--run", str(bundle_root),
    )
    committed = bundle.wiki_articles_dir / f"{PAGE_SLUG}.md"
    assert committed.is_file()

    # 8. render — must produce site files under derived/site.
    _invoke("render", "--bundle", str(bundle_root))
    site_index = bundle.derived_dir / "site" / "index.html"
    assert site_index.is_file()

    # 9. eval — must write derived/eval.json with the corpus-free subset.
    _invoke("eval", "--bundle", str(bundle_root))
    eval_path = bundle.derived_dir / "eval.json"
    assert eval_path.is_file()
    payload = json.loads(eval_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["n_articles"] == 1
    # Without --corpus the corpus-dependent metrics are explicit nulls.
    assert payload["M1_coverage_residual"] is None
    assert payload["M6_grounding"] is None
    assert payload["corpus_dependent_unavailable"] == ["M1", "M6"]
