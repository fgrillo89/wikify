"""Compute slice-6 eval metrics on the mvp20 wiki bundle."""
from __future__ import annotations

import json
from pathlib import Path

from wikify_simple.eval import metrics
from wikify_simple.eval.bundle import load_bundle
from wikify_simple.infra.embedding import embed_texts
from wikify_simple.paths import CorpusPaths
from wikify_simple.store.corpus import all_chunks, read_vector_store

CORPUS = Path("data/wikify_simple/corpora/mvp20")
BUNDLE = Path("data/wikify_simple/wikis/mvp20_M/M_1x_seed0_20260408T075209")


def main() -> None:
    corpus = CorpusPaths(CORPUS)
    wb = load_bundle(BUNDLE)
    print("PAGES", len(wb.pages))
    chunks = all_chunks(corpus)
    print("CHUNKS", len(chunks))
    vs = read_vector_store(corpus)
    print("VECS", vs.matrix.shape)

    for name, fn in [
        ("M1_coverage_residual", lambda: metrics.coverage_residual(wb, vs.matrix, embed_texts)),
        ("M3_g_evidence", lambda: metrics.spectral_gap_modularity(wb)),
        ("M3_g_links", lambda: metrics.g_links_modularity(wb)),
    ]:
        try:
            print(name, fn())
        except Exception as e:  # noqa: BLE001
            print(name, "FAIL", type(e).__name__, str(e)[:160])

    try:
        run = json.loads((BUNDLE / "_run.json").read_text(encoding="utf-8"))
        print("M5_hit_rate", metrics.hit_rate(wb, run.get("chunks_read", [])))
    except Exception as e:  # noqa: BLE001
        print("M5_hit_rate FAIL", type(e).__name__, str(e)[:160])

    try:
        cbid = {c.id: c for c in chunks}
        print("M6_grounding", metrics.grounding(wb, lambda cid: cbid[cid].text if cid in cbid else ""))
    except Exception as e:  # noqa: BLE001
        print("M6_grounding FAIL", type(e).__name__, str(e)[:160])


if __name__ == "__main__":
    main()
