"""Role enum and per-role context envelope spec lists.

A Role identifies which kind of model call we are making (extract a chunk,
write a page, take an orchestrator step). Each role has a fixed spec list
that the ContextEnvelope builder uses to assemble prompts within the
global TOTAL_CONTEXT cap.

These spec lists are the locked v1 constants from strategies.md.
"""

from enum import Enum

from ..infra.context_envelope import Pool, Required, SlotSpec

# --- global caps ---------------------------------------------------------

_TOTAL_CONTEXT = 128_000  # tokens; effective input cap = TOTAL - RESERVE
_RESPONSE_RESERVE = 8_000


def total_context() -> int:
    return _TOTAL_CONTEXT


def response_reserve() -> int:
    return _RESPONSE_RESERVE


# --- roles ---------------------------------------------------------------


class Role(str, Enum):
    EXTRACTOR = "extractor"
    COMPACTOR = "compactor"
    EDITOR = "editor"
    WRITER = "writer"
    ORCHESTRATOR = "orchestrator"


# --- spec lists ----------------------------------------------------------

# Pool names are stable identifiers; the strategy code populates pools by
# these names when constructing a request.

_EXTRACTOR_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=1_000),
    Required(name="target_chunk", fixed_tokens=None),  # variable
    Pool(name="canonical_titles", floor_tokens=1_000, ceiling_tokens=4_000),
]

_WRITER_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=1_000),
    Required(name="page_skeleton", fixed_tokens=None),  # variable
    Pool(name="evidence_chunks", floor_tokens=4_000, ceiling_tokens=80_000),
    Pool(name="neighbor_titles", floor_tokens=0, ceiling_tokens=8_000),
]

_COMPACTOR_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=500),
    Pool(name="dossier_entries", floor_tokens=2_000, ceiling_tokens=20_000),
]

_EDITOR_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=1_000),
    Pool(name="dossier", floor_tokens=2_000, ceiling_tokens=30_000),
    Pool(name="wiki_index", floor_tokens=1_000, ceiling_tokens=10_000),
]

_ORCHESTRATOR_SPEC: list[SlotSpec] = [
    Required(name="state_header", fixed_tokens=2_000),
    Required(name="action_menu", fixed_tokens=2_000),
    Pool(name="page_index", floor_tokens=4_000, ceiling_tokens=40_000),
    Pool(name="action_history", floor_tokens=4_000, ceiling_tokens=20_000),
    Pool(name="open_candidates", floor_tokens=2_000, ceiling_tokens=20_000),
]


_SPECS: dict[Role, list[SlotSpec]] = {
    Role.EXTRACTOR: _EXTRACTOR_SPEC,
    Role.COMPACTOR: _COMPACTOR_SPEC,
    Role.EDITOR: _EDITOR_SPEC,
    Role.WRITER: _WRITER_SPEC,
    Role.ORCHESTRATOR: _ORCHESTRATOR_SPEC,
}


def role_spec(role: Role) -> list[SlotSpec]:
    return _SPECS[role]
