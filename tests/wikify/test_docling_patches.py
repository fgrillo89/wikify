"""Tests for ``wikify.ingest.parsers._docling_patches``.

The patches target the installed ``docling`` package; these tests
exercise the patch surface against a stand-in module so they run
GPU-free and Docling-version-agnostic on CI.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from wikify.ingest.parsers import _docling_patches
from wikify.ingest.parsers._docling_patches import (
    _LEAK_LOC_RE,
    _MAX_NEW_TOKENS,
    _NO_REPEAT_NGRAM_SIZE,
    _REPETITION_PENALTY,
    _STOP_STRINGS,
    _build_patched_call,
    _patched_post_process,
    apply_formula_extraction_patches,
)

# ---------------------------------------------------------------------------
# _patched_post_process
# ---------------------------------------------------------------------------


def test_post_process_strips_opening_formula_token() -> None:
    """The upstream strip removes only ``</formula>``; the patch adds
    the ``<formula>`` opener so a leaked wrapper is fully cleaned."""
    out = _patched_post_process(
        SimpleNamespace(),
        ["<formula>x = 1</formula>"],
    )
    assert out == ["x = 1"]


def test_post_process_strips_arbitrary_loc_tokens() -> None:
    """Upstream only removes the literal standard bbox; the patch
    handles any ``<loc_NNN>`` index emitted on a faulty decode."""
    out = _patched_post_process(
        SimpleNamespace(),
        ["<formula><loc_247><loc_0><loc_500><loc_499>x = 1</formula>"],
    )
    assert out == ["x = 1"]


def test_post_process_preserves_clean_latex() -> None:
    """A clean decode must round-trip unchanged (modulo upstream's
    own ``lstrip``)."""
    clean = r"\nabla \times E = -\partial_t B"
    out = _patched_post_process(SimpleNamespace(), [clean])
    assert out == [clean]


def test_post_process_truncates_at_end_of_utterance() -> None:
    """Behaviour preserved from upstream: text after the EOS marker
    is dropped before any other strip runs."""
    out = _patched_post_process(
        SimpleNamespace(),
        ["x = 1<end_of_utterance>garbage after"],
    )
    assert out == ["x = 1"]


def test_post_process_strips_code_opener_too() -> None:
    out = _patched_post_process(
        SimpleNamespace(),
        ["<code>print(1)</code>"],
    )
    assert out == ["print(1)"]


def test_post_process_strips_truncated_close_formula() -> None:
    """Granite emits ``</formula<end_of_utterance>`` (no ``>`` between).
    After upstream truncates at ``<end_of_utterance``, ``</formula``
    leaks because it doesn't match the literal ``</formula>`` strip.
    The patch must clean the truncated form too."""
    out = _patched_post_process(
        SimpleNamespace(),
        ["x = 1</formula<end_of_utterance>"],
    )
    assert out == ["x = 1"]


def test_post_process_strips_truncated_close_code() -> None:
    out = _patched_post_process(
        SimpleNamespace(),
        ["print(1)</code<end_of_utterance>"],
    )
    assert out == ["print(1)"]


def test_post_process_truncated_strip_does_not_eat_well_formed_close() -> None:
    """Order matters: the well-formed ``</formula>`` strip must run
    BEFORE the truncated-form strip so a clean close tag never leaves
    a stray ``>`` behind."""
    out = _patched_post_process(
        SimpleNamespace(),
        ["x = 1</formula>"],
    )
    assert out == ["x = 1"]


def test_leak_loc_regex_only_matches_loc_tokens() -> None:
    """Sanity guard: the regex must not eat non-token text that
    happens to contain ``loc_``."""
    assert _LEAK_LOC_RE.findall("<loc_1><loc_42><loc_999>") == [
        "<loc_1>", "<loc_42>", "<loc_999>",
    ]
    # Don't match within identifiers / prose
    assert _LEAK_LOC_RE.findall("locale loc_42 var<location>") == []


# ---------------------------------------------------------------------------
# _build_patched_call: fake module + fake engine
# ---------------------------------------------------------------------------


class _FakeImage:
    """Minimal stand-in for PIL ``Image.Image``.

    The patch only needs ``isinstance(image, Image.Image)`` to work;
    we make ``Image`` a class whose ``Image`` attribute is itself.
    """

    @staticmethod
    def fromarray(arr: Any) -> "_FakeImage":
        return _FakeImage()


class _FakeImageModule:
    Image = _FakeImage

    @staticmethod
    def fromarray(arr: Any) -> _FakeImage:
        return _FakeImage()


class _RecordingEngine:
    """Fake VLM engine that records every input it received."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def predict_batch(self, engine_inputs: list[Any]) -> list[Any]:
        self.calls.append(engine_inputs)
        return [SimpleNamespace(text="x = 1") for _ in engine_inputs]


def _build_fake_module(engine_input_cls: type) -> ModuleType:
    """Construct a minimal fake of ``code_formula_vlm_model``.

    Only the symbols the replacement ``__call__`` reads need to be
    present. ``CodeItem`` and ``TextItem`` are real classes so
    ``isinstance`` checks behave correctly.
    """

    class CodeItem:
        def __init__(self, label: str = "code") -> None:
            self.label = label
            self.text = ""
            self.code_language = None

    class TextItem:
        def __init__(self, label: str = "formula") -> None:
            self.label = label
            self.text = ""

    fake = ModuleType("fake_code_formula_vlm_model")
    fake.Image = _FakeImageModule
    fake.VlmEngineInput = engine_input_cls
    fake.CodeItem = CodeItem
    fake.TextItem = TextItem
    fake._log = logging.getLogger("fake")
    fake.np = SimpleNamespace()
    return fake


class _RecordingVlmEngineInput:
    """Captures every kwarg the patched ``__call__`` constructs it with."""

    instances: list[dict] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _RecordingVlmEngineInput.instances.append(kwargs)


@pytest.fixture(autouse=True)
def _reset_recording_inputs() -> None:
    _RecordingVlmEngineInput.instances.clear()
    yield
    _RecordingVlmEngineInput.instances.clear()


def _make_self(post_process_out: list[str]) -> SimpleNamespace:
    """Build the ``self`` arg the replacement ``__call__`` expects."""
    return SimpleNamespace(
        enabled=True,
        engine=_RecordingEngine(),
        _get_prompt=lambda label: f"prompt:{label}",
        _post_process=lambda outputs: post_process_out,
        _extract_code_language=lambda txt: (txt, "python"),
        _get_code_language_enum=lambda x: x,
    )


def test_patched_call_sets_repetition_penalty_and_ngram_size() -> None:
    """The decisive test: the patch wires the two generation knobs
    that the upstream code-formula stage omits."""
    fake_mod = _build_fake_module(_RecordingVlmEngineInput)
    patched = _build_patched_call(fake_mod)

    text_item = fake_mod.TextItem()
    element = SimpleNamespace(item=text_item, image=_FakeImage())
    self_obj = _make_self(post_process_out=["clean"])

    list(patched(self_obj, doc=None, element_batch=[element]))

    assert len(_RecordingVlmEngineInput.instances) == 1
    cfg = _RecordingVlmEngineInput.instances[0]
    extra = cfg["extra_generation_config"]
    assert extra["repetition_penalty"] == _REPETITION_PENALTY
    assert extra["no_repeat_ngram_size"] == _NO_REPEAT_NGRAM_SIZE
    assert extra["skip_special_tokens"] is False
    assert cfg["max_new_tokens"] == _MAX_NEW_TOKENS
    assert cfg["stop_strings"] == _STOP_STRINGS
    assert cfg["temperature"] == 0.0


def test_patched_call_disabled_short_circuits() -> None:
    """When the model is disabled, items pass through untouched."""
    fake_mod = _build_fake_module(_RecordingVlmEngineInput)
    patched = _build_patched_call(fake_mod)

    item = fake_mod.TextItem()
    element = SimpleNamespace(item=item, image=_FakeImage())
    self_obj = _make_self(post_process_out=[])
    self_obj.enabled = False

    out = list(patched(self_obj, doc=None, element_batch=[element]))
    assert out == [item]
    assert _RecordingVlmEngineInput.instances == []


def test_patched_call_engine_failure_yields_empty_text() -> None:
    """Mirror upstream's contract: any predict_batch exception is
    caught and the items keep their original (empty) text."""
    class _BoomEngine:
        def predict_batch(self, _: list) -> list:
            raise RuntimeError("boom")

    fake_mod = _build_fake_module(_RecordingVlmEngineInput)
    patched = _build_patched_call(fake_mod)

    item = fake_mod.TextItem()
    element = SimpleNamespace(item=item, image=_FakeImage())
    self_obj = _make_self(post_process_out=[""])
    self_obj.engine = _BoomEngine()

    out = list(patched(self_obj, doc=None, element_batch=[element]))
    assert out == [item]
    assert item.text == ""


def test_patched_call_writes_post_processed_text() -> None:
    """End-to-end through the patched __call__: post-processed text
    is what lands on the item."""
    fake_mod = _build_fake_module(_RecordingVlmEngineInput)
    patched = _build_patched_call(fake_mod)

    item = fake_mod.TextItem()
    element = SimpleNamespace(item=item, image=_FakeImage())
    self_obj = _make_self(post_process_out=["clean latex"])

    list(patched(self_obj, doc=None, element_batch=[element]))
    assert item.text == "clean latex"


# ---------------------------------------------------------------------------
# apply_formula_extraction_patches: idempotency + signature guard
# ---------------------------------------------------------------------------


def test_apply_patches_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call must NOT re-patch (the global flag is the
    contract; observed via call counter on a fake module)."""
    monkeypatch.setattr(_docling_patches, "_PATCHES_APPLIED", False)

    apply_count = {"calls": 0}

    def fake_patch_call(cls, mod):
        apply_count["calls"] += 1

    def fake_patch_post(cls):
        apply_count["calls"] += 1

    fake_module = ModuleType("fake_code_formula_vlm_model")
    fake_module.CodeFormulaVlmModel = type("X", (), {"_post_process": lambda *a: []})
    parent = ModuleType("docling.models.stages.code_formula")
    parent.code_formula_vlm_model = fake_module
    monkeypatch.setitem(
        sys.modules,
        "docling.models.stages.code_formula",
        parent,
    )
    monkeypatch.setitem(
        sys.modules,
        "docling.models.stages.code_formula.code_formula_vlm_model",
        fake_module,
    )
    monkeypatch.setattr(_docling_patches, "_patch_call", fake_patch_call)
    monkeypatch.setattr(_docling_patches, "_patch_post_process", fake_patch_post)

    apply_formula_extraction_patches()
    apply_formula_extraction_patches()
    apply_formula_extraction_patches()

    assert apply_count["calls"] == 2  # one _patch_call + one _patch_post


def test_apply_patches_no_op_when_docling_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the docling code-formula stage isn't installed, the patcher
    must silently skip — not crash an unrelated parse."""
    import builtins

    monkeypatch.setattr(_docling_patches, "_PATCHES_APPLIED", False)
    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if "code_formula" in name:
            raise ImportError("simulated absent module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    # Drop any cached reference so the patcher actually re-imports.
    for mod_name in list(sys.modules):
        if "code_formula" in mod_name:
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    apply_formula_extraction_patches()
    assert _docling_patches._PATCHES_APPLIED is False


def test_patch_call_warns_when_required_symbols_missing(
    capsys: pytest.CaptureFixture,
) -> None:
    """Signature guard: missing module symbol produces a stderr
    warning and skips the patch instead of installing a broken
    replacement."""
    incomplete = ModuleType("incomplete_mod")
    # Deliberately omit ``Image``, ``VlmEngineInput``, etc.
    cls = type("X", (), {"__call__": lambda self: None})
    _docling_patches._patch_call(cls, incomplete)

    # Original __call__ untouched
    captured = capsys.readouterr()
    assert "missing expected symbols" in captured.err
    # Test by signature: the replacement would take (self, doc, element_batch)
    # — original takes just (self,). If patch had applied, calling without
    # those args would fail differently.
    cls()  # original lambda accepts only self


def test_patch_post_process_warns_on_missing_method(
    capsys: pytest.CaptureFixture,
) -> None:
    """Same defense for the post-process patch."""

    class Empty:
        pass

    _docling_patches._patch_post_process(Empty)
    assert "_post_process missing" in capsys.readouterr().err
