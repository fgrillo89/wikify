"""ONNX Runtime GenAI provider for local LLM inference.

Wraps onnxruntime-genai to provide a complete() compatible interface for
running quantized models on GPU. Used as an alternative to the fast tier for
concept extraction (Pass 1) when cost or offline operation is a priority.

Usage:
    provider = OnnxProvider("data/cache/models/phi-3.5-mini-instruct-onnx/cuda/cuda-int4-rtn-block-32")
    text = provider.complete(messages, max_tokens=1024, temperature=0.1)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_nvidia_path_added = False


def _add_nvidia_to_path() -> None:
    """Add pip-installed NVIDIA CUDA runtime bin dirs to PATH (Windows).

    Packages like nvidia-cublas-cu12, nvidia-cufft-cu12, etc. install
    DLLs under .venv/Lib/site-packages/nvidia/*/bin/.  ONNX Runtime
    GenAI needs these on PATH to load the CUDA execution provider.
    """
    global _nvidia_path_added  # noqa: PLW0603
    if _nvidia_path_added:
        return

    import glob
    import os
    import sys

    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    nvidia_bins = glob.glob(str(site_packages / "nvidia" / "*" / "bin"))
    if nvidia_bins:
        extra = ";".join(str(Path(b).resolve()) for b in nvidia_bins)
        os.environ["PATH"] = extra + ";" + os.environ.get("PATH", "")
        logger.debug("Added %d NVIDIA bin dirs to PATH", len(nvidia_bins))

    _nvidia_path_added = True


class OnnxProvider:
    """Local LLM inference via ONNX Runtime GenAI with CUDA support."""

    def __init__(self, model_path: str) -> None:
        self._model_path = str(Path(model_path).resolve())
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        """Lazy-load model and tokenizer on first call."""
        if self._model is not None:
            return

        try:
            import onnxruntime_genai as og
        except ImportError as exc:
            raise ImportError(
                "onnxruntime-genai-cuda is required for local ONNX inference. "
                "Install with: uv pip install onnxruntime-genai-cuda"
            ) from exc

        # Ensure NVIDIA CUDA runtime DLLs are on PATH (pip-installed)
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
        """Generate a completion from chat messages.

        Args:
            messages: List of {"role": "user"/"system"/"assistant", "content": str}
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.

        Returns:
            Generated text string.
        """
        import onnxruntime_genai as og

        self._ensure_loaded()

        # Format messages into a single prompt using ChatML template
        prompt = self._format_messages(messages)

        # Tokenize
        input_tokens = self._tokenizer.encode(prompt)

        # Configure generation parameters
        params = og.GeneratorParams(self._model)
        params.set_search_options(
            max_length=len(input_tokens) + max_tokens,
            temperature=max(temperature, 0.01),  # ORT doesn't accept 0.0
            top_p=0.9,
            do_sample=temperature > 0.01,
        )

        # Generate token-by-token
        start = time.monotonic()
        generator = og.Generator(self._model, params)
        generator.append_tokens(input_tokens)

        output_tokens = []
        while not generator.is_done():
            generator.generate_next_token()
            token = generator.get_next_tokens()[0]
            output_tokens.append(token)

        elapsed = time.monotonic() - start

        # Decode output (skip input tokens)
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
        """Generate a completion and parse as JSON.

        Strips markdown fences and recovers JSON boundaries,
        matching the behavior of wikify.core.llm.client.complete_json.
        """
        text = self.complete(messages, max_tokens, temperature)
        text = text.strip()

        # Strip markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fallback: find JSON boundaries
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
        """Format chat messages using Phi-3.5 template.

        Template: <|system|>\\n{content}<|end|>\\n<|user|>\\n{content}<|end|>\\n<|assistant|>\\n
        """
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<|{role}|>\n{content}<|end|>\n")
        parts.append("<|assistant|>\n")
        return "".join(parts)
