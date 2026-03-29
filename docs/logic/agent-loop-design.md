# Agent Loop Design

## The Two Interfaces

ScholarForge's knowledge base functions serve two interfaces:

```
┌──────────────────────────────┐
│  Knowledge Base Functions    │
│  (list_papers, deep_read,   │
│   search_papers, etc.)       │
├──────────────┬───────────────┤
│  MCP Server  │  Agent Loop   │
│  (external)  │  (internal)   │
│              │               │
│  Claude Code │  litellm +    │
│  Cursor      │  tool_use     │
│  Any client  │               │
└──────────────┴───────────────┘
```

**MCP Server**: For external LLM clients. They bring their own LLM.
**Agent Loop**: For internal generation and testing. We control the LLM.

Both call the same underlying Python functions. No code duplication.

## Agent Loop Architecture

```python
class ScholarForgeAgent:
    """Internal agent that uses litellm tool_use to interact with the KB."""

    def __init__(self, model: str, tools: list[Callable], hooks: list[LLMHook]):
        self.model = model
        self.tools = {fn.__name__: fn for fn in tools}
        self.tool_schemas = [fn_to_schema(fn) for fn in tools]
        self.hooks = hooks
        self.messages = []

    def run(self, prompt: str, max_turns: int = 20) -> str:
        """Run the agent loop until the LLM stops calling tools."""
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]

        for turn in range(max_turns):
            response = litellm.completion(
                model=self.model,
                messages=self.messages,
                tools=self.tool_schemas,
            )

            message = response.choices[0].message
            self.messages.append(message)

            if not message.tool_calls:
                return message.content  # LLM is done

            # Execute tool calls
            for tool_call in message.tool_calls:
                fn = self.tools[tool_call.function.name]
                args = json.loads(tool_call.function.arguments)
                result = fn(**args)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })

        return self.messages[-1].get("content", "")
```

## Tool Registration

Same functions power both MCP and the agent loop:

```python
# In mcp_server.py (for external clients)
@mcp.tool()
def list_papers(limit=None):
    return _list_papers_impl(limit)

# In agent.py (for internal loop)
agent = ScholarForgeAgent(
    model="claude-sonnet-4-20250514",
    tools=[_list_papers_impl, _search_papers_impl, _deep_read_impl, ...],
)
```

## Testing with LLM in the Loop

```python
# test_agent_integration.py
def test_agent_writes_introduction():
    agent = ScholarForgeAgent(
        model="claude-haiku-4-5-20251001",  # cheap for testing
        tools=[list_papers, get_graph_metrics, deep_read, search_papers],
    )

    result = agent.run(
        "Read the corpus and write a literature review introduction "
        "on ALD-based memristors for neuromorphic computing."
    )

    assert len(result) > 500
    assert "memristor" in result.lower()
    assert "[REF:" in result or "[1]" in result
```

No MCP protocol, no subprocess, no restart. Direct function calls.
Uses a real LLM (haiku for cost). Runs in seconds.

## Benefits

- Test with real LLM in the loop (no mocks)
- No MCP restart pain
- Same functions, two interfaces
- Cost-controlled (haiku for tests, sonnet/opus for production)
- Full hook support (cost tracking, token budget, quality gates)
- Deterministic tool execution (same DB, same functions)
