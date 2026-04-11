"""Tests for the LLM policy's control actions: set_tier and set_allocation."""

from wikify_simple.contracts.schema import OrchAction
from wikify_simple.distill.policy import (
    LlmPolicy,
    PolicyContext,
    PolicyRuntime,
)
from wikify_simple.distill.sampler import (
    GlobalOp,
    LevyMixSampler,
    LocalOp,
)


class _ScriptedOrchestrator:
    """Returns a preset sequence of actions on each step()."""

    def __init__(self, actions: list[OrchAction]) -> None:
        self._actions = iter(actions)

    def step(self, state) -> OrchAction:  # noqa: ARG002
        return next(self._actions)


def _sampler() -> LevyMixSampler:
    return LevyMixSampler(local_op=LocalOp.NONE, global_op=GlobalOp.UNIFORM, jump_rate=1.0)


def _ctx() -> PolicyContext:
    return PolicyContext(
        run_id="t",
        n_pages=0,
        n_candidates=0,
        n_concepts=0,
        n_people=0,
        docs_covered=0,
        docs_total=0,
    )


def test_set_tier_mutates_runtime():
    rt = PolicyRuntime()
    assert rt.write_tier == "M"
    orch = _ScriptedOrchestrator([OrchAction(name="set_tier", args={"role": "write", "tier": "L"})])
    policy = LlmPolicy(orch, _sampler(), runtime=rt)
    decision = policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert decision.action == "set_tier"
    assert decision.batch == ()
    assert rt.write_tier == "L"


def test_set_tier_locked_for_orchestrator():
    rt = PolicyRuntime()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_tier", args={"role": "orchestrate", "tier": "S"})]
    )
    policy = LlmPolicy(orch, _sampler(), runtime=rt)
    policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert rt.orchestrate_tier == "L"


def test_set_tier_rejects_invalid_tier():
    rt = PolicyRuntime()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_tier", args={"role": "extract", "tier": "Q"})]
    )
    policy = LlmPolicy(orch, _sampler(), runtime=rt)
    policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert rt.extract_tier == "S"


def test_set_allocation_mutates_runtime_and_bumps_epoch():
    rt = PolicyRuntime()
    assert rt.exploit_fraction is None
    assert rt.allocation_epoch == 0
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_allocation", args={"exploit_fraction": 0.7})]
    )
    policy = LlmPolicy(orch, _sampler(), runtime=rt)
    decision = policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert decision.action == "set_allocation"
    assert rt.exploit_fraction == 0.7
    assert rt.allocation_epoch == 1


def test_set_allocation_rejects_out_of_range():
    rt = PolicyRuntime()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_allocation", args={"exploit_fraction": 1.5})]
    )
    policy = LlmPolicy(orch, _sampler(), runtime=rt)
    policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert rt.exploit_fraction is None
    assert rt.allocation_epoch == 0
