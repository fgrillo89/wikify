"""Closed vocabularies for wikify."""

from __future__ import annotations

from enum import Enum


class ModelTier(str, Enum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"
