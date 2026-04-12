"""Tests for FileDispatchExtractor.extract_many (Phase 5A).

Verifies:
- 4-request batch returns all 4 responses in input order.
- Cache-hit short-circuit: pre-populate 2 of 4 chunks; assert no request
  files are written for those 2.
- Single extract(request) wrapper still works via extract_many([request])[0].
"""

import json
import threading
import time
from pathlib import Path

from wikify.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from wikify.dispatch import Dispatch
from wikify.meter import CostMeter
from wikify.schema import ExtractRequest, ExtractResponse

# --- fake dispatcher thread ----------------------------------------------


class _FakeDispatcher:
    """Watches ``<root>/extract/*.request.json`` and writes responses."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._stop = threading.Event()
        self._seen: set[Path] = set()
        self._written: list[str] = []  # chunk_ids written as request files
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    @property
    def written_chunk_ids(self) -> list[str]:
        with self._lock:
            return list(self._written)

    def _run(self) -> None:
        while not self._stop.is_set():
            role_dir = self.root / "extract"
            if role_dir.exists():
                for req_path in role_dir.glob("*.request.json"):
                    if req_path in self._seen:
                        continue
                    try:
                        payload = json.loads(req_path.read_text(encoding="utf-8"))
                    except (FileNotFoundError, json.JSONDecodeError):
                        continue
                    self._seen.add(req_path)
                    with self._lock:
                        self._written.append(payload["chunk_id"])
                    chunk_text = payload["chunk_text"]
                    quote = chunk_text[: max(5, min(80, len(chunk_text)))]
                    response = {
                        "chunk_id": payload["chunk_id"],
                        "concepts": [
                            {
                                "title": f"Concept-{payload['chunk_id']}",
                                "aliases": [],
                                "kind": "article",
                                "quote": quote,
                                "evidence_figures": [],
                            }
                        ],
                        "tokens_in": 10,
                        "tokens_out": 5,
                    }
                    res_path = req_path.with_name(req_path.name.replace(".request.", ".response."))
                    res_path.write_text(json.dumps(response), encoding="utf-8")
            time.sleep(0.01)


def _make_req(chunk_id: str) -> ExtractRequest:
    return ExtractRequest(
        chunk_id=chunk_id,
        chunk_text=f"Text about {chunk_id} which is a self-limiting process used here.",
        canonical_titles=[],
        prompt_template="wikify/extract",
        model_id="claude-haiku",
        tier="S",
    )


def _meter(tmp_path: Path) -> CostMeter:
    return CostMeter(
        budget_haiku_eq=1_000_000.0,
        run_id="test-extract-many",
        events_path=tmp_path / "calls.jsonl",
    )


# --- tests ---------------------------------------------------------------


def test_extract_many_batch_order(tmp_path: Path) -> None:
    """4-request batch returns all 4 responses in input order."""
    dispatch_root = tmp_path / "dispatch"
    dispatch_root.mkdir()
    dispatcher = _FakeDispatcher(dispatch_root)
    dispatcher.start()
    try:
        meter = _meter(tmp_path)
        cache = ExtractCache(root=tmp_path / "cache")
        extractor = Dispatch(meter, cache, dispatch_dir=dispatch_root)

        chunk_ids = ["c1", "c2", "c3", "c4"]
        reqs = [_make_req(cid) for cid in chunk_ids]
        responses = extractor.extract_many(reqs)

        assert len(responses) == 4
        for i, (cid, resp) in enumerate(zip(chunk_ids, responses)):
            assert isinstance(resp, ExtractResponse), f"slot {i} not ExtractResponse"
            assert resp.chunk_id == cid, f"slot {i}: expected chunk_id={cid}, got {resp.chunk_id}"
            assert len(resp.concepts) == 1
            assert resp.concepts[0].title == f"Concept-{cid}"
    finally:
        dispatcher.stop()


def test_extract_many_cache_hit_skips_dispatch(tmp_path: Path) -> None:
    """Pre-populate 2 of 4 chunks in cache; assert no request files written for them."""
    dispatch_root = tmp_path / "dispatch"
    dispatch_root.mkdir()
    dispatcher = _FakeDispatcher(dispatch_root)
    dispatcher.start()
    try:
        meter = _meter(tmp_path)
        cache = ExtractCache(root=tmp_path / "cache")
        extractor = Dispatch(meter, cache, dispatch_dir=dispatch_root)

        # Pre-populate chunks c1 and c3 in the cache directly.
        cached_ids = {"c1", "c3"}
        for cid in cached_ids:
            req = _make_req(cid)
            key = ExtractCacheKey(
                binding_name="file_dispatch",
                model_id=req.model_id,
                prompt_hash=prompt_hash(req.prompt_template),
                chunk_id=cid,
            )
            cached_entry = CachedExtract(
                payload={
                    "chunk_id": cid,
                    "concepts": [
                        {
                            "title": f"Cached-{cid}",
                            "aliases": [],
                            "kind": "article",
                            "quote": f"Text about {cid}",
                            "category": None,
                        }
                    ],
                },
                tokens_in=1,
                tokens_out=1,
            )
            # Write into cache by using get_or_extract with a compute that returns
            # the pre-built entry.
            cache.get_or_extract(key, lambda e=cached_entry: e)

        reqs = [_make_req(cid) for cid in ["c1", "c2", "c3", "c4"]]
        responses = extractor.extract_many(reqs)

        assert len(responses) == 4

        # Cache-hit slots should return the pre-populated titles.
        assert responses[0].concepts[0].title == "Cached-c1"
        assert responses[2].concepts[0].title == "Cached-c3"

        # Dispatcher should only have received requests for c2 and c4.
        written = set(dispatcher.written_chunk_ids)
        assert written == {"c2", "c4"}, f"expected {{c2, c4}}, got {written}"
    finally:
        dispatcher.stop()


def test_extract_single_wrapper(tmp_path: Path) -> None:
    """Single extract(request) still works as a thin wrapper over extract_many."""
    dispatch_root = tmp_path / "dispatch"
    dispatch_root.mkdir()
    dispatcher = _FakeDispatcher(dispatch_root)
    dispatcher.start()
    try:
        meter = _meter(tmp_path)
        cache = ExtractCache(root=tmp_path / "cache")
        extractor = Dispatch(meter, cache, dispatch_dir=dispatch_root)

        req = _make_req("solo")
        resp = extractor.extract(req)

        assert isinstance(resp, ExtractResponse)
        assert resp.chunk_id == "solo"
        assert resp.concepts[0].title == "Concept-solo"
        assert meter.spent_haiku_eq > 0
    finally:
        dispatcher.stop()
