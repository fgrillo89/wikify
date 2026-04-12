# LLM Interaction Layer — Design

## Principle

Pydantic models define every LLM input/output contract. A single `complete_structured()`
function handles schema injection, validation, and retry. No frameworks — just Pydantic +
litellm (already a dependency).

## Architecture

```
Caller (planner, writer, hub-spoke)
    │
    │  passes: messages + response_model (Pydantic class)
    │
    ▼
complete_structured()          ← NEW: the core structured call
    │
    ├── inject schema into system prompt
    ├── call litellm via complete()
    ├── parse response → Pydantic model
    ├── on ValidationError → append error + retry (max 2)
    ├── run hooks (cost tracking, token budget)
    │
    ▼
Validated Pydantic object (or LLMOutputError)
```

## Components

### 1. Output schemas (`llm/schemas.py`) — NEW

Pydantic models for every LLM interaction:
- `PaperPlanOutput` — plan with validated sections
- `SectionOutput` — section text with citation/quality validators
- `HubSynthesisOutput` — structured hub-spoke synthesis
- `ChatResponse` — RAG chat with source attribution

### 2. `complete_structured()` (`llm/client.py`) — ADD

```python
def complete_structured(
    messages: list[dict],
    response_model: type[BaseModel],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_retries: int = 2,
    hooks: list[LLMHook] | None = None,
) -> BaseModel:
```

Core behavior:
- Auto-appends JSON schema from `response_model.model_json_schema()` to system prompt
- Calls `complete()` (which handles litellm + caching)
- Parses response into Pydantic model
- On `ValidationError`: appends error message to conversation, retries
- Runs `before_call`/`after_call` hooks on each attempt
- Raises `LLMOutputError` after max retries

### 3. Hooks (`llm/hooks.py`) — NEW

```python
class LLMHook(Protocol):
    def before_call(self, event: LLMEvent) -> LLMEvent: ...
    def after_call(self, event: LLMEvent) -> LLMEvent: ...

# Concrete hooks:
class CostTracker(LLMHook): ...      # accumulates USD cost estimate
class TokenBudget(LLMHook): ...      # hard cap on total tokens per run
class CallLogger(LLMHook): ...       # logs every call for debugging
```

### 4. Prompt templates (`generate/prompts.py`) — NEW

Frozen dataclasses with typed slots. Replace scattered f-strings in planner.py,
writer.py, hub_spoke.py. Each template has a name, version, and `render()` method
that validates all slots are filled.

## Migration (incremental, zero breakage)

1. Add `llm/schemas.py` + `llm/hooks.py` (new files, nothing changes)
2. Add `complete_structured()` to `llm/client.py` (alongside existing functions)
3. Migrate `plan_paper()` → use `PaperPlanOutput` schema
4. Add `CostTracker` hook for spend visibility
5. Migrate `_write_section()` → use `SectionOutput` with quality validators
6. Migrate hub-spoke → use `HubSynthesisOutput`
7. Extract prompts into `generate/prompts.py`

Each step is a separate commit. Existing `complete()` and `complete_json()` stay
for backward compatibility until all callers are migrated.

## Why not Instructor/PydanticAI?

- litellm already handles multi-provider routing (OpenAI, Anthropic, Ollama)
- `complete_structured()` is ~60 lines of code — not worth a dependency
- Native provider structured outputs (OpenAI/Anthropic) can be used via litellm
  when available, with our retry logic as fallback
- Keeping it simple means fewer things to break
