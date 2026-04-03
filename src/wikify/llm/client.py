"""Unified LLM client with disk caching and structured output."""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

import diskcache
import litellm
from pydantic import BaseModel, ValidationError
from rich.console import Console

from wikify.config import settings

if TYPE_CHECKING:
    from wikify.llm.hooks import LLMEvent, LLMHook

console = Console()

# Suppress litellm debug noise
litellm.suppress_debug_info = True


# ── Cache manager ─────────────────────────────────────────────────────────────


class CacheManager:
    """Manages the diskcache lifecycle for LLM responses.

    Designed for dependency injection: create an instance and pass it where
    needed.  The module-level ``_cache_mgr`` instance is used by the
    convenience function below.
    """

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = cache_dir
        self._cache: diskcache.Cache | None = None

    @property
    def cache(self) -> diskcache.Cache:
        """Return the diskcache.Cache, creating it on first access."""
        if self._cache is None:
            resolved = self._cache_dir or str(settings.cache_dir / "llm_cache")
            import pathlib

            pathlib.Path(resolved).mkdir(parents=True, exist_ok=True)
            self._cache = diskcache.Cache(resolved)
        return self._cache


# ── Module-level instance ─────────────────────────────────────────────────────

_cache_mgr = CacheManager()


def _get_cache() -> diskcache.Cache:
    """Return the diskcache.Cache from the module-level CacheManager."""
    return _cache_mgr.cache


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

    # litellm handles API key validation for all providers — no provider-specific checks here

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


def complete_json(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | list:
    """Send a completion request expecting JSON output.

    Handles markdown fence stripping and JSON boundary recovery.
    """
    text = complete(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3].rstrip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find JSON boundaries
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


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


# ── Structured output ────────────────────────────────────────────────────────


class LLMOutputError(Exception):
    """Raised when LLM output fails validation after all retries."""

    def __init__(self, errors: list[str], raw_output: str) -> None:
        self.errors = errors
        self.raw_output = raw_output
        super().__init__(f"LLM output invalid after retries: {errors}")


def schema_to_prompt(model_cls: type[BaseModel]) -> str:
    """Convert a Pydantic model to LLM-friendly JSON format instructions."""
    schema = model_cls.model_json_schema()
    return (
        "Return a JSON object conforming to this schema:\n"
        f"```json\n{json.dumps(schema, indent=2)}\n```\n"
        "Return ONLY valid JSON. No markdown fences, no commentary."
    )


def _extract_json(text: str) -> dict | list | None:
    """Extract a JSON object or array from raw LLM text.

    Handles markdown fences and boundary recovery. Returns None on failure.
    """
    text = text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3].rstrip()

    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find JSON boundaries
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    return None


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _run_hooks_before(
    hooks: list[LLMHook],
    event: LLMEvent,
) -> LLMEvent:
    for hook in hooks:
        event = hook.before_call(event)
    return event


def _run_hooks_after(
    hooks: list[LLMHook],
    event: LLMEvent,
) -> LLMEvent:
    for hook in hooks:
        event = hook.after_call(event)
    return event


def complete_structured(
    messages: list[dict[str, str]],
    response_model: type[BaseModel],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_retries: int = 2,
    hooks: list[LLMHook] | None = None,
) -> BaseModel:
    """Send a completion request and validate against a Pydantic model.

    On validation failure, appends the error to the conversation and retries.
    Raises ``LLMOutputError`` after *max_retries* failed attempts.
    """
    from wikify.llm.hooks import LLMEvent

    resolved_model = model or settings.llm_model
    active_hooks: list[LLMHook] = hooks or []

    # Inject schema instructions into the system message
    schema_instructions = schema_to_prompt(response_model)
    enriched: list[dict[str, str]] = _inject_schema(messages, schema_instructions)

    errors_so_far: list[str] = []
    raw = ""

    for attempt in range(max_retries + 1):
        event = LLMEvent(
            messages=enriched,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
            attempt=attempt,
        )
        event = _run_hooks_before(active_hooks, event)

        start_time = time.time()
        raw = complete(
            messages=enriched,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_cache=(attempt == 0),
        )
        elapsed_ms = (time.time() - start_time) * 1000

        event.raw_response = raw
        event.latency_ms = elapsed_ms
        event.input_tokens = sum(_estimate_tokens(m.get("content", "")) for m in enriched)
        event.output_tokens = _estimate_tokens(raw)

        # Parse JSON
        parsed = _extract_json(raw)
        if parsed is None:
            error_msg = f"Could not extract valid JSON from response: {raw[:200]}"
            errors_so_far.append(error_msg)
            event.parsed_output = None
            event = _run_hooks_after(active_hooks, event)
            enriched = _append_retry_context(enriched, raw, error_msg)
            continue

        # Validate against Pydantic model
        try:
            result = response_model.model_validate(parsed)
            event.parsed_output = result
            event = _run_hooks_after(active_hooks, event)
            return result
        except ValidationError as e:
            error_msg = str(e)
            errors_so_far.append(error_msg)
            event.parsed_output = None
            event = _run_hooks_after(active_hooks, event)
            enriched = _append_retry_context(enriched, raw, error_msg)

    raise LLMOutputError(errors_so_far, raw)


def validate_and_retry_text(
    messages: list[dict[str, str]],
    response_model: type[BaseModel],
    content_field: str = "content",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    max_retries: int = 2,
    hooks: list[LLMHook] | None = None,
    skip_citation_check: bool = False,
) -> tuple[str, BaseModel]:
    """Call ``complete()`` for prose output, then validate via a Pydantic model.

    Unlike ``complete_structured()``, this does NOT ask the LLM for JSON.
    The raw text is wrapped into ``{content_field: text}`` and validated.
    On failure the validation errors are fed back and the LLM retries.

    Returns ``(raw_text, validated_model)`` on success.
    Raises ``LLMOutputError`` after *max_retries* failed attempts.
    """
    from wikify.llm.hooks import LLMEvent

    resolved_model = model or settings.llm_model
    active_hooks: list[LLMHook] = hooks or []

    enriched: list[dict[str, str]] = list(messages)
    errors_so_far: list[str] = []
    raw = ""

    for attempt in range(max_retries + 1):
        event = LLMEvent(
            messages=enriched,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
            attempt=attempt,
        )
        event = _run_hooks_before(active_hooks, event)

        start_time = time.time()
        raw = complete(
            messages=enriched,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_cache=(attempt == 0),
        )
        elapsed_ms = (time.time() - start_time) * 1000

        event.raw_response = raw
        event.latency_ms = elapsed_ms
        event.input_tokens = sum(_estimate_tokens(m.get("content", "")) for m in enriched)
        event.output_tokens = _estimate_tokens(raw)

        # Validate the prose as a Pydantic model
        try:
            result = response_model.model_validate({content_field: raw})
            # Run optional citation check (not a field validator so it can
            # be skipped for Abstract sections)
            if not skip_citation_check and hasattr(result, "check_citations"):
                result.check_citations()
            event.parsed_output = result
            event = _run_hooks_after(active_hooks, event)
            return raw, result
        except (ValidationError, ValueError) as e:
            error_msg = str(e)
            errors_so_far.append(error_msg)
            event.parsed_output = None
            event = _run_hooks_after(active_hooks, event)
            enriched = enriched + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"Your previous output failed quality validation:\n"
                        f"{error_msg}\n\n"
                        "Please rewrite the section fixing the issues above."
                    ),
                },
            ]

    raise LLMOutputError(errors_so_far, raw)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _inject_schema(
    messages: list[dict[str, str]],
    schema_instructions: str,
) -> list[dict[str, str]]:
    """Append schema instructions to the system message."""
    enriched = list(messages)
    for i, msg in enumerate(enriched):
        if msg.get("role") == "system":
            enriched[i] = {
                "role": "system",
                "content": msg["content"] + "\n\n" + schema_instructions,
            }
            return enriched
    # No system message found — prepend one
    return [{"role": "system", "content": schema_instructions}, *enriched]


def _append_retry_context(
    messages: list[dict[str, str]],
    raw_output: str,
    error: str,
) -> list[dict[str, str]]:
    """Feed the failed output + error back for retry."""
    return [
        *messages,
        {"role": "assistant", "content": raw_output},
        {
            "role": "user",
            "content": (
                f"Your previous output failed validation:\n{error}\n\n"
                "Please fix the errors and return valid JSON."
            ),
        },
    ]
