"""Unit tests for ``ExtractedConcept`` structural validators.

These cover STEP 1 of the slice 6 structural rework:

- ``kind`` stays the two-value page-type discriminator
- ``category`` is an optional facet and is rejected for ``kind="person"``
- title/alias/quote hygiene rules reject garbage input early
- duplicate / empty / title-equal aliases are deduped
- quote and title whitespace is normalized
- the quote-substring rule is enforced by the BINDING wrapper, not the
  schema (schemas don't see ``chunk_text``)
"""

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from wikify_simple.dispatch import Dispatch
from wikify_simple.schema import (
    ExtractedConcept,
    ExtractRequest,
    QuoteNotInChunkError,
)
from wikify_simple.cache import ExtractCache
from wikify_simple.meter import CostMeter

# --- schema-level tests --------------------------------------------------


def test_concept_with_category_accepted():
    c = ExtractedConcept(
        title="Atomic Layer Deposition",
        aliases=["ALD"],
        kind="article",
        quote="ALD is a self-limiting process.",
        category="method",
    )
    assert c.category == "method"
    assert c.kind == "article"


def test_person_without_category_accepted():
    c = ExtractedConcept(
        title="Chua",
        aliases=[],
        kind="person",
        quote="Chua introduced the memristor concept.",
    )
    assert c.category is None
    assert c.kind == "person"


def test_person_with_category_rejected():
    with pytest.raises(ValidationError) as excinfo:
        ExtractedConcept(
            title="Chua",
            aliases=[],
            kind="person",
            quote="Chua introduced the memristor concept.",
            category="theory",
        )
    assert "person" in str(excinfo.value)


def test_title_too_short_rejected():
    with pytest.raises(ValidationError):
        ExtractedConcept(
            title="A",
            aliases=[],
            kind="article",
            quote="too short title case.",
        )


def test_title_stopword_rejected():
    with pytest.raises(ValidationError):
        ExtractedConcept(
            title="the",
            aliases=[],
            kind="article",
            quote="stopword title should be rejected.",
        )


def test_title_trailing_punctuation_rejected():
    with pytest.raises(ValidationError):
        ExtractedConcept(
            title="memristor,",
            aliases=[],
            kind="article",
            quote="Trailing punctuation should trip the hygiene rule.",
        )


def test_title_whitespace_stripped():
    c = ExtractedConcept(
        title="  memristor  ",
        aliases=[],
        kind="article",
        quote="Memristor is a two-terminal device.",
    )
    assert c.title == "memristor"


def test_aliases_dedupe_case_insensitive():
    c = ExtractedConcept(
        title="Atomic Layer Deposition",
        aliases=["ALD", "ald", "ALD ", "A.L.D.", "ald"],
        kind="article",
        quote="ALD is a self-limiting process.",
    )
    # 'ALD' and 'ald' and 'ALD ' collapse to one; 'A.L.D.' is a distinct key
    lowered = [a.lower() for a in c.aliases]
    assert lowered.count("ald") == 1
    assert "a.l.d." in lowered


def test_aliases_drops_entries_equal_to_title():
    c = ExtractedConcept(
        title="Memristor",
        aliases=["memristor", "MEMRISTOR", "ReRAM"],
        kind="article",
        quote="Memristor is a two-terminal device.",
    )
    assert [a.lower() for a in c.aliases] == ["reram"]


def test_aliases_capped_at_eight():
    c = ExtractedConcept(
        title="HfO2",
        aliases=[f"alias{i}" for i in range(20)],
        kind="article",
        quote="HfO2 is a common high-k dielectric.",
    )
    assert len(c.aliases) == 8


def test_quote_stripped_and_length_enforced():
    c = ExtractedConcept(
        title="HfO2",
        aliases=[],
        kind="article",
        quote="   HfO2 layers.   ",
    )
    assert c.quote == "HfO2 layers."


def test_quote_too_short_rejected():
    with pytest.raises(ValidationError):
        ExtractedConcept(
            title="HfO2",
            aliases=[],
            kind="article",
            quote="   x   ",
        )


# --- binding-level quote-substring enforcement ---------------------------


class _SingleResponder:
    """Minimal dispatcher stand-in: writes ONE response and stops."""

    def __init__(self, root: Path, response: dict) -> None:
        self.root = root
        self._response = response
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
                        payload = dict(self._response)
                        try:
                            req_payload = json.loads(req.read_text(encoding="utf-8"))
                            payload["chunk_id"] = req_payload["chunk_id"]
                        except (FileNotFoundError, json.JSONDecodeError):
                            continue
                        res.write_text(json.dumps(payload), encoding="utf-8")
            time.sleep(0.02)


def _make_extractor(tmp_path: Path, dispatch_root: Path) -> Dispatch:
    meter = CostMeter(
        budget_haiku_eq=1_000_000.0,
        run_id="concept-test",
        events_path=tmp_path / "calls.jsonl",
    )
    cache = ExtractCache(root=tmp_path / "cache")
    return Dispatch(meter, cache, dispatch_dir=dispatch_root)


def test_quote_not_in_chunk_rejected_by_binding(tmp_path):
    """Hallucinated paraphrased quote must be rejected by the binding
    wrapper, NOT by the schema, and must leave a ``.error.json`` artifact.
    """
    dispatch_root = tmp_path / "dispatch"
    dispatch_root.mkdir()
    # Schema-valid response whose quote is NOT in the request chunk_text.
    bad_response = {
        "concepts": [
            {
                "title": "Atomic Layer Deposition",
                "aliases": ["ALD"],
                "kind": "article",
                "quote": "A paraphrase that never appeared in the source chunk verbatim.",
                "category": "method",
                "evidence_figures": [],
            }
        ],
        "tokens_in": 10,
        "tokens_out": 10,
    }
    responder = _SingleResponder(dispatch_root, bad_response)
    responder.start()
    try:
        extractor = _make_extractor(tmp_path, dispatch_root)
        req = ExtractRequest(
            chunk_id="chunk-halluc",
            chunk_text="Atomic layer deposition is a self-limiting process.",
            canonical_titles=[],
            prompt_template="wikify_simple/extract",
            model_id="claude-haiku",
            tier="S",
        )
        with pytest.raises(QuoteNotInChunkError):
            extractor.extract(req)
    finally:
        responder.stop()

    # .error.json artifact present; request file preserved.
    errors = list((dispatch_root / "extract").glob("*.error.json"))
    assert errors, "binding should have written a .error.json for the hallucination"
    err_payload = json.loads(errors[0].read_text(encoding="utf-8"))
    assert err_payload["error_type"] == "QuoteNotInChunkError"
    requests = list((dispatch_root / "extract").glob("*.request.json"))
    assert requests, "request file must be preserved on rejection"
    responses = list((dispatch_root / "extract").glob("*.response.json"))
    assert responses == [], "response file must be cleaned up even on failure"


def test_noisy_quote_accepted_by_binding(tmp_path):
    """A quote the model legitimately cleaned (stripped [12] citation
    marker, normalised em-dash, collapsed double spaces) must still be
    accepted by ``_assert_quotes_in_chunk`` via the tolerant normalizer.
    """
    dispatch_root = tmp_path / "dispatch"
    dispatch_root.mkdir()
    good_response = {
        "concepts": [
            {
                "title": "Memristor",
                "aliases": [],
                "kind": "article",
                # citation stripped, em-dash -> hyphen, double space collapsed
                "quote": "the memristor was first described by chua - a theoretical device",
                "category": "device",
                "evidence_figures": [],
            }
        ],
        "tokens_in": 10,
        "tokens_out": 10,
    }
    responder = _SingleResponder(dispatch_root, good_response)
    responder.start()
    try:
        extractor = _make_extractor(tmp_path, dispatch_root)
        req = ExtractRequest(
            chunk_id="chunk-noisy",
            # raw chunk has the [12] marker, em-dash, and a double space
            chunk_text=(
                "The memristor [12] was first described  by Chua\u2014a theoretical device."
            ),
            canonical_titles=[],
            prompt_template="wikify_simple/extract",
            model_id="claude-haiku",
            tier="S",
        )
        # Must NOT raise QuoteNotInChunkError.
        resp = extractor.extract(req)
        assert resp.concepts[0].title.lower() == "memristor"
    finally:
        responder.stop()
