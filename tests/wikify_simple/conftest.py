"""Test-suite fixtures for ``wikify_simple``.

Pins the embedder backend to ``hash`` for the duration of every test
that doesn't explicitly override it. This preserves the historical
test behaviour after the production default flipped from ``hash`` →
``fastembed``: a real semantic embedder produces different similarity
graphs → different sampler decisions → different golden assertions in
tests like ``test_iteration_history`` that depend on a stable sampling
order.

Tests that genuinely need a real embedder must override
``WIKIFY_SIMPLE_EMBEDDER`` themselves (e.g. via ``monkeypatch.setenv``)
inside the test body.
"""

import pytest


@pytest.fixture(autouse=True)
def _pin_embedder_to_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIFY_SIMPLE_EMBEDDER", "hash")
