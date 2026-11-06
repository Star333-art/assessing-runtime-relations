from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .contracts import (
    AnalysisWindow,
    EvaluationRecord,
    LSDBAdapterOutput,
    MetricBinding,
    MetricSeries,
    RCAConfig,
    Relation,
    RuntimeEntity,
    RuntimeGraph,
)
from .dataset import LatentScopeCase
from .engine import RCAEngine
from .lsdb_adapter import LSDBAdapter


def run_case(
    dataset_root: Path | str,
    case_id: str | int,
    baro_root: Path | str | None = None,
    propagation_lambda: float = 0.2,
    topology_components: str = "all",
    direction: str = "relation-aware",
    score_normalization: str = "raw",
    entity_aggregation: str = "max",
    edge_normalization: str = "none",
    mapping_policy: str = "most_specific",
    score_mode: str = "baro_dataframe_equivalent",
    metric_policy: str = "all",
    scorer: Callable | None = None,
) -> dict:
    adapted = LSDBAdapter().load(dataset_root, case_id)
    config = _config(
        propagation_lambda, topology_components, direction, score_normalization,
        entity_aggregation, edge_normalization, mapping_policy, score_mode,
        metric_policy,
    )
    result = RCAEngine(baro_root=baro_root, scorer=scorer).analyze(
        adapted.graph, adapted.observations, adapted.window, config
    )
    return _legacy_result_view(result, adapted.evaluation, config)


def run_case_from_metric_ranking(
    case: LatentScopeCase,
    metric_ranking: list[tuple[str, float]],
    propagation_lambda: float = 0.2,
    topology_components: str = "all",
    direction: str = "relation-aware",
    score_normalization: str = "raw",
    entity_aggregation: str = "max",
    edge_normalization: str = "none",
    mapping_policy: str = "most_specific",
    score_mode: str = "precomputed",
    metric_policy: str = "precomputed",
) -> dict:
    adapted = _adapt_case(case)
    config = _config(
        propagation_lambda, topology_components, direction, score_normalization,
        entity_aggregation, edge_normalization, mapping_policy, score_mode,
        metric_policy,
    )
    engine = RCAEngine(scorer=lambda _metrics, _window, _root: metric_ranking)
    result = engine.analyze(adapted.graph, adapted.observations, adapted.window, config)
    return _legacy_result_view(result, adapted.evaluation, config)


def _config(
    propagation_lambda, topology_components, direction, score_normalization,
    entity_aggregation, edge_normalization, mapping_policy, score_mode, metric_policy,
) -> RCAConfig:
    relation_groups = ["mapping"]
    if topology_components in {"containment", "all"}:
        relation_groups.append("containment")
    if topology_components in {"dependency", "all"}:
        relation_groups.append("dependency")
    if topology_components not in {"mapping-only", "containment", "dependency", "all"}:
        raise ValueError("topology_components must be mapping-only, containment, dependency, or all")
    return RCAConfig(
        relation_groups=tuple(relation_groups),
        propagation_lambda=propagation_lambda,
        direction=direction,
        score_normalization=score_normalization,
        entity_aggregation=entity_aggregation,
        edge_normalization=edge_normalization,
        mapping_policy=mapping_policy,
        score_mode=score_mode,
        metric_policy=metric_policy,
    )


def _adapt_case(case: LatentScopeCase) -> LSDBAdapterOutput:
    id_by_legacy = {
        entity.id: f"lsdb:{entity.kind}:{quote(entity.key, safe='')}"
        for entity in case.entities
    }
    entities = tuple(
        RuntimeEntity(id=id_by_legacy[entity.id], kind=entity.kind, name=entity.key)
        for entity in case.entities
    )
    entity_by_legacy = {entity.id: entity for entity in case.entities}
    relations = []
    relation_occurrences: dict[tuple[str, str, str], int] = {}
    for source, target, _ in case.rcc_edges:
        source_kind = entity_by_legacy[source].kind
        target_kind = entity_by_legacy[target].kind
        kind = "deployment" if source_kind == "host" and target_kind == "pod" else (
            "ownership" if source_kind == "service" and target_kind == "pod" else "observed"
        )
        signature = (kind, id_by_legacy[source], id_by_legacy[target])
        occurrence = relation_occurrences.get(signature, 0)
        relation_occurrences[signature] = occurrence + 1
        relations.append(Relation(
            id=(f"lsdb:relation:{kind}:{id_by_legacy[source]}:"
                f"{id_by_legacy[target]}:{occurrence}"),
            source=id_by_legacy[source], target=id_by_legacy[target], kind=kind,
            source_ref="rccs.json:rcc_edges",
        ))
    services = {entity.key: id_by_legacy[entity.id] for entity in case.entities if entity.kind == "service"}
    for caller, callee in sorted(set(case.service_dependencies)):
        if caller in services and callee in services:
            relations.append(Relation(
                id=f"lsdb:dependency:{quote(caller, safe='')}:{quote(callee, safe='')}",
                source=services[caller], target=services[callee], kind="dependency",
                source_ref="service_deps.json",
            ))
    unique_metrics = {row["key"]: row for row in case.metrics}
    observations = tuple(
        MetricSeries(
            metric_id=f"lsdb:metric:{quote(row['key'], safe='')}",
            timestamps=tuple(ts for ts, _ in row["samples"]),
            values=tuple(value for _, value in row["samples"]),
        ) for row in unique_metrics.values()
    )
    bindings = tuple(sorted({
        MetricBinding(
            metric_id=f"lsdb:metric:{quote(metric, safe='')}",
            entity_id=id_by_legacy[owner],
            source_ref="rccs.json:rccs.linked_metrics",
        )
        for owner, metrics in case.linked_metrics.items() for metric in metrics
    }, key=lambda binding: (binding.metric_id, binding.entity_id)))
    times = [timestamp for series in observations for timestamp in series.timestamps]
    window = AnalysisWindow(min(times), case.trigger_time - 1, case.trigger_time, max(times))
    graph = RuntimeGraph(
        entities=entities, relations=tuple(relations), metric_bindings=bindings,
        data_source="LatentScope Dataset B", graph_version="in-memory-adapted",
        unavailable_kinds=("physical_server", "process", "task"),
        metadata={"case_id": case.case_id},
    )
    evaluation = EvaluationRecord(
        case_id=case.case_id,
        root_causes=tuple(dict(item) for item in case.root_causes),
        observed_triggers=tuple(dict(item) for item in case.triggers),
        window_source="label.json:time (public injection timestamp)",
    )
    return LSDBAdapterOutput(graph, observations, window, evaluation)


def _legacy_result_view(result, evaluation: EvaluationRecord, config: RCAConfig) -> dict:
    contributions = result.contributions
    owner_metrics: dict[str, set[str]] = {}
    for mapping in result.entity_mappings:
        owner_metrics.setdefault(mapping["target"], set()).add(mapping["source"])
    for row in contributions:
        shared = owner_metrics.get(row["from"], set()) & owner_metrics.get(row["to"], set())
        row["shared_metric_count"] = len(shared)
        row["shares_mapped_evidence"] = bool(shared)
    return {
        "case_id": evaluation.case_id,
        "trigger_time": result.run_info["analysis_window"]["detection_start"],
        "ground_truth": list(evaluation.root_causes),
        "triggers": list(evaluation.observed_triggers),
        "evaluation_protocol": {
            "labels_used_by_engine": False,
            "root_cause_labels_used_by_engine": False,
            "label_used_to_define_window": True,
            "window_source": evaluation.window_source,
        },
        "method": "BARO RobustScorer -> Entity Mapping -> one-hop Topology Propagation -> Hierarchical RCA Ranking",
        "formula": "S_final[i] = S_BARO_mapped[i] + lambda * sum_j(w_ji * S_BARO_mapped[j])",
        "lambda": config.propagation_lambda,
        "topology_components": (
            "+".join(group for group in config.relation_groups if group != "mapping")
            or "mapping-only"
        ),
        "direction": config.direction,
        "score_normalization": config.score_normalization,
        "entity_aggregation": config.entity_aggregation,
        "edge_normalization": config.edge_normalization,
        "mapping_policy": config.mapping_policy,
        "score_mode": config.score_mode,
        "metric_policy": config.metric_policy,
        "score_source": "BARO RobustScorer only",
        "topology_sources": ["rccs.json", "service_deps.json"],
        "level_availability": {
            "available": result.run_info["available_entity_kinds"] + ["metric"],
            "unavailable": result.run_info["unavailable_entity_kinds"],
        },
        "counts": {
            **result.run_info["counts"],
            "mapping_edges": len(result.entity_mappings),
            "runtime_edges": len(result.runtime_topology),
            "propagation_edges": len(result.propagation_edges),
        },
        "mapping_diagnostics": {
            "scored_metrics": result.run_info["counts"]["scored_metrics"],
            "metrics_linked_to_multiple_entities": _multiple_mapped_metrics(result.entity_mappings),
            "propagation_edges_sharing_mapped_evidence": _shared_evidence_count(
                result.entity_mappings, contributions
            ),
        },
        "run_info": result.run_info,
        "base_scores": result.base_scores,
        "final_scores": result.final_scores,
        "entity_mappings": result.entity_mappings,
        "runtime_topology": result.runtime_topology,
        "propagation_edges": result.propagation_edges,
        "contributions": contributions,
        "hierarchical_rankings": result.rankings,
        "impact_paths": result.impact_paths,
    }


def _multiple_mapped_metrics(mappings: list[dict]) -> int:
    owners: dict[str, set[str]] = {}
    for row in mappings:
        owners.setdefault(row["source"], set()).add(row["target"])
    return sum(len(targets) > 1 for targets in owners.values())


def _shared_evidence_count(mappings: list[dict], contributions: list[dict]) -> int:
    owner_metrics: dict[str, set[str]] = {}
    for row in mappings:
        owner_metrics.setdefault(row["target"], set()).add(row["source"])
    return sum(
        bool(owner_metrics.get(row["from"], set()) & owner_metrics.get(row["to"], set()))
        for row in contributions
    )


def root_cause_rank(result: dict) -> int | None:
    ranks = result["hierarchical_rankings"]
    positions: list[int] = []
    for truth in result["ground_truth"]:
        level = "host" if truth["type"] == "host" else truth["type"]
        for index, row in enumerate(ranks.get(level, []), start=1):
            if row["key"] == truth["key"]:
                positions.append(index)
                break
    return min(positions) if positions else None


def _tie_aware_rank(rows: list[dict], key: str, tolerance: float = 1e-12) -> dict | None:
    match = next((row for row in rows if row["key"] == key), None)
    if match is None:
        return None
    score = float(match["score"])
    positions = [
        index for index, row in enumerate(rows, start=1)
        if abs(float(row["score"]) - score) <= tolerance
    ]
    return {
        "rank": sum(positions) / len(positions),
        "rank_min": min(positions),
        "rank_max": max(positions),
        "top1": sum(position <= 1 for position in positions) / len(positions),
        "top3": sum(position <= 3 for position in positions) / len(positions),
        "top5": sum(position <= 5 for position in positions) / len(positions),
        "tie_size": len(positions),
    }


def root_cause_rank_details(result: dict, scope: str = "level") -> dict | None:
    """Return tie-aware rank and fractional Top-K credit for a root cause."""
    if scope not in {"level", "global"}:
        raise ValueError("scope must be level or global")
    candidates = []
    for truth in result["ground_truth"]:
        rows = (
            result["hierarchical_rankings"]["all_entities"]
            if scope == "global"
            else result["hierarchical_rankings"].get(truth["type"], [])
        )
        detail = _tie_aware_rank(rows, truth["key"])
        if detail is not None:
            candidates.append(detail)
    return min(candidates, key=lambda row: row["rank"]) if candidates else None
