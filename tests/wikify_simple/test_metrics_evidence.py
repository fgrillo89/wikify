"""Pin regression: g_evidence produces edges when pages share doc_ids.

Previously the evidence-line regex rejected chunk_ids/doc_ids containing
spaces, so ``_build_g_evidence`` saw zero overlap and M3 collapsed to 0
even when the writer produced valid evidence blocks.
"""

from wikify_simple.eval.bundle import load_bundle
from wikify_simple.eval.metrics import spectral_gap_modularity


def _write_page(d, pid, evidence_lines):
    (d / f"{pid}.md").write_text(
        f"---\nid: {pid}\nkind: concept\ntitle: {pid}\n---\n\n"
        f"# {pid}\n\nBody [^e1].\n\n## Evidence\n\n" + "\n".join(evidence_lines) + "\n",
        encoding="utf-8",
    )


def test_g_evidence_edges_with_overlapping_doc_ids(tmp_path):
    concepts = tmp_path / "concepts"
    concepts.mkdir(parents=True)
    # Three pages; A and B share DOC_X, B and C share DOC_Y.
    # Chunk/doc ids deliberately contain spaces and brackets to mimic real
    # writer output.
    _write_page(
        concepts,
        "concept-a",
        ['[^e1]: [2018 Foo] Paper_abc__c001 ([2018 Foo] Paper_abc) > "q1"'],
    )
    _write_page(
        concepts,
        "concept-b",
        [
            '[^e1]: [2018 Foo] Paper_abc__c002 ([2018 Foo] Paper_abc) > "q2"',
            '[^e2]: [2019 Bar] Paper_def__c003 ([2019 Bar] Paper_def) > "q3"',
        ],
    )
    _write_page(
        concepts,
        "concept-c",
        ['[^e1]: [2019 Bar] Paper_def__c004 ([2019 Bar] Paper_def) > "q4"'],
    )
    bundle = load_bundle(tmp_path)
    assert len(bundle.pages) == 3
    # Every page has parsed evidence.
    for p in bundle.pages:
        assert p.evidence, f"{p.id} lost evidence"

    out = spectral_gap_modularity(bundle)
    assert out["n_nodes"] == 3.0
    assert out["n_edges"] > 0, f"expected edges, got {out}"
