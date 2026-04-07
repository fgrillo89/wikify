"""Agent-native extractor protocol for discovery nodes.

Wikify is an agentic application: the orchestrating agent (the LLM
runtime that drives the workflow) IS the model. Discovery nodes do not
call any LLM SDK directly. Instead, the extract nodes accept an
``AgentExtractor`` whose implementation is supplied by the runtime:

- in production, the runtime binds an extractor that dispatches to the
  driving agent (subagent calls, tool calls, or direct reasoning over
  the unit payload)
- in tests and dry runs, ``EchoExtractor`` returns deterministic
  structured work-items so the DAG, scheduler, and persistence layers
  can be exercised without invoking any model

This keeps the discovery subsystem framework-neutral and free of any
provider SDK dependency.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Protocol

from wikify.wiki.discovery.contracts import (
    ExtractionNote,
    ExtractionUnit,
    ModalityKind,
)


class AgentExtractor(Protocol):
    """Contract a runtime fulfills to turn extraction units into notes.

    The agent decides how to interrogate each unit. Implementations may
    batch, escalate to a stronger reasoner, or fan out across subagents.
    """

    def extract(
        self,
        units: Iterable[ExtractionUnit],
        *,
        strategy_id: str,
        node_id: str,
        modalities: tuple[ModalityKind, ...] | None = None,
    ) -> list[ExtractionNote]: ...


class EchoExtractor:
    """Deterministic extractor that emits a structured work-item per unit.

    Used for tests, dry runs, and DAG smoke coverage. The notes it emits
    record exactly which units would have been handed to the agent and
    what modality routing they implied. This is **not** a fake model: no
    natural-language synthesis is invented.
    """

    def __init__(self, *, agent_label: str = "pending-agent") -> None:
        self.agent_label = agent_label

    def extract(
        self,
        units: Iterable[ExtractionUnit],
        *,
        strategy_id: str,
        node_id: str,
        modalities: tuple[ModalityKind, ...] | None = None,
    ) -> list[ExtractionNote]:
        out: list[ExtractionNote] = []
        now = time.time()
        for u in units:
            if modalities and u.modality not in modalities:
                continue
            out.append(
                ExtractionNote(
                    note_id=f"{u.unit_id}:{node_id}",
                    document_id=u.document_id,
                    unit_ids=[u.unit_id],
                    strategy_id=strategy_id,
                    node_id=node_id,
                    content={
                        "work_item": {
                            "unit_kind": u.kind.value,
                            "modality": u.modality.value,
                            "section": u.section,
                            "weight": u.weight,
                        },
                        "agent": self.agent_label,
                        "status": "pending-agent",
                    },
                    confidence=0.0,
                    model=self.agent_label,
                    created_at=now,
                )
            )
        return out
