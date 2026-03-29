"""Unified LLM client with disk caching."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import diskcache
import litellm
from rich.console import Console

from scholarforge.config import settings

console = Console()

# Suppress litellm debug noise
litellm.suppress_debug_info = True

# Disk cache for LLM responses (keyed by model + messages hash)
_cache: diskcache.Cache | None = None


def _get_cache() -> diskcache.Cache:
    global _cache
    if _cache is None:
        cache_dir = settings.cache_dir / "llm_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache = diskcache.Cache(str(cache_dir))
    return _cache


def _cache_key(model: str, messages: list[dict], **kwargs: Any) -> str:
    """Deterministic cache key from model + messages + relevant kwargs."""
    payload = json.dumps({"model": model, "messages": messages, **kwargs}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def complete(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> str:
    """Send a chat completion request and return the text response.

    Uses disk cache by default to avoid duplicate API calls.
    """
    model = model or settings.llm_model

    # Check API key early
    if "anthropic" in model or "claude" in model:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set. Add it to .env or export it.")

    cache_params = {"temperature": temperature, "max_tokens": max_tokens}
    key = _cache_key(model, messages, **cache_params)

    if use_cache:
        cache = _get_cache()
        cached = cache.get(key)
        if cached is not None:
            return cached

    start = time.time()
    response = litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    elapsed = time.time() - start

    text = response.choices[0].message.content or ""
    console.print(f"[dim]  LLM ({model}): {elapsed:.1f}s, {len(text)} chars[/dim]")

    if use_cache:
        cache = _get_cache()
        cache.set(key, text)

    return text


def complete_streaming(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
):
    """Stream a chat completion, yielding text chunks."""
    model = model or settings.llm_model

    response = litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in response:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content
