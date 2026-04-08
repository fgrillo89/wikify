"""Thin assembly files: pick sampler + schedule + tiering, hand to pipeline.run."""

from .exploit import build as build_exploit
from .explore import build as build_explore
from .mixed import build as build_mixed

STRATEGIES = {"E": build_explore, "M": build_mixed, "X": build_exploit}
