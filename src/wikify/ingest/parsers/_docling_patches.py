"""Targeted monkeypatches for ``docling.CodeFormulaVlmModel``.

Docling 2.86 hardwires Granite-Docling's decoder with
``max_new_tokens=2048`` and an ``extra_generation_config`` containing
only ``skip_special_tokens=False``. No ``repetition_penalty``,
``no_repeat_ngram_size``, or ``stop_strings`` are configured, even
though the underlying ``transformers_engine`` forwards every one of
those to ``model.generate(...)``. The result is that ~11% of papers
in formula-heavy corpora hit autoregressive degeneration: the head
loops on a 2-3 token cycle until the budget is exhausted, producing
both garbage LaTeX and minutes of wasted wall-clock per paper.

These patches close the gap by:

1. Replacing ``CodeFormulaVlmModel.__call__`` so that each
   ``VlmEngineInput`` carries:
     - ``max_new_tokens = _MAX_NEW_TOKENS`` (raised from upstream's
       hardcoded 2048; the spec's intent is 8192 but we cap at 4096
       to bound worst-case wall-clock when the loop guards still fail);
     - ``stop_strings = _STOP_STRINGS`` (forces clean termination at
       the actual ``</formula>`` / ``</code>`` boundary);
     - ``extra_generation_config`` augmented with
       ``repetition_penalty`` and ``no_repeat_ngram_size`` — the two
       knobs that empirically prevent the loop in autoregressive
       transformer decoders.

2. Extending ``CodeFormulaVlmModel._post_process`` to also strip the
   ``<formula>`` / ``<code>`` opener tokens and arbitrary
   ``<loc_NNN>`` bbox tokens. Upstream already strips the closing
   tags and the standard ``<loc_0><loc_0><loc_500><loc_500>`` bbox;
   the extension just completes the surface so residual tokens after
   a clean decode never reach ``FormulaItem.text``.

Patches are applied once per process (idempotent) and only when the
upstream class still exposes the exact attribute shape we patched
against. If Docling refactors away the targeted methods, both patches
no-op with a stderr warning so the regression surfaces instead of
silently stomping new code.
"""

from __future__ import annotations

import logging
import re
import sys

_log = logging.getLogger(__name__)

# Generation hyperparameters.
#
# - repetition_penalty=1.15: conventional value for technical text
#   decoders. Discourages re-emitting recently-emitted tokens without
#   distorting greedy decoding for typical equation tokens.
# - no_repeat_ngram_size=12: forbids repeating any 12-gram. Long
#   enough that LaTeX subscripts / matrix rows that legitimately
#   re-use 3-5 token spans aren't blocked, short enough to terminate
#   the observed `\text{not} \, s` (×3 tokens) cycle long before it
#   reaches 2000 repetitions.
# - max_new_tokens=4096: doubled from upstream's hardcoded 2048;
#   matches the engine's default. The actual model spec exposes 8192
#   but the code-formula stage never reads it, so we cap at 4096 to
#   bound worst-case decode time on the rare paper that exhausts the
#   budget despite the repetition guards.
_REPETITION_PENALTY = 1.15
_NO_REPEAT_NGRAM_SIZE = 12
_MAX_NEW_TOKENS = 4096

# Stop strings honoured by the transformers engine via
# ``StopStringCriteria``. ``</formula>`` is the legitimate end of a
# formula decode; ``</code>`` likewise for code blocks; the third
# entry catches the special-token form of EOS that the tokenizer
# sometimes emits in literal text.
_STOP_STRINGS: list[str] = ["</formula>", "</code>", "<end_of_utterance>"]

# Residual tokens to strip after generation. Upstream already removes
# ``</code>``, ``</formula>``, and the standard
# ``<loc_0><loc_0><loc_500><loc_500>`` placeholder; we add the openers
# and a regex for arbitrary location tokens.
_OPENER_TOKENS: tuple[str, ...] = ("<formula>", "<code>")
_LEAK_LOC_RE = re.compile(r"<loc_\d+>")

# Truncated-close-tag forms. Granite-Docling regularly emits
# ``</formula<end_of_utterance>`` as one contiguous sequence — no
# ``>`` between the two markers. After upstream truncates at
# ``<end_of_utterance``, the residue is ``</formula`` (no closing
# ``>``), which the literal ``</formula>`` strip in upstream fails
# to match. ``</code`` lands the same way for code blocks. Strip
# these AFTER the well-formed close-tag pass so we only catch the
# truncated cases.
_TRUNCATED_CLOSE_TOKENS: tuple[str, ...] = ("</formula", "</code")

_PATCHES_APPLIED = False


def apply_formula_extraction_patches() -> None:
    """Apply ``CodeFormulaVlmModel`` patches once per process."""
    global _PATCHES_APPLIED
    if _PATCHES_APPLIED:
        return
    try:
        from docling.models.stages.code_formula import code_formula_vlm_model
    except ImportError:
        _log.debug("docling code-formula stage not present; skipping patches")
        return

    cls = getattr(code_formula_vlm_model, "CodeFormulaVlmModel", None)
    if cls is None:
        sys.stderr.write(
            "[wikify] CodeFormulaVlmModel missing in installed docling; "
            "formula generation patches NOT applied\n",
        )
        return

    _patch_call(cls, code_formula_vlm_model)
    _patch_post_process(cls)
    _PATCHES_APPLIED = True


def _patched_post_process(self, texts: list[str]) -> list[str]:
    """Drop-in replacement for ``CodeFormulaVlmModel._post_process``.

    Mirrors upstream's contract — strip closing tags + the standard
    bbox placeholder + ``<end_of_utterance>`` trailers — and ALSO
    strips the ``<formula>`` / ``<code>`` opener tokens plus any
    arbitrary ``<loc_NNN>`` location token. Without these additions a
    Granite-Docling decode that fails to consume the wrapper leaks
    raw VLM vocabulary into ``FormulaItem.text``.
    """
    base_remove = ["</code>", "</formula>", "<loc_0><loc_0><loc_500><loc_500>"]

    def clean(text: str) -> str:
        idx = text.find("<end_of_utterance>")
        if idx == -1:
            idx = text.find("<end_of_utterance")
        if idx != -1:
            text = text[:idx]
        for token in base_remove:
            if token in text:
                text = text.replace(token, "")
        for token in _OPENER_TOKENS:
            if token in text:
                text = text.replace(token, "")
        text = _LEAK_LOC_RE.sub("", text)
        # Truncated close tags (``</formula`` / ``</code`` without
        # the trailing ``>``). Run AFTER the well-formed strip above
        # so legitimate close tags don't leave a stray ``>`` behind.
        for token in _TRUNCATED_CLOSE_TOKENS:
            if token in text:
                text = text.replace(token, "")
        return text.lstrip()

    return [clean(t) for t in texts]


def _patch_post_process(cls) -> None:
    if not hasattr(cls, "_post_process"):
        sys.stderr.write(
            "[wikify] CodeFormulaVlmModel._post_process missing; "
            "leak-token strip NOT applied\n",
        )
        return
    cls._post_process = _patched_post_process


def _build_patched_call(module):
    """Build a replacement ``__call__`` bound to ``module``'s symbols.

    Kept as a closure so tests can rebuild the replacement against an
    injected fake module without mutating the real Docling import.
    Names match upstream's class names (``Image``, ``VlmEngineInput``,
    ``CodeItem``, ``TextItem``) so the body reads side-by-side with
    the original method.
    """
    image_mod = module.Image
    vlm_engine_input = module.VlmEngineInput
    code_item_cls = module.CodeItem
    text_item_cls = module.TextItem
    log_upstream = module._log

    def patched_call(self, doc, element_batch):
        if not self.enabled:
            for element in element_batch:
                yield element.item
            return
        if self.engine is None:
            raise RuntimeError("Engine not initialized")

        labels: list[str] = []
        images: list = []
        elements: list = []
        for el in element_batch:
            assert isinstance(el.item, (code_item_cls, text_item_cls))
            elements.append(el.item)
            labels.append(el.item.label)
            images.append(el.image)

        try:
            engine_inputs = [
                vlm_engine_input(
                    image=image
                    if isinstance(image, image_mod.Image)
                    else image_mod.fromarray(image),
                    prompt=self._get_prompt(label),
                    temperature=0.0,
                    max_new_tokens=_MAX_NEW_TOKENS,
                    stop_strings=list(_STOP_STRINGS),
                    extra_generation_config={
                        "skip_special_tokens": False,
                        "repetition_penalty": _REPETITION_PENALTY,
                        "no_repeat_ngram_size": _NO_REPEAT_NGRAM_SIZE,
                    },
                )
                for image, label in zip(images, labels)
            ]
            batch_outputs = self.engine.predict_batch(engine_inputs)
            outputs = [output.text for output in batch_outputs]
        except Exception as e:
            log_upstream.error(f"Error processing code/formula batch: {e}")
            outputs = [""] * len(images)

        outputs = self._post_process(outputs)
        for item, output_text in zip(elements, outputs):
            if isinstance(item, code_item_cls):
                output_text, code_language = self._extract_code_language(output_text)
                item.code_language = self._get_code_language_enum(code_language)
            item.text = output_text
            yield item

    return patched_call


def _patch_call(cls, module) -> None:
    # Surface refactor checks: the upstream ``__call__`` reads these
    # symbols from its module — if any are gone the patch must NOT
    # silently apply, because the replacement would crash with an
    # opaque NameError on first use.
    required = ("Image", "VlmEngineInput", "CodeItem", "TextItem", "_log")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        sys.stderr.write(
            f"[wikify] CodeFormulaVlmModel module missing expected "
            f"symbols {missing}; generation-config patch NOT applied\n",
        )
        return
    if not hasattr(cls, "__call__"):
        sys.stderr.write(
            "[wikify] CodeFormulaVlmModel.__call__ missing; "
            "generation-config patch NOT applied\n",
        )
        return
    cls.__call__ = _build_patched_call(module)
