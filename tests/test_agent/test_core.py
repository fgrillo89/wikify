"""Unit tests for ScholarForgeAgent — all litellm calls are mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from scholarforge.agent.core import AgentResult, ScholarForgeAgent

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_message(content: str | None = None, tool_calls=None) -> MagicMock:
    """Build a mock litellm message object."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    msg.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls or [],
    }
    return msg


def _make_tool_call(name: str, arguments: dict, call_id: str = "call_1") -> MagicMock:
    """Build a mock tool_call object as litellm returns it."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


def _make_response(
    content: str | None = None,
    tool_calls=None,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> MagicMock:
    """Build a mock litellm completion response."""
    msg = _make_message(content=content, tool_calls=tool_calls)

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    choice = MagicMock()
    choice.message = msg

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAgentNoTools:
    """Agent with no tools: single completion, no tool calls."""

    def test_returns_content(self):
        agent = ScholarForgeAgent(model="test-model")
        resp = _make_response(content="Hello, world!")

        with patch("litellm.completion", return_value=resp) as mock_llm:
            result = agent.run("Say hello")

        assert isinstance(result, AgentResult)
        assert result.content == "Hello, world!"
        assert result.total_turns == 1
        assert result.tool_calls == []
        mock_llm.assert_called_once()

    def test_messages_include_user_prompt(self):
        agent = ScholarForgeAgent(model="test-model")
        resp = _make_response(content="Done")

        with patch("litellm.completion", return_value=resp):
            result = agent.run("My question")

        roles = [m["role"] for m in result.messages]
        assert "user" in roles
        assert "assistant" in roles

    def test_system_prompt_included(self):
        agent = ScholarForgeAgent(model="test-model", system_prompt="You are helpful.")
        resp = _make_response(content="Sure")

        with patch("litellm.completion", return_value=resp) as mock_llm:
            agent.run("Hello")

        call_messages = mock_llm.call_args.kwargs["messages"]
        assert call_messages[0]["role"] == "system"
        assert "helpful" in call_messages[0]["content"]


class TestAgentSingleToolCall:
    """Agent that executes one tool call then finishes."""

    def test_tool_called_with_correct_args(self):
        def my_tool(query: str) -> str:
            """Search the database."""
            return f"results for {query}"

        tool_call = _make_tool_call("my_tool", {"query": "ALD memristors"})
        first_resp = _make_response(tool_calls=[tool_call])
        second_resp = _make_response(content="Here is the final answer.")

        agent = ScholarForgeAgent(model="test-model", tools=[my_tool])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Find papers on ALD memristors")

        assert result.content == "Here is the final answer."
        assert len(result.tool_calls) == 1
        record = result.tool_calls[0]
        assert record.tool_name == "my_tool"
        assert record.arguments == {"query": "ALD memristors"}
        assert "ALD memristors" in record.result

    def test_tool_result_appended_to_messages(self):
        def echo(text: str) -> str:
            return text

        tool_call = _make_tool_call("echo", {"text": "ping"})
        first_resp = _make_response(tool_calls=[tool_call])
        second_resp = _make_response(content="Done")

        agent = ScholarForgeAgent(model="test-model", tools=[echo])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Echo ping")

        tool_messages = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["content"] == "ping"


class TestAgentMaxTurns:
    """Agent respects max_turns limit."""

    def test_stops_at_max_turns(self):
        def dummy_tool() -> str:
            return "result"

        # Always returns a tool call — never terminates naturally
        tool_call = _make_tool_call("dummy_tool", {})
        always_tool_resp = _make_response(tool_calls=[tool_call])

        agent = ScholarForgeAgent(model="test-model", tools=[dummy_tool])

        with patch("litellm.completion", return_value=always_tool_resp):
            result = agent.run("Go", max_turns=3)

        assert result.total_turns == 3


class TestAgentHooks:
    """Hooks are invoked for each LLM call."""

    def test_hooks_called(self):
        mock_hook = MagicMock()
        mock_hook.before_call.side_effect = lambda e: e
        mock_hook.after_call.side_effect = lambda e: e

        agent = ScholarForgeAgent(model="test-model", hooks=[mock_hook])
        resp = _make_response(content="Hi")

        with patch("litellm.completion", return_value=resp):
            agent.run("Hello")

        mock_hook.before_call.assert_called_once()
        mock_hook.after_call.assert_called_once()

    def test_hooks_called_each_turn(self):
        mock_hook = MagicMock()
        mock_hook.before_call.side_effect = lambda e: e
        mock_hook.after_call.side_effect = lambda e: e

        def dummy_tool() -> str:
            return "ok"

        tool_call = _make_tool_call("dummy_tool", {})
        first_resp = _make_response(tool_calls=[tool_call])
        second_resp = _make_response(content="Done")

        agent = ScholarForgeAgent(model="test-model", tools=[dummy_tool], hooks=[mock_hook])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            agent.run("Go")

        # One call per LLM turn
        assert mock_hook.before_call.call_count == 2
        assert mock_hook.after_call.call_count == 2


class TestAgentUnknownTool:
    """Unknown tool names return an error payload — not an exception."""

    def test_unknown_tool_returns_error_result(self):
        tool_call = _make_tool_call("nonexistent_tool", {"x": 1})
        first_resp = _make_response(tool_calls=[tool_call])
        second_resp = _make_response(content="I handled the error.")

        agent = ScholarForgeAgent(model="test-model", tools=[])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Call something weird")

        assert len(result.tool_calls) == 1
        record = result.tool_calls[0]
        error_payload = json.loads(record.result)
        assert "error" in error_payload
        assert "nonexistent_tool" in error_payload["error"]

    def test_unknown_tool_does_not_raise(self):
        tool_call = _make_tool_call("ghost_fn", {})
        first_resp = _make_response(tool_calls=[tool_call])
        second_resp = _make_response(content="OK")

        agent = ScholarForgeAgent(model="test-model")

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Try it")  # Should not raise

        assert result.content == "OK"


class TestAgentTokenTracking:
    """Token counts are accumulated across all turns."""

    def test_token_counts_single_turn(self):
        resp = _make_response(content="Done", prompt_tokens=50, completion_tokens=30)
        agent = ScholarForgeAgent(model="test-model")

        with patch("litellm.completion", return_value=resp):
            result = agent.run("Hello")

        assert result.total_input_tokens == 50
        assert result.total_output_tokens == 30

    def test_token_counts_two_turns(self):
        def dummy_tool() -> str:
            return "data"

        tool_call = _make_tool_call("dummy_tool", {})
        first_resp = _make_response(tool_calls=[tool_call], prompt_tokens=40, completion_tokens=10)
        second_resp = _make_response(content="Final", prompt_tokens=60, completion_tokens=20)

        agent = ScholarForgeAgent(model="test-model", tools=[dummy_tool])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Go")

        assert result.total_input_tokens == 100  # 40 + 60
        assert result.total_output_tokens == 30  # 10 + 20

    def test_missing_usage_does_not_crash(self):
        resp = _make_response(content="OK")
        resp.usage = None

        agent = ScholarForgeAgent(model="test-model")

        with patch("litellm.completion", return_value=resp):
            result = agent.run("Hello")

        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0


class TestAgentToolRecordDetails:
    """ToolCallRecord captures timing and turn index."""

    def test_record_has_turn_index(self):
        def tool_a() -> str:
            return "a"

        def tool_b() -> str:
            return "b"

        tc_a = _make_tool_call("tool_a", {}, call_id="c1")
        tc_b = _make_tool_call("tool_b", {}, call_id="c2")

        # Turn 0: both tool_a and tool_b called together
        first_resp = _make_response(tool_calls=[tc_a, tc_b])
        second_resp = _make_response(content="All done")

        agent = ScholarForgeAgent(model="test-model", tools=[tool_a, tool_b])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Go")

        assert len(result.tool_calls) == 2
        for record in result.tool_calls:
            assert record.turn == 0

    def test_record_result_truncated_at_500(self):
        long_output = "x" * 1000

        def verbose_tool() -> str:
            return long_output

        tc = _make_tool_call("verbose_tool", {})
        first_resp = _make_response(tool_calls=[tc])
        second_resp = _make_response(content="OK")

        agent = ScholarForgeAgent(model="test-model", tools=[verbose_tool])

        with patch("litellm.completion", side_effect=[first_resp, second_resp]):
            result = agent.run("Run")

        assert len(result.tool_calls[0].result) <= 500
