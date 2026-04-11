"""Wiki discovery subsystem.

Owns document profiling, strategy selection, extraction-unit planning,
DAG-based workflow execution, multimodal extraction, note emission, and
finite-coverage scheduling. Canonical concept persistence lives in
``wikify.wiki.concepts`` and is intentionally separate from this package.

This subsystem is additive: it provides the typed contracts, DAG runtime,
and registry that future epochs will route through. Existing
``wikify.wiki.concepts`` (the legacy monolithic module) continues to work
unchanged while callers migrate.
"""

from wikify.wiki.discovery.config import load_workflow_yaml
from wikify.wiki.discovery.contracts import (
    ArtifactRef,
    CandidateConcept,
    CoverageRecord,
    DagNodeSpec,
    DagRunSpec,
    DiscoveryStrategy,
    DocumentProfile,
    ExtractionNote,
    ExtractionUnit,
    ModalityKind,
    UnitKind,
)
from wikify.wiki.discovery.dag import DagValidationError, validate_dag
from wikify.wiki.discovery.executor import DagExecutionResult, DagExecutor
from wikify.wiki.discovery.extractors import AgentExtractor, EchoExtractor
from wikify.wiki.discovery.planner import DiscoveryPlanner
from wikify.wiki.discovery.recipe import (
    FrontierConfig,
    Recipe,
    RecipeError,
    StepConfig,
    load_recipe_yaml,
    parse_recipe,
)
from wikify.wiki.discovery.recipe_compiler import compile_recipe
from wikify.wiki.discovery.registry import NodeRegistry, default_registry
from wikify.wiki.discovery.scheduler import EventualCoverageScheduler
from wikify.wiki.discovery.strategies import StrategyRegistry, default_strategies

__all__ = [
    "AgentExtractor",
    "ArtifactRef",
    "CandidateConcept",
    "CoverageRecord",
    "DagExecutionResult",
    "DagExecutor",
    "DagNodeSpec",
    "DagRunSpec",
    "DagValidationError",
    "DiscoveryPlanner",
    "DiscoveryStrategy",
    "DocumentProfile",
    "EchoExtractor",
    "EventualCoverageScheduler",
    "FrontierConfig",
    "Recipe",
    "RecipeError",
    "StepConfig",
    "compile_recipe",
    "load_recipe_yaml",
    "parse_recipe",
    "ExtractionNote",
    "ExtractionUnit",
    "ModalityKind",
    "NodeRegistry",
    "StrategyRegistry",
    "UnitKind",
    "default_registry",
    "default_strategies",
    "load_workflow_yaml",
    "validate_dag",
]
