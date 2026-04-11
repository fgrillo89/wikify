"""Shared infrastructure: cost meter, cache, context envelope, tokens.

Pure Python. No LLM, no domain logic. Import submodules directly —
``from wikify_simple.infra.cost_meter import CostMeter``. This package
intentionally does NOT re-export symbols because ``contracts/roles.py``
imports from ``infra/context_envelope`` at module load time, and
re-exports here would trigger ``infra/cost_meter`` (which imports from
``contracts/roles.py``) inside the partial-init window, creating a
cycle.
"""
