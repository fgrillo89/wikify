"""Model tier identifiers shared by requests, policies, and accounting."""

from enum import Enum


class ModelTier(str, Enum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"


def model_id_for_tier(tier: ModelTier | str) -> str:
    value = tier.value if isinstance(tier, ModelTier) else str(tier)
    return f"tier-{value}"
