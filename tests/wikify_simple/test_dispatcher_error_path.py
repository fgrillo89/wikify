"""Dispatcher error-path tests.

Covers STEP 2 of the slice 6 structural rework:

- a schema-invalid response raises ``ValidationError``
- a ``.error.json`` artifact is written next to the request
- the request file is preserved for operator inspection
- the response file is still cleaned up (no stale garbage)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from wikify_simple.agents.schema import ExtractRequest
from wikify_simple.bindings.claude_code import ClaudeCodeExtractor
from wikify_simple.infra.cache import ExtractCache
from wikify_simple.infra.cost_meter import CostMeter


class _InvalidResponder:
    """Writes a deliberately-malformed extract response."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.root.exists():
                for role_dir in self.root.iterdir():
                    if not role_dir.is_dir():
                        continue
                    for req in role_dir.glob("*.request.json"):
                        res = req.with_name(req.name.replace(".request.", ".response."))
                        if res.exists():
                            continue
                        # Missing required ``chunk_id`` and ``concepts``:
                        # this is guaranteed to fail schema validation.
                        res.write_text(
                            json.dumps({"tokens_in": 0, "tokens_out": 0}),
                            encoding="utf-8",
                        )
            time.sleep(0.02)


def test_invalid_response_writes_error_artifact(tmp_path):
    dispatch_root = tmp_path / "dispatch"
    dispatch_root.mkdir()
    responder = _InvalidResponder(dispatch_root)
    responder.start()
    try:
        meter = CostMeter(
            budget_haiku_eq=1_000_000.0,
            run_id="err-path",
            events_path=tmp_path / "calls.jsonl",
        )
        cache = ExtractCache(root=tmp_path / "cache")
        extractor = ClaudeCodeExtractor(cache, meter, dispatch_dir=dispatch_root)
        req = ExtractRequest(
            chunk_id="chunk-err",
            chunk_text="any text will do here because validation will fail first.",
            canonical_titles=[],
            prompt_template="wikify_simple/extract/v1",
            model_id="claude-haiku",
            tier="S",
        )
        with pytest.raises(ValidationError):
            extractor.extract(req)
    finally:
        responder.stop()

    errors = list((dispatch_root / "extract").glob("*.error.json"))
    assert errors, "validation failure should write a .error.json artifact"
    payload = json.loads(errors[0].read_text(encoding="utf-8"))
    assert payload["schema"] == "ExtractResponse"
    assert "error" in payload

    requests = list((dispatch_root / "extract").glob("*.request.json"))
    assert requests, "request file must be preserved on validation failure"

    responses = list((dispatch_root / "extract").glob("*.response.json"))
    assert responses == [], "response file must still be cleaned up"
