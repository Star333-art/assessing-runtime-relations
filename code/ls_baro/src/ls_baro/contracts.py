"""Dataset-neutral request and result types for the RCA engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeEntity:
    id: str
    kind: str
    name: str


@dataclass(frozen=True)
class Relation:
    id: str
    source: str
    target: str
    kind: str
    source_ref: str


@dataclass(frozen=True)
class MetricBinding:
    metric_id: str
    entity_id: str
    source_ref: str


@dataclass(frozen=True)
class RuntimeGraph:
    entities: tuple[RuntimeEntity, ...]
    relations: tuple[Relation, ...]
    metric_bindings: tuple[MetricBinding, ...]
    data_source: str
    graph_version: str
    unavailable_kinds: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricSeries:
    metric_id: str
    timestamps: tuple[int, ...]
    values: tuple[float, ...]


@dataclass(frozen=True)
class AnalysisWindow:
    baseline_start: int
    baseline_end: int
    detection_start: int
    detection_end: int


@dataclass(frozen=True)
class RCAConfig:
    # containment is an alias for the union of deployment and ownership.
    # Explicit kinds can be selected independently; overlapping selectors do
    # not duplicate edges.
    relation_groups: tuple[str, ...] = ("mapping", "containment", "dependency")
    propagation_lambda: float = 0.2
    direction: str = "relation-aware"
    score_normalization: str = "raw"
    entity_aggregation: str = "max"
    # reference_row holds weights fixed using all supported graph relations;
    # row recomputes each source's degree after relation selection.
    edge_normalization: str = "none"
    mapping_policy: str = "most_specific"
    score_mode: str = "baro_dataframe_equivalent"
    metric_policy: str = "exclude_last_seen"


@dataclass(frozen=True)
class EvaluationRecord:
    """Labels kept outside the engine request; never used for scoring/ranking."""
    case_id: str
    root_causes: tuple[dict[str, str], ...]
    observed_triggers: tuple[dict[str, str], ...]
    window_source: str


@dataclass(frozen=True)
class LSDBAdapterOutput:
    graph: RuntimeGraph
    observations: tuple[MetricSeries, ...]
    window: AnalysisWindow
    evaluation: EvaluationRecord


@dataclass
class RCAResult:
    rankings: dict[str, list[dict[str, Any]]]
    base_scores: dict[str, float]
    final_scores: dict[str, float]
    entity_mappings: list[dict[str, Any]]
    runtime_topology: list[dict[str, Any]]
    propagation_edges: list[dict[str, Any]]
    contributions: list[dict[str, Any]]
    impact_paths: list[dict[str, Any]]
    run_info: dict[str, Any]
