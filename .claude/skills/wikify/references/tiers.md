---
name: wikify/references/tiers
description: Model tier vocabulary (S/M/L) + haiku-equivalent cost mapping.
---

# Tiers

Three model tiers for cost telemetry: S, M, L. The mapping to actual
provider models and the per-token / per-call rates live in
``src/wikify/bundle/run/cost.py``. The ``ModelTier`` enum
(``src/wikify/types.py``) is the canonical Python type.

| Tier | Typical model class | Use for |
|---|---|---|
| S | haiku-class | extract, lightweight retrieval, triage |
| M | sonnet-class | writer, refiner |
| L | opus-class | escalation only (after M fails or for high-stakes pages) |

Cost is denominated in haiku-equivalent units: S=1.0 reference, M and
L are multiples of S per token plus per-call overhead. The exact
rates are in ``cost.py::_DEFAULT_TIERS``; an aggregator
(``cost_summary(bundle)``) rolls up totals from
``run/events.jsonl`` filtered to ``type == "call"``.

## Skill responsibility

Skills explicitly choose ``--tier`` for every model call (e.g.
``wikify draft build --tier M``). Python never picks a default tier;
this is strategy and lives in skills.
