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
class AgentResult:
    """Result of an agent run."""

    content: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    messages: list[dict] = field(default_factory=list)


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
    ) -> None:
        self.model = model
        self.tools_list: list[Callable] = tools or []
        self.hooks: list[LLMHook] = hooks or []
        self.system_prompt = system_prompt
        self.max_tokens_per_turn = max_tokens_per_turn
        self.temperature = temperature

    def run(self, prompt: str, max_turns: int = 20) -> AgentResult:
        """Execute the agent loop until LLM stops calling tools or max_turns."""
        import litellm

        from scholarforge.config import settings
        from scholarforge.llm.hooks import LLMEvent

        model = self.model or settings.llm_model

        tool_schemas = fns_to_tool_schemas(self.tools_list) if self.tools_list else []
        tool_map: dict[str, Callable] = {fn.__name__: fn for fn in self.tools_list}

        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})

        all_tool_calls: list[ToolCallRecord] = []
        total_in = 0
        total_out = 0

        for turn in range(max_turns):
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
            total_in += input_tokens
            total_out += output_tokens

            event.raw_response = message.content or ""
            event.input_tokens = input_tokens
            event.output_tokens = output_tokens
            event.latency_ms = latency
            for hook in self.hooks:
                event = hook.after_call(event)

            # Append the assistant message — convert to dict if needed
            try:
                msg_dict = message.model_dump()
            except AttributeError:
                msg_dict = {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": message.tool_calls,
                }
            messages.append(msg_dict)

            # No tool calls — agent is done
            if not message.tool_calls:
                return AgentResult(
                    content=message.content or "",
                    tool_calls=all_tool_calls,
                    total_turns=turn + 1,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                    messages=messages,
                )

            # Execute each tool call
            for tc in message.tool_calls:
                fn = tool_map.get(tc.function.name)
                duration = 0.0
                args: dict[str, Any] = {}
                if fn is None:
                    tool_result = json.dumps({"error": f"Unknown tool: {tc.function.name}"})
                else:
                    try:
                        args = json.loads(tc.function.arguments)
                        t_start = time.time()
                        raw_result = fn(**args)
                        duration = (time.time() - t_start) * 1000
                        tool_result = str(raw_result)
                    except Exception as exc:
                        tool_result = json.dumps({"error": str(exc)})
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

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    }
                )

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
            messages=messages,
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
