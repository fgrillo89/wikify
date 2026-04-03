# LLM Interaction Patterns for ScholarForge

Design patterns extracted from six frameworks (Instructor, DSPy, LangChain,
Cheshire Cat, Marvin, Outlines). Zero new dependencies — all implementable
with Pydantic + Python stdlib.

---

## Pattern 1: Schema-First Output Contracts (Instructor + Marvin)

**Problem today**: `complete_json()` returns `dict | list` — the caller manually
picks keys with `.get()`, no validation, no autocomplete, silent failures when
the LLM invents new keys or drops required ones.

**Pattern**: Define a Pydantic model for every LLM output. The model IS the
contract. Prompt includes the JSON schema. Response is parsed into the model.
If parsing fails, we know immediately and can retry.

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal


class PlannedSection(BaseModel):
    heading: str = Field(..., min_length=3)
    level: Literal[1, 2, 3] = 1
    description: str = Field(..., min_length=10)
    target_tokens: int = Field(..., gt=50, lt=5000)
    source_papers: list[str] = Field(default_factory=list, min_length=1)

    @field_validator("heading")
    @classmethod
    def no_numbered_prefix(cls, v: str) -> str:
        """LLMs love to add '1.' prefixes — strip them."""
        import re
        return re.sub(r"^\d+[\.\)]\s*", "", v)


class PaperPlanOutput(BaseModel):
    """Schema the LLM must produce for paper planning."""
    title: str = Field(..., min_length=5, max_length=200)
    paper_type: Literal["lit_review", "research", "grant_proposal", "abstract"]
    target_length: int = Field(..., gt=500)
    sections: list[PlannedSection] = Field(..., min_length=3)

    @field_validator("sections")
    @classmethod
    def must_have_intro_and_conclusion(cls, v: list[PlannedSection]) -> list[PlannedSection]:
        headings_lower = [s.heading.lower() for s in v]
        if not any("intro" in h for h in headings_lower):
            raise ValueError("Plan must include an Introduction section")
        if not any("conclu" in h for h in headings_lower):
            raise ValueError("Plan must include a Conclusion section")
        return v
```

**How prompts use it**: Auto-generate format instructions from the schema.

```python
def schema_to_prompt_instructions(model_cls: type[BaseModel]) -> str:
    """Convert a Pydantic model to LLM-friendly format instructions."""
    schema = model_cls.model_json_schema()
    import json
    return (
        "Return a JSON object conforming to this schema:\n"
        f"```json\n{json.dumps(schema, indent=2)}\n```\n"
        "Return ONLY valid JSON. No markdown fences, no commentary."
    )
```

**Applied to**: `plan_paper()`, `plan_slides()`, hub-spoke synthesis output.

---

## Pattern 2: Validate-and-Retry Loop (Instructor + DSPy Assertions)

**Problem today**: `complete_json()` tries to parse JSON and raises `ValueError`
on failure. No retry, no error feedback to the LLM, no structured validation
beyond "is it valid JSON?".

**Pattern**: Wrap every structured LLM call in a retry loop that catches
`ValidationError`, appends the error message to the conversation, and re-prompts.
DSPy calls this "backtracking" — the key insight is feeding the *specific*
validation failure back as context.

```python
from pydantic import BaseModel, ValidationError
import json


class LLMOutputError(Exception):
    """Raised when LLM output fails validation after all retries."""
    def __init__(self, errors: list[str], raw_output: str):
        self.errors = errors
        self.raw_output = raw_output
        super().__init__(f"LLM output invalid after retries: {errors}")


def complete_structured(
    messages: list[dict[str, str]],
    response_model: type[BaseModel],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_retries: int = 2,
) -> BaseModel:
    """Send a completion request and validate against a Pydantic model.

    On validation failure, appends the error to the conversation and retries.
    Inspired by Instructor's automatic retry and DSPy's assertion backtracking.
    """
    from wikify.llm.client import complete

    # Inject schema instructions into the system message
    schema_instructions = schema_to_prompt_instructions(response_model)
    enriched_messages = _inject_schema(messages, schema_instructions)

    errors_so_far: list[str] = []

    for attempt in range(max_retries + 1):
        raw = complete(
            messages=enriched_messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_cache=(attempt == 0),  # only cache first attempt
        )

        # Parse JSON from raw text
        parsed = _extract_json(raw)
        if parsed is None:
            error_msg = f"Could not extract valid JSON from response: {raw[:200]}"
            errors_so_far.append(error_msg)
            enriched_messages = _append_retry_context(
                enriched_messages, raw, error_msg
            )
            continue

        # Validate against Pydantic model
        try:
            return response_model.model_validate(parsed)
        except ValidationError as e:
            error_msg = str(e)
            errors_so_far.append(error_msg)
            enriched_messages = _append_retry_context(
                enriched_messages, raw, error_msg
            )

    raise LLMOutputError(errors_so_far, raw)


def _append_retry_context(
    messages: list[dict], raw_output: str, error: str
) -> list[dict]:
    """DSPy-style backtracking: feed the failed output + error back."""
    return messages + [
        {"role": "assistant", "content": raw_output},
        {"role": "user", "content": (
            f"Your previous output failed validation:\n{error}\n\n"
            "Please fix the errors and return valid JSON."
        )},
    ]
```

**Applied to**: Every `complete_json()` call site gets replaced with
`complete_structured()`.

---

## Pattern 3: Prompt Templates with Typed Slots (DSPy Signatures + LangChain)

**Problem today**: Prompts are f-strings scattered across modules. Impossible to
version, test, or audit. Easy to introduce injection bugs.

**Pattern**: Declare prompts as classes with typed input slots and a fixed
template. DSPy calls these "signatures" — we use a lighter version that is just
a dataclass holding the template + metadata.

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptTemplate:
    """A versioned, testable prompt template."""
    name: str
    version: str
    system: str
    user: str
    input_fields: dict[str, type] = field(default_factory=dict)

    def render(self, **kwargs: Any) -> list[dict[str, str]]:
        """Render to messages list, validating all slots are filled."""
        missing = set(self.input_fields) - set(kwargs)
        if missing:
            raise ValueError(f"Missing template fields: {missing}")
        extra = set(kwargs) - set(self.input_fields)
        if extra:
            raise ValueError(f"Unexpected template fields: {extra}")
        return [
            {"role": "system", "content": self.system.format(**kwargs)},
            {"role": "user", "content": self.user.format(**kwargs)},
        ]


# ── Concrete templates ──────────────────────────────────────────────

PLAN_PAPER = PromptTemplate(
    name="plan_paper",
    version="2.0",
    system=(
        "{persona}\n\n"
        "Given a writing prompt and source papers, create a detailed outline "
        "for a {artifact_name}.\n\n"
        "{schema_instructions}\n\n"
        "{section_guidance}\n"
        "{type_hint}\n"
        "Distribute the target word count across sections proportionally."
    ),
    user=(
        "Prompt: {user_prompt}\n\n"
        "Available papers:\n{paper_list}\n\n"
        "{graph_section}"
    ),
    input_fields={
        "persona": str,
        "artifact_name": str,
        "schema_instructions": str,
        "section_guidance": str,
        "type_hint": str,
        "user_prompt": str,
        "paper_list": str,
        "graph_section": str,
    },
)


WRITE_SECTION = PromptTemplate(
    name="write_section",
    version="2.0",
    system=(
        "{persona}\n\n"
        "Write the following section of a review paper based on the "
        "literature provided. Cite sources using [REF:display_name] markers.\n"
        "Be precise, technical, and thorough.\n"
        "Do NOT include the section heading.\n"
        "Target approximately {target_tokens} words.\n"
        "{figure_instruction}\n"
        "After drafting, self-revise: check for banned words, "
        "nominalizations, passive voice overuse, vague quantifiers."
    ),
    user=(
        "Paper title: {paper_title}\n"
        "Section: {section_heading}\n"
        "Section description: {section_description}\n"
        "{source_hint}\n\n"
        "--- Previously written sections ---\n{prior_sections}\n\n"
        "--- Literature context ---\n{lit_context}"
    ),
    input_fields={
        "persona": str,
        "target_tokens": str,
        "figure_instruction": str,
        "paper_title": str,
        "section_heading": str,
        "section_description": str,
        "source_hint": str,
        "prior_sections": str,
        "lit_context": str,
    },
)


HUB_SYNTHESIS = PromptTemplate(
    name="hub_synthesis",
    version="1.0",
    system="You are a research subagent exploring a hub paper and its neighborhood.",
    user=(
        "Hub paper: {hub_title} ({hub_authors}, {hub_year})\n\n"
        "Produce a DENSE synthesis (200-300 words max):\n"
        "1. Hypothesis -> Test -> Result\n"
        "2. State of the art\n"
        "3. Pitfalls and limitations\n"
        "4. Conclusions and open questions\n"
        "5. Reading recommendations: READ IN FULL / SKIM / SKIP\n\n"
        "{focus_instruction}\n\n"
        "{schema_instructions}\n\n"
        "--- Excerpts ---\n{excerpts}"
    ),
    input_fields={
        "hub_title": str,
        "hub_authors": str,
        "hub_year": str,
        "focus_instruction": str,
        "schema_instructions": str,
        "excerpts": str,
    },
)
```

**Benefits**: Prompts live in one place, are version-tagged, can be unit-tested
with `.render()`, and never have unset format slots at runtime.

---

## Pattern 4: Lifecycle Hooks / Middleware (Cheshire Cat + LangChain)

**Problem today**: Cross-cutting concerns (token counting, cost tracking,
logging, rate limiting) would require modifying `complete()` directly. No way
for callers to observe or transform LLM I/O without coupling.

**Pattern**: A simple hook/event system around the LLM call boundary. Cheshire
Cat uses `@hook` decorators on named events. We use a lighter callback protocol.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class LLMEvent:
    """Data passed through the hook pipeline."""
    messages: list[dict[str, str]]
    model: str
    temperature: float
    max_tokens: int
    raw_response: str | None = None
    parsed_output: object | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0
    attempt: int = 0       # retry attempt number
    cached: bool = False


@runtime_checkable
class LLMHook(Protocol):
    """Protocol for LLM lifecycle hooks."""
    def before_call(self, event: LLMEvent) -> LLMEvent:
        """Transform or inspect the event before the LLM call."""
        ...

    def after_call(self, event: LLMEvent) -> LLMEvent:
        """Transform or inspect the event after the LLM call."""
        ...


class TokenBudgetHook:
    """Enforce a token budget across a pipeline run."""
    def __init__(self, budget: int):
        self.budget = budget
        self.spent = 0

    def before_call(self, event: LLMEvent) -> LLMEvent:
        if self.spent >= self.budget:
            raise RuntimeError(
                f"Token budget exhausted: {self.spent}/{self.budget}"
            )
        return event

    def after_call(self, event: LLMEvent) -> LLMEvent:
        self.spent += event.input_tokens + event.output_tokens
        return event


class CostTrackerHook:
    """Accumulate estimated cost across all LLM calls in a run."""
    # Approximate per-token pricing (USD)
    PRICING = {
        "claude-sonnet-4-20250514": (3.0e-6, 15.0e-6),
        "claude-haiku-3.5": (0.25e-6, 1.25e-6),
    }

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.calls: list[dict] = []

    def before_call(self, event: LLMEvent) -> LLMEvent:
        return event

    def after_call(self, event: LLMEvent) -> LLMEvent:
        self.total_input_tokens += event.input_tokens
        self.total_output_tokens += event.output_tokens
        in_price, out_price = self.PRICING.get(event.model, (3.0e-6, 15.0e-6))
        call_cost = event.input_tokens * in_price + event.output_tokens * out_price
        self.total_cost_usd += call_cost
        self.calls.append({
            "model": event.model,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "cost_usd": call_cost,
            "cached": event.cached,
        })
        return event

    def summary(self) -> str:
        return (
            f"LLM calls: {len(self.calls)}, "
            f"tokens: {self.total_input_tokens}+{self.total_output_tokens}, "
            f"est. cost: ${self.total_cost_usd:.4f}"
        )
```

**Integration**: `complete()` accepts `hooks: list[LLMHook] = []` and runs
`before_call` / `after_call` around the litellm call.

---

## Pattern 5: Semantic Guardrails via Validators (Instructor + Outlines)

**Problem today**: LLM output quality is unchecked. The writer might produce
sections that are too short, miss citation markers, or hallucinate paper names
not in the context.

**Pattern**: Pydantic validators that encode *domain-specific* constraints.
Instructor calls these "field validators". DSPy calls them "assertions".
Outlines enforces them at generation time. Since we use API-based LLMs, we
enforce at parse time with retry.

```python
import re
from pydantic import BaseModel, Field, field_validator, model_validator


class SectionOutput(BaseModel):
    """Validated output from the section writer."""
    content: str = Field(..., min_length=100)
    citations_found: list[str] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def minimum_word_count(cls, v: str) -> str:
        words = len(v.split())
        if words < 50:
            raise ValueError(
                f"Section has only {words} words; minimum is 50. "
                "Expand the section with more detail."
            )
        return v

    @field_validator("content")
    @classmethod
    def must_contain_citations(cls, v: str) -> str:
        refs = re.findall(r"\[REF:[^\]]+\]", v)
        if len(refs) < 1:
            raise ValueError(
                "Section must contain at least one [REF:...] citation marker. "
                "Cite the source papers."
            )
        return v

    @field_validator("content")
    @classmethod
    def no_llm_tells(cls, v: str) -> str:
        """Catch common LLM stylistic tells."""
        tells = [
            (r"\bdelve\b", "delve"),
            (r"\beverchanging\b", "everchanging"),
            (r"\bIt is worth noting\b", "It is worth noting"),
            (r"\bIn conclusion,\b", "In conclusion,"),  # mid-section only
        ]
        found = [name for pattern, name in tells if re.search(pattern, v, re.IGNORECASE)]
        if found:
            raise ValueError(
                f"Remove LLM stylistic tells: {', '.join(found)}. "
                "Rephrase these passages."
            )
        return v

    @model_validator(mode="after")
    def extract_citations(self):
        """Populate citations_found from content for downstream use."""
        self.citations_found = re.findall(r"\[REF:([^\]]+)\]", self.content)
        return self


class HubSynthesisOutput(BaseModel):
    """Structured output for hub-spoke synthesis."""
    summary: str = Field(..., min_length=100, max_length=2000)
    read_in_full: list[str] = Field(default_factory=list)
    skim: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("read_in_full")
    @classmethod
    def at_least_one_deep_read(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("Must recommend at least one paper for deep reading.")
        return v


class ReferenceMatchResult(BaseModel):
    """Validated result from reference resolution."""
    paper_id: str
    display_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    match_reason: str = ""

    @field_validator("confidence")
    @classmethod
    def reject_low_confidence(cls, v: float) -> float:
        if v < 0.3:
            raise ValueError(
                f"Match confidence {v:.2f} is below threshold 0.3. "
                "This match is too uncertain."
            )
        return v
```

**Applied to**: Section writing gets `SectionOutput`, hub synthesis gets
`HubSynthesisOutput`, reference matching gets `ReferenceMatchResult`.

---

## Pattern 6: Pipeline Composition with Typed Steps (DSPy Modules + LangChain LCEL)

**Problem today**: The generation pipeline (`plan_paper` -> `write_paper` ->
`resolve_references`) is implicitly chained through function calls. No way to
introspect the pipeline, swap steps, or track progress uniformly.

**Pattern**: Define each pipeline step as a typed callable with explicit
input/output models. DSPy uses `Module` classes with `forward()`. LangChain
uses the `Runnable` protocol with `|` composition. We use a simpler approach:
typed step functions registered in a pipeline.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, Generic
from pydantic import BaseModel

T_In = TypeVar("T_In", bound=BaseModel)
T_Out = TypeVar("T_Out", bound=BaseModel)


@dataclass
class StepResult(Generic[T_Out]):
    """Result of a single pipeline step."""
    output: T_Out
    step_name: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    retries: int = 0


@dataclass
class Pipeline:
    """A sequence of typed LLM steps with shared hooks."""
    name: str
    steps: list[Callable] = field(default_factory=list)
    hooks: list[LLMHook] = field(default_factory=list)

    def add_step(self, fn: Callable) -> "Pipeline":
        self.steps.append(fn)
        return self

    def run(self, initial_input: Any) -> list[StepResult]:
        """Execute all steps sequentially, threading output -> input."""
        results: list[StepResult] = []
        current = initial_input
        for step_fn in self.steps:
            result = step_fn(current, hooks=self.hooks)
            results.append(result)
            current = result.output
        return results


# Usage: composing the paper generation pipeline
def build_paper_pipeline(hooks: list[LLMHook] | None = None) -> Pipeline:
    return Pipeline(
        name="paper_generation",
        hooks=hooks or [],
    ).add_step(plan_step).add_step(write_step).add_step(resolve_step)
```

This is the lightest-touch pattern. The main value is making step boundaries
explicit and hooks composable. NOT about abstracting away the LLM call.

---

## Implementation Priority

| Priority | Pattern | Effort | Impact |
|----------|---------|--------|--------|
| **P0** | Schema-First Output Contracts | Low | High — catches bugs, enables autocomplete |
| **P0** | Validate-and-Retry Loop | Low | High — single biggest reliability gain |
| **P1** | Prompt Templates | Medium | Medium — testability, auditability |
| **P1** | Lifecycle Hooks (cost tracker) | Medium | Medium — visibility into spend |
| **P2** | Semantic Guardrails | Low | Medium — catches quality regressions |
| **P2** | Pipeline Composition | Medium | Low until we have more artifact types |

P0 items can be implemented in `llm/client.py` and `store/models.py` without
changing any caller signatures. The `complete_structured()` function is a
drop-in addition alongside the existing `complete()` and `complete_json()`.

---

## Migration Path

1. Add `complete_structured()` to `llm/client.py` (new function, no breakage)
2. Define output models in `store/models.py` or a new `llm/schemas.py`
3. Migrate `plan_paper()` first (clearest schema, easiest to test)
4. Add `CostTrackerHook` to get spend visibility immediately
5. Migrate `_write_section()` with `SectionOutput` validators
6. Extract prompt strings into `PromptTemplate` instances in `generate/prompts.py`
7. Migrate hub-spoke synthesis to `HubSynthesisOutput`

Each step is independently deployable and backward-compatible.
