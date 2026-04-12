"""Tests for Phase 4: images as first-class sampling units."""

import random

import numpy as np
import pytest

from wikify_simple.distill.explorer import (
    _CAPTION_DEFAULT_RESIDUAL,
    _CAPTION_NEAR_FLOOR,
    GlobalOp,
    LevyExplorer,
    LocalOp,
    ExplorerState,
    apply_coverage_feedback,
    init_coverage_state,
    explore_global,
)
from wikify_simple.eval.metrics import image_coverage_residual
from wikify_simple.models import CorpusGraph
from wikify_simple.store.vectors import VectorStore


def _make_state(caption_ids: set[str], all_ids: list[str]) -> ExplorerState:
    doc_map = {cid: "d1" for cid in all_ids}
    state = ExplorerState(
        rng=random.Random(42),
        graph=CorpusGraph(nodes={}, edges={"similar_strong": [], "co_section": []}),
        vectors=VectorStore(
            ids=all_ids,
            matrix=np.eye(len(all_ids), dtype=np.float32),
        ),
        chunks_by_doc={"d1": all_ids},
        abstract_chunk_by_doc={"d1": all_ids[0]},
        pagerank_doc={"d1": 1.0},
        neighbors_by_chunk={},
        chunk_degree={cid: 0 for cid in all_ids},
        chunk_to_doc=doc_map,
        caption_chunk_ids=caption_ids,
    )
    init_coverage_state(state, all_ids)
    return state


class TestCaptionTagging:
    def test_caption_chunk_ids_populated(self):
        caps = {"cap1", "cap2"}
        all_ids = ["txt1", "txt2", "cap1", "cap2"]
        state = _make_state(caps, all_ids)
        assert state.caption_chunk_ids == caps

    def test_caption_residual_lower_than_text(self):
        caps = {"cap1"}
        all_ids = ["txt1", "cap1"]
        state = _make_state(caps, all_ids)
        assert state.coverage_residuals["cap1"] == pytest.approx(_CAPTION_DEFAULT_RESIDUAL)
        assert state.coverage_residuals["txt1"] == pytest.approx(1.0)

    def test_caption_heap_initialized(self):
        caps = {"cap1", "cap2"}
        all_ids = ["txt1", "cap1", "cap2"]
        state = _make_state(caps, all_ids)
        # caption heap should contain exactly the caption chunks
        cap_heap_ids = {cid for _, _, cid in state.caption_heap}
        assert cap_heap_ids == caps

    def test_caption_near_floor_applied_to_caption_neighbors(self):
        """Reading a text chunk should discount caption neighbors less than text neighbors."""
        caps = {"cap1"}
        all_ids = ["txt1", "txt2", "cap1"]
        # txt1 neighbors: txt2 (text) and cap1 (caption)
        state = ExplorerState(
            rng=random.Random(0),
            graph=CorpusGraph(nodes={}, edges={"similar_strong": [], "co_section": []}),
            vectors=VectorStore(ids=all_ids, matrix=np.eye(3, dtype=np.float32)),
            chunks_by_doc={"d1": all_ids},
            abstract_chunk_by_doc={"d1": "txt1"},
            pagerank_doc={"d1": 1.0},
            neighbors_by_chunk={"txt1": ("txt2", "cap1")},
            chunk_degree={cid: 0 for cid in all_ids},
            chunk_to_doc={cid: "d1" for cid in all_ids},
            caption_chunk_ids=caps,
        )
        init_coverage_state(state, all_ids)
        apply_coverage_feedback(state, "txt1", as_evidence=False)
        # txt2 (text neighbor) discounted to text near_floor (0.35 for as_evidence=False)
        assert state.coverage_residuals["txt2"] == pytest.approx(0.35)
        # cap1 (caption neighbor) discounted only to _CAPTION_NEAR_FLOOR (0.4)
        assert state.coverage_residuals["cap1"] == pytest.approx(_CAPTION_NEAR_FLOOR)

    def test_caption_near_floor_constant_value(self):
        assert _CAPTION_NEAR_FLOOR == pytest.approx(0.4)

    def test_caption_default_residual_constant_value(self):
        assert _CAPTION_DEFAULT_RESIDUAL == pytest.approx(0.8)


class TestJumpFigures:
    def test_jump_figures_returns_caption_chunk(self):
        caps = {"cap1", "cap2"}
        all_ids = ["txt1", "txt2", "cap1", "cap2"]
        state = _make_state(caps, all_ids)
        result = explore_global(state, GlobalOp.FIGURES)
        assert len(result) == 1
        assert result[0] in caps

    def test_jump_figures_skips_seen(self):
        caps = {"cap1", "cap2"}
        all_ids = ["txt1", "cap1", "cap2"]
        state = _make_state(caps, all_ids)
        state.seen_chunks.add("cap1")
        state.seen_chunks.add("cap2")
        result = explore_global(state, GlobalOp.FIGURES)
        assert result == []

    def test_jump_figures_returns_highest_residual_caption(self):
        caps = {"cap1", "cap2"}
        all_ids = ["txt1", "cap1", "cap2"]
        state = _make_state(caps, all_ids)
        # Manually set cap2 to higher residual
        from wikify_simple.distill.explorer import _set_residual

        _set_residual(state, "cap1", 0.3)
        _set_residual(state, "cap2", 0.9)
        result = explore_global(state, GlobalOp.FIGURES)
        assert result == ["cap2"]

    def test_jump_figures_empty_when_no_captions(self):
        all_ids = ["txt1", "txt2"]
        state = _make_state(set(), all_ids)
        result = explore_global(state, GlobalOp.FIGURES)
        assert result == []

    def test_levy_mix_sampler_figures_op(self):
        caps = {"cap1"}
        all_ids = ["txt1", "cap1"]
        state = _make_state(caps, all_ids)
        sampler = LevyExplorer(
            local_op=LocalOp.NONE, global_op=GlobalOp.FIGURES, jump_rate=1.0
        )
        batch = sampler.next_batch(state, 1)
        assert batch == ["cap1"]

    def test_jump_figures_via_policy(self):
        from wikify_simple.distill.strategy import GuidedMode, ModeContext, RuntimeOverrides
        from wikify_simple.distill.explorer import LevyExplorer

        caps = {"cap1", "cap2"}
        all_ids = ["txt1", "cap1", "cap2"]
        state = _make_state(caps, all_ids)

        class _FakeOrch:
            def step(self, s):
                from wikify_simple.schema import OrchAction

                return OrchAction(name="jump_figures", args={"k": 2})

        policy = GuidedMode(
            orchestrator=_FakeOrch(),
            fallback_explorer=LevyExplorer(
                local_op=LocalOp.NONE, global_op=GlobalOp.UNIFORM, jump_rate=1.0
            ),
            runtime=RuntimeOverrides(),
        )
        ctx = ModeContext(
            run_id="test",
            n_pages=0,
            n_candidates=0,
            n_concepts=0,
            n_people=0,
            docs_covered=0,
            docs_total=1,
        )
        decision = policy.next_extract(state, k=2, ctx=ctx)
        assert decision.action == "jump_figures"
        assert set(decision.batch).issubset(caps)


class TestImageCoverageResidual:
    def _embed(self, texts):
        """Deterministic fake embedder: embed by first char index."""
        dim = 4
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            v = np.ones(dim, dtype=np.float32) * (ord(t[0]) if t else 1.0)
            n = np.linalg.norm(v)
            out[i] = v / n if n > 0 else v
        return out

    def _make_bundle(self, body_texts: list[str]):
        from pathlib import Path

        from wikify_simple.eval.bundle import Bundle, Page

        pages = [
            Page(
                id=f"p{i}",
                kind="article",
                title=f"Page {i}",
                aliases=[],
                links=[],
                body_clean=body,
                evidence=[],
                path=Path(f"p{i}.md"),
            )
            for i, body in enumerate(body_texts)
        ]
        return Bundle(name="test", root=Path("."), pages=pages)

    def test_returns_float_in_unit_interval(self, monkeypatch):
        bundle = self._make_bundle(["alpha text", "beta text"])
        cap_texts = ["alpha caption", "beta figure"]
        cap_embeds = self._embed(cap_texts)

        def fake_load_or_compute(_bundle, pages, _embed):
            ids = [p.id for p in pages]
            return ids, self._embed([p.body_clean for p in pages])

        monkeypatch.setattr(
            "wikify_simple.store.bundle_embeddings.load_or_compute",
            fake_load_or_compute,
        )
        val = image_coverage_residual(bundle, cap_embeds, self._embed)
        assert isinstance(val, float)
        assert 0.0 <= val <= 1.0

    def test_returns_one_when_no_pages(self):
        from pathlib import Path

        from wikify_simple.eval.bundle import Bundle

        bundle = Bundle(name="test", root=Path("."), pages=[])
        cap_embeds = np.zeros((3, 4), dtype=np.float32)
        val = image_coverage_residual(bundle, cap_embeds, self._embed)
        assert val == 1.0

    def test_returns_one_when_no_captions(self, monkeypatch):
        bundle = self._make_bundle(["alpha text"])

        def fake_load_or_compute(_bundle, pages, _embed):
            return [p.id for p in pages], self._embed([p.body_clean for p in pages])

        monkeypatch.setattr(
            "wikify_simple.store.bundle_embeddings.load_or_compute",
            fake_load_or_compute,
        )
        cap_embeds = np.empty((0, 4), dtype=np.float32)
        val = image_coverage_residual(bundle, cap_embeds, self._embed)
        assert val == 1.0

    def test_non_negative(self, monkeypatch):
        """Regression: must not return negative from float underflow."""
        bundle = self._make_bundle(["alpha"])
        dim = 4
        vec = np.ones((1, dim), dtype=np.float32)
        vec /= np.linalg.norm(vec, axis=1, keepdims=True)

        def fake_load_or_compute(_bundle, pages, _embed):
            return [p.id for p in pages], vec

        monkeypatch.setattr(
            "wikify_simple.store.bundle_embeddings.load_or_compute",
            fake_load_or_compute,
        )
        val = image_coverage_residual(bundle, vec, lambda xs: vec)
        assert val >= 0.0
