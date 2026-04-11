"""ONNX Runtime GenAI provider for local LLM inference.

Wraps onnxruntime-genai to provide a complete()-compatible interface for
running quantized models on GPU. Used as an alternative to the fast-tier
hosted model when cost or offline operation is a priority.

Usage::

    provider = OnnxProvider("data/cache/models/phi-3.5-mini-instruct-onnx/cuda/cuda-int4-rtn-block-32")
    text = provider.complete(messages, max_tokens=1024, temperature=0.1)
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
import time
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _add_nvidia_to_path() -> None:
    """Add pip-installed NVIDIA CUDA runtime bin dirs to ``PATH`` (Windows).

    Packages like ``nvidia-cublas-cu12``, ``nvidia-cufft-cu12``, etc.
    install DLLs under ``.venv/Lib/site-packages/nvidia/*/bin/``. ONNX
    Runtime GenAI needs these on ``PATH`` to load the CUDA execution
    provider. ``lru_cache`` makes the operation idempotent without a
    module-level mutable flag.
    """

    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    nvidia_bins = glob.glob(str(site_packages / "nvidia" / "*" / "bin"))
    if nvidia_bins:
        extra = ";".join(str(Path(b).resolve()) for b in nvidia_bins)
        os.environ["PATH"] = extra + ";" + os.environ.get("PATH", "")
        logger.debug("Added %d NVIDIA bin dirs to PATH", len(nvidia_bins))


class OnnxProvider:
    """Local LLM inference via ONNX Runtime GenAI with CUDA support."""

    def __init__(self, model_path: str) -> None:
        self._model_path = str(Path(model_path).resolve())
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        """Lazy-load the ONNX model and tokenizer on first call.

        ``onnxruntime_genai`` is imported lazily because it is an
        optional dependency: importing this module must succeed even
        when the package is not installed (e.g. for type checking,
        documentation generation, or environments without CUDA).
        """

        if self._model is not None:
            return

        try:
            import onnxruntime_genai as og  # noqa: PLC0415  (optional dep)
        except ImportError as exc:
            raise ImportError(
                "onnxruntime-genai-cuda is required for local ONNX inference. "
                "Install with: uv pip install onnxruntime-genai-cuda"
            ) from exc

        _add_nvidia_to_path()

        logger.info("Loading ONNX model from %s", self._model_path)
        start = time.monotonic()
        self._model = og.Model(self._model_path)
        self._tokenizer = og.Tokenizer(self._model)
        elapsed = time.monotonic() - start
        device = getattr(self._model, "device_type", "unknown")
        logger.info("ONNX model loaded in %.1fs (device: %s)", elapsed, device)

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> str:
        """Generate a completion from chat messages."""

        import onnxruntime_genai as og  # noqa: PLC0415  (optional dep)

        self._ensure_loaded()

        prompt = self._format_messages(messages)
        input_tokens = self._tokenizer.encode(prompt)

        params = og.GeneratorParams(self._model)
        params.set_search_options(
            max_length=len(input_tokens) + max_tokens,
            temperature=max(temperature, 0.01),
            top_p=0.9,
            do_sample=temperature > 0.01,
        )

        start = time.monotonic()
        generator = og.Generator(self._model, params)
        generator.append_tokens(input_tokens)

        output_tokens: list[int] = []
        while not generator.is_done():
            generator.generate_next_token()
            token = generator.get_next_tokens()[0]
            output_tokens.append(token)

        elapsed = time.monotonic() - start
        text = self._tokenizer.decode(output_tokens)

        tokens_per_sec = len(output_tokens) / elapsed if elapsed > 0 else 0
        logger.info(
            "ONNX generation: %d tokens in %.2fs (%.1f tok/s)",
            len(output_tokens),
            elapsed,
            tokens_per_sec,
        )
        return text

    def complete_json(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> dict | list:
        """Generate a completion and parse it as JSON.

        Strips markdown fences and recovers JSON boundaries to match
        the behavior of ``wikify.core.llm.client.complete_json``.
        """

        text = self.complete(messages, max_tokens, temperature).strip()

        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue

        logger.warning("Failed to parse JSON from ONNX output: %.200s", text)
        return []

    def _format_messages(self, messages: list[dict[str, str]]) -> str:
        """Format chat messages with the Phi-3.5 ChatML template."""

        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<|{role}|>\n{content}<|end|>\n")
        parts.append("<|assistant|>\n")
        return "".join(parts)
