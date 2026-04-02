"""Core ScholarForgeAgent — internal LLM agent loop using litellm tool_use."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_type_hints

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from scholarforge.agent.run_context import RunContext
    from scholarforge.llm.hooks import LLMHook


# ── Tool schema builder ───────────────────────────────────────────────────────

_PY_TO_JSON_TYPE: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _py_type_to_json(annotation: Any) -> str:
    """Map a Python type annotation to a JSON Schema type string."""
    return _PY_TO_JSON_TYPE.get(annotation, "string")


def _fn_to_tool_schema(fn: Callable) -> dict[str, Any]:
    """Convert a Python function to an OpenAI/litellm tool schema dict."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        json_type = _py_type_to_json(hints.get(name))
        prop: dict[str, Any] = {"type": json_type}

        # Pull inline description from docstring "param_name: ..." if present
        if fn.__doc__:
            for line in fn.__doc__.splitlines():
                line = line.strip()
                if line.startswith(f"{name}:") or line.startswith(f"{name} :"):
                    description = line.split(":", 1)[1].strip()
                    if description:
                        prop["description"] = description
                    break

        properties[name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def fns_to_tool_schemas(fns: list[Callable]) -> list[dict[str, Any]]:
    """Convert a list of Python callables to litellm tool schema dicts."""
    return [_fn_to_tool_schema(fn) for fn in fns]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class ToolCallRecord:
    """Record of a single tool call within an agent run."""

    tool_name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: float
    turn: int


@dataclass
class TurnTelemetry:
    """Per-turn timing and token data for profiling."""

    turn: int
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    tool_names: list[str] = field(default_factory=list)
    tool_durations_ms: list[float] = field(default_factory=list)
    context_chars: int = 0  # total message chars at start of turn


@dataclass
class AgentResult:
    """Result of an agent run."""

    content: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    turn_telemetry: list[TurnTelemetry] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    run_context: RunContext | None = None


# ── Tool result compaction ────────────────────────────────────────────────────


def _compact_tool_results(
    messages: list[dict],
    threshold: int = 2000,
    run_context: RunContext | None = None,
) -> None:
    """Compact large tool results from prior turns to save context tokens.

    Context-aware compaction:
    - If the model called record_paper_summary after a deep_read, the summary
      captures what's important. The raw text can be compacted aggressively.
    - If no summary was recorded, keep more context (the model may still need it).
    - The preview length is proportional to the original content, with a floor
      and ceiling that depend on whether a summary exists.

    This is model-agnostic: modifies the message list before any LLM call.

    Args:
        messages: The conversation message list (modified in-place).
        threshold: Character count above which a tool result is compacted.
    """
    last_assistant_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], dict) and messages[i].get("role") == "assistant":
            last_assistant_idx = i
            break

    if last_assistant_idx < 0:
        return

    # Check which tool calls were followed by a record_paper_summary call
    # (meaning the model already extracted what it needs)
    summarized_tool_ids: set[str] = set()
    for i in range(len(messages)):
        msg = messages[i]
        if not isinstance(msg, dict):
            continue
        # Look for assistant messages that contain a record_paper_summary call
        tool_calls = msg.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc if isinstance(tc, dict) else getattr(tc, "function", None)
                if fn is None:
                    continue
                name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
                if name == "record_paper_summary":
                    # The tool call RIGHT BEFORE this assistant message was summarized
                    # Mark all tool results between the previous assistant and this one
                    for j in range(i - 1, -1, -1):
                        prev = messages[j]
                        if isinstance(prev, dict) and prev.get("role") == "tool":
                            tid = prev.get("tool_call_id", "")
                            if tid:
                                summarized_tool_ids.add(tid)
                        elif isinstance(prev, dict) and prev.get("role") == "assistant":
                            break

    for i in range(last_assistant_idx):
        msg = messages[i]
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if len(content) <= threshold:
            continue

        original_len = len(content)
        tool_id = msg.get("tool_call_id", "")
        has_summary = tool_id in summarized_tool_ids

        if has_summary:
            # Model already extracted what it needs — compact aggressively
            # Keep just enough for citation reference (metadata + first finding)
            preview_len = max(300, min(1000, original_len // 20))
            note = "Summary recorded via record_paper_summary."
        else:
            # No summary yet — keep more context (up to 20% for hub papers)
            preview_len = max(1000, min(5000, original_len // 5))
            note = "No summary recorded. Consider calling record_paper_summary."

        preview = content[:preview_len]
        msg["content"] = f"{preview}\n\n[... compacted: {original_len} chars. {note}]"

    # After compaction, inject session context once so the model has all
    # summaries available without needing to call get_session_context()
    _inject_session_context(messages, run_context=run_context)


_SESSION_CONTEXT_MARKER = "[Session context: paper summaries]"


def _session_level_compaction(messages: list[dict]) -> None:
    """Drop old turns when total message size is excessive.

    Adaptive threshold: triggers when total chars exceed 3x the system
    prompt size (the static content). This scales naturally — a long
    system prompt (detailed style guide) gets more room; a short one
    triggers compaction earlier.

    Keeps: system messages, session context, and enough recent messages
    to maintain conversation coherence (at least the last assistant +
    tool exchange cycle).
    """
    # Compute total and system-only sizes
    total_chars = 0
    system_chars = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content", "") or ""
        size = len(content)
        total_chars += size
        if m.get("role") == "system":
            system_chars += size

    # Adaptive threshold: 3x system prompt size, minimum 80K chars
    threshold = max(80_000, system_chars * 3)
    if total_chars < threshold:
        return

    # Adaptive keep_recent: keep more if messages are short, fewer if large
    non_system_count = sum(1 for m in messages if isinstance(m, dict) and m.get("role") != "system")
    non_system_chars = total_chars - system_chars
    avg_msg_size = non_system_chars / max(non_system_count, 1)

    # Small messages (tool confirmations, short responses): keep 8
    # Large messages (deep reads still in context): keep 4
    keep_recent = 8 if avg_msg_size < 2000 else 4

    # Identify system messages (always keep)
    system_indices = [
        i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "system"
    ]

    if len(messages) <= len(system_indices) + keep_recent:
        return

    cutoff = len(messages) - keep_recent
    keep_indices = set(system_indices) | set(range(cutoff, len(messages)))

    dropped_count = sum(1 for i in range(len(messages)) if i not in keep_indices)
    if dropped_count == 0:
        return

    new_messages = []
    dropped = False
    for i, msg in enumerate(messages):
        if i in keep_indices:
            new_messages.append(msg)
        elif not dropped:
            new_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[{dropped_count} earlier messages compacted. "
                        f"Paper summaries preserved in session context above.]"
                    ),
                }
            )
            dropped = True

    messages.clear()
    messages.extend(new_messages)


def _inject_session_context(
    messages: list[dict],
    run_context: RunContext | None = None,
) -> None:
    """Inject or update the session context summary in the message list.

    Replaces any prior session context message with an updated version
    containing all paper summaries recorded so far. This ensures the
    model always has access to its structured notes without needing
    to call get_session_context() explicitly.

    Injected as a system message so it doesn't break assistant/user alternation.
    """
    try:
        from scholarforge.agent.run_context import get_current_run_context
        from scholarforge.agent.tools import get_session_context

        active_context = run_context or get_current_run_context()
        summaries = list(active_context.paper_summaries)
        if len(summaries) < 2:
            return

        context_text = get_session_context()
        if not context_text or "No paper summaries" in context_text:
            return

        # Include concept graph if enabled and has edges
        graph_text = ""
        try:
            from scholarforge.config import settings as _cfg

            if _cfg.inject_concept_graph and active_context.concept_graph.edges:
                graph_text = "\n\n" + active_context.concept_graph.to_compact_text()
        except Exception:  # noqa: BLE001
            pass

        ctx_message = {
            "role": "system",
            "content": f"{_SESSION_CONTEXT_MARKER}\n\n{context_text}{graph_text}",
        }

        # Replace existing session context message if present
        for i, msg in enumerate(messages):
            if (
                isinstance(msg, dict)
                and msg.get("role") == "system"
                and _SESSION_CONTEXT_MARKER in msg.get("content", "")
            ):
                messages[i] = ctx_message
                return

        # Insert after the first system message (the main prompt)
        insert_idx = 1
        for i, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                insert_idx = i + 1
                break
        messages.insert(insert_idx, ctx_message)

    except Exception:  # noqa: BLE001
        pass


def _serialize_tool_result(result: Any) -> str:
    """Serialize tool output into a stable string for the tool message."""
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


def _tool_error_payload(tool_name: str, message: str, **extra: Any) -> str:
    """Standardized JSON error payload for tool execution failures."""
    return json.dumps(
        {"ok": False, "tool": tool_name, "error": message, **extra},
        ensure_ascii=False,
        default=str,
    )


# ── Agent ─────────────────────────────────────────────────────────────────────


class ScholarForgeAgent:
    """Agent that uses litellm tool_use to interact with the knowledge base.

    Instantiate with explicit tools, hooks, and system prompt.
    No globals, no singletons — everything is injected.
    """

    def __init__(
        self,
        model: str | None = None,
        tools: list[Callable] | None = None,
        hooks: list[LLMHook] | None = None,
        system_prompt: str = "",
        max_tokens_per_turn: int = 4096,
        temperature: float = 0.3,
        run_context: RunContext | None = None,
    ) -> None:
        self.model = model
        self.tools_list: list[Callable] = tools or []
        self.hooks: list[LLMHook] = hooks or []
        self.system_prompt = system_prompt
        self.max_tokens_per_turn = max_tokens_per_turn
        self.temperature = temperature
        self.run_context = run_context

    def run(self, prompt: str, max_turns: int = 20) -> AgentResult:
        """Execute the agent loop until LLM stops calling tools or max_turns."""
        import litellm

        from scholarforge.agent.run_context import (
            create_run_context,
            restore_run_context,
            set_current_run_context,
        )
        from scholarforge.config import settings
        from scholarforge.llm.hooks import LLMEvent

        model = self.model or settings.llm_model
        run_context = self.run_context or create_run_context(topic=prompt)

        tool_schemas = fns_to_tool_schemas(self.tools_list) if self.tools_list else []
        tool_map: dict[str, Callable] = {fn.__name__: fn for fn in self.tools_list}

        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})

        all_tool_calls: list[ToolCallRecord] = []
        all_telemetry: list[TurnTelemetry] = []
        total_in = 0
        total_out = 0
        total_cache_read = 0
        total_cache_write = 0
        token = set_current_run_context(run_context)

        try:
            for turn in range(max_turns):
                # Compact large tool results from prior turns to save tokens
                if turn > 0 and settings.enable_tool_compaction:
                    _compact_tool_results(
                        messages,
                        settings.tool_compaction_threshold,
                        run_context=run_context,
                    )

                    _session_level_compaction(messages)

                # Telemetry: measure context size at start of turn
                ctx_chars = sum(
                    len(m.get("content", "") or "") for m in messages if isinstance(m, dict)
                )
                turn_telem = TurnTelemetry(turn=turn, context_chars=ctx_chars)

                event = LLMEvent(
                    messages=messages,
                    model=model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens_per_turn,
                    attempt=turn,
                )
                for hook in self.hooks:
                    event = hook.before_call(event)

                t0 = time.time()
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": self.max_tokens_per_turn,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas

                response = litellm.completion(**kwargs)
                latency = (time.time() - t0) * 1000

                choice = response.choices[0]
                message = choice.message

                usage = response.usage
                input_tokens: int = usage.prompt_tokens if usage else 0
                output_tokens: int = usage.completion_tokens if usage else 0
                cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write: int = getattr(usage, "cache_creation_input_tokens", 0) or 0
                total_in += input_tokens
                total_out += output_tokens
                total_cache_read += cache_read
                total_cache_write += cache_write

                turn_telem.input_tokens = input_tokens
                turn_telem.output_tokens = output_tokens
                turn_telem.latency_ms = latency

                event.raw_response = message.content or ""
                event.input_tokens = input_tokens
                event.output_tokens = output_tokens
                event.latency_ms = latency
                for hook in self.hooks:
                    event = hook.after_call(event)

                # Append the assistant message - convert to dict if needed
                try:
                    msg_dict = message.model_dump()
                except AttributeError:
                    msg_dict = {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": message.tool_calls,
                    }
                messages.append(msg_dict)

                # No tool calls - agent is done
                if not message.tool_calls:
                    all_telemetry.append(turn_telem)
                    return AgentResult(
                        content=message.content or "",
                        tool_calls=all_tool_calls,
                        total_turns=turn + 1,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        total_cache_read_tokens=total_cache_read,
                        total_cache_write_tokens=total_cache_write,
                        turn_telemetry=all_telemetry,
                        messages=messages,
                        run_context=run_context,
                    )

                # Execute each tool call
                for tc in message.tool_calls:
                    fn = tool_map.get(tc.function.name)
                    duration = 0.0
                    args: dict[str, Any] = {}
                    if fn is None:
                        tool_result = _tool_error_payload(
                            tc.function.name,
                            f"Unknown tool: {tc.function.name}",
                        )
                    else:
                        try:
                            args = json.loads(tc.function.arguments)
                            t_start = time.time()
                            raw_result = fn(**args)
                            duration = (time.time() - t_start) * 1000
                            tool_result = _serialize_tool_result(raw_result)
                        except Exception as exc:
                            tool_result = _tool_error_payload(
                                tc.function.name,
                                str(exc),
                                arguments=args,
                            )
                            duration = 0.0

                    all_tool_calls.append(
                        ToolCallRecord(
                            tool_name=tc.function.name,
                            arguments=args,
                            result=tool_result[:500],
                            duration_ms=duration,
                            turn=turn,
                        )
                    )

                    turn_telem.tool_names.append(tc.function.name)
                    turn_telem.tool_durations_ms.append(duration)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_result,
                        }
                    )

                all_telemetry.append(turn_telem)

        finally:
            restore_run_context(token)

        # Max turns exhausted — return last available content
        last_content = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") in ("assistant", "tool"):
                candidate = msg.get("content") or ""
                if candidate:
                    last_content = candidate
                    break

        return AgentResult(
            content=last_content,
            tool_calls=all_tool_calls,
            total_turns=max_turns,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            total_cache_read_tokens=total_cache_read,
            total_cache_write_tokens=total_cache_write,
            turn_telemetry=all_telemetry,
            messages=messages,
            run_context=run_context,
        )

    def run_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        max_turns: int = 20,
        max_retries: int = 2,
    ) -> BaseModel:
        """Run agent loop, then validate final output against Pydantic model."""
        from scholarforge.llm.client import LLMOutputError, _extract_json, schema_to_prompt

        schema_inst = schema_to_prompt(response_model)
        full_prompt = f"{prompt}\n\n{schema_inst}"

        result = self.run(full_prompt, max_turns=max_turns)

        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            parsed = _extract_json(result.content)
            if parsed is not None:
                try:
                    return response_model.model_validate(parsed)
                except Exception as exc:
                    last_error = exc
                    if attempt < max_retries:
                        result = self.run(
                            f"Your output failed validation: {exc}\n"
                            "Please fix and return valid JSON.",
                            max_turns=1,
                        )
                        continue
                    raise LLMOutputError([str(exc)], result.content) from exc
            else:
                last_error = ValueError(f"No JSON found in output: {result.content[:200]}")
                if attempt < max_retries:
                    result = self.run(
                        "Your previous response did not contain valid JSON. "
                        "Please return only a valid JSON object.",
                        max_turns=1,
                    )
                    continue
                raise LLMOutputError(
                    [str(last_error)],
                    result.content,
                ) from last_error

        # Should not be reached, but satisfy type checker
        raise LLMOutputError(
            [str(last_error) if last_error else "Unknown error"],
            result.content,
        )
