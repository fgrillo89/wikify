"""Shared infrastructure: cost meter, cache, context envelope.

Pure Python. No LLM, no domain logic. These three pieces are the contract
between strategies and any model dispatcher. They are built once, locked,
and never reasoned about again.
"""

from ..contracts.roles import Role, response_reserve, role_spec, total_context
from .cache import ExtractCache, ExtractCacheKey
from .context_envelope import ContextEnvelope, Pool, Required, SlotSpec
from .cost_meter import CallRecord, CostMeter, TierPrice
from .tokens import count_tokens

__all__ = [
    "CallRecord",
    "ContextEnvelope",
    "CostMeter",
    "ExtractCache",
    "ExtractCacheKey",
    "Pool",
    "Required",
    "Role",
    "SlotSpec",
    "TierPrice",
    "count_tokens",
    "response_reserve",
    "role_spec",
    "total_context",
]
