from __future__ import annotations

from collections import defaultdict, deque
import math
from typing import Iterable, Mapping

from .dataset import LatentScopeCase
from .model import Edge, entity_id


def aggregate_metric_scores(
    case: LatentScopeCase,
    metric_scores: Mapping[str, float],
    aggregation: str = "max",
    mapping_policy: str = "all_links",
    include_mapping_edges: bool = True,
) -> tuple[dict[str, float], list[Edge]]:
    """Map BARO metric scores to every RCC using Dataset B's linked_metrics."""
    if aggregation not in {"max", "top3_mean", "top5_mean", "p95"}:
        raise ValueError("aggregation must be max, top3_mean, top5_mean, or p95")
    if mapping_policy not in {"all_links", "most_specific"}:
        raise ValueError("mapping_policy must be all_links or most_specific")

    def aggregate(values: list[float]) -> float:
        if not values:
            return 0.0
        values = sorted(values, reverse=True)
        if aggregation == "max":
            return values[0]
        if aggregation == "p95":
            ascending = list(reversed(values))
            return ascending[max(0, math.ceil(0.95 * len(ascending)) - 1)]
        k = 3 if aggregation == "top3_mean" else 5
        selected = values[:k]
        return sum(selected) / len(selected)

    assigned: dict[str, list[str]] = {entity.id: [] for entity in case.entities}
    if mapping_policy == "all_links":
        for entity in case.entities:
            assigned[entity.id] = [
                metric for metric in case.linked_metrics[entity.id]
                if metric in metric_scores
            ]
    else:
        entities_by_id = {entity.id: entity for entity in case.entities}
        owners_by_metric: dict[str, list[str]] = defaultdict(list)
        for entity in case.entities:
            for metric in case.linked_metrics[entity.id]:
                if metric in metric_scores:
                    owners_by_metric[metric].append(entity.id)

        for metric in metric_scores:
            owners = owners_by_metric.get(metric, [])
            if not owners:
                continue
            descriptor = case.metric_descriptor(metric)
            preferred = [
                ("pod", descriptor.get("pod")),
                ("service", descriptor.get("service")),
                ("host", descriptor.get("node")),
            ]
            selected = None
            for kind, key in preferred:
                if not key:
                    continue
                selected = next(
                    (
                        owner for owner in owners
                        if entities_by_id[owner].kind == kind
                        and entities_by_id[owner].key == str(key)
                    ),
                    None,
                )
                if selected is not None:
                    break
            if selected is None:
                priority = {"pod": 0, "service": 1, "host": 2}
                selected = min(
                    owners,
                    key=lambda owner: (
                        priority[entities_by_id[owner].kind],
                        entities_by_id[owner].key,
                    ),
                )
            assigned[selected].append(metric)

    entity_scores: dict[str, float] = {}
    mapping_edges: list[Edge] = []
    for entity in case.entities:
        linked = assigned[entity.id]
        entity_scores[entity.id] = aggregate(
            [metric_scores[key] for key in linked if key in metric_scores]
        )
        if include_mapping_edges:
            for metric in linked:
                mapping_edges.append(Edge(
                    source=entity_id("metric", metric),
                    target=entity.id,
                    weight=1.0,
                    relation="observes",
                    provenance="rccs.json:rccs.linked_metrics",
                ))
    return entity_scores, mapping_edges


def build_propagation_edges(
    case: LatentScopeCase,
    components: str = "all",
    direction: str = "relation-aware",
    edge_normalization: str = "none",
) -> tuple[list[Edge], list[Edge]]:
    """Build root-cause evidence edges and retain the original runtime edges."""
    if components not in {"containment", "dependency", "all"}:
        raise ValueError("components must be containment, dependency, or all")
    if direction not in {"forward", "backward", "bidirectional", "relation-aware"}:
        raise ValueError(
            "direction must be forward, backward, bidirectional, or relation-aware"
        )
    if edge_normalization not in {"none", "row"}:
        raise ValueError("edge_normalization must be none or row")

    original: list[Edge] = []
    base_edges: list[Edge] = []
    if components in {"containment", "all"}:
        for source, target, _ in case.rcc_edges:
            edge = Edge(source, target, 1.0, "contains", "rccs.json:rcc_edges")
            original.append(edge)
            base_edges.append(edge)

    services = {entity.key for entity in case.entities if entity.kind == "service"}
    outgoing: dict[str, list[str]] = defaultdict(list)
    if components in {"dependency", "all"}:
        for caller, callee in case.service_dependencies:
            if caller in services and callee in services and caller != callee:
                outgoing[caller].append(callee)
        for caller, callees in outgoing.items():
            for callee in sorted(set(callees)):
                edge = Edge(
                    entity_id("service", caller),
                    entity_id("service", callee),
                    1.0,
                    "calls",
                    "service_deps.json",
                )
                original.append(edge)
                base_edges.append(edge)

    evidence: list[Edge] = []
    for edge in base_edges:
        is_containment = edge.relation == "contains"
        orientations: list[tuple[str, str, str]] = []
        if direction == "forward":
            orientations.append((edge.source, edge.target, "forward"))
        elif direction == "backward":
            orientations.append((edge.target, edge.source, "backward"))
        elif direction == "bidirectional":
            orientations.extend([
                (edge.source, edge.target, "forward"),
                (edge.target, edge.source, "backward"),
            ])
        elif is_containment:
            orientations.append((edge.target, edge.source, "relation-aware-backward"))
        else:
            orientations.append((edge.source, edge.target, "relation-aware-forward"))
        for source, target, orientation in orientations:
            evidence.append(Edge(
                source=source,
                target=target,
                weight=1.0,
                relation=(
                    f"{edge.relation}:{orientation}"
                ),
                provenance=edge.provenance,
            ))

    # The legacy/default behavior gives every containment edge weight 1 and
    # row-normalizes service dependencies only. Formal ablations use global
    # row normalization so every evidence source has the same propagation budget.
    if edge_normalization == "none":
        dependency_count = {
            entity_id("service", caller): len(set(callees))
            for caller, callees in outgoing.items()
        }
        evidence = [
            Edge(
                edge.source,
                edge.target,
                (
                    1.0 / dependency_count[edge.source]
                    if edge.relation.startswith("calls:")
                    and edge.source in dependency_count
                    else edge.weight
                ),
                edge.relation,
                edge.provenance,
            )
            for edge in evidence
        ]
    else:
        row_counts: dict[str, int] = defaultdict(int)
        for edge in evidence:
            row_counts[edge.source] += 1
        evidence = [
            Edge(
                edge.source,
                edge.target,
                1.0 / row_counts[edge.source],
                edge.relation,
                edge.provenance,
            )
            for edge in evidence
        ]
    return evidence, original


def normalize_metric_scores(
    metric_ranking: Iterable[tuple[str, float]],
    method: str = "raw",
) -> list[tuple[str, float]]:
    rows = [(str(metric), float(score)) for metric, score in metric_ranking]
    if method == "raw":
        return rows
    if method == "max":
        denominator = max((score for _, score in rows), default=0.0)
        if denominator <= 0:
            return [(metric, 0.0) for metric, _ in rows]
        return [(metric, score / denominator) for metric, score in rows]
    if method == "percentile":
        ordered = sorted(rows, key=lambda item: (-item[1], item[0]))
        n = len(ordered)
        if n == 0:
            return []
        if n == 1:
            return [(ordered[0][0], 1.0)]
        # Equal BARO scores must receive the same percentile.  Use the average
        # zero-based position of the tie group, matching average-rank semantics.
        normalized: list[tuple[str, float]] = []
        index = 0
        while index < n:
            end = index + 1
            while end < n and ordered[end][1] == ordered[index][1]:
                end += 1
            average_position = (index + end - 1) / 2.0
            percentile = 1.0 - average_position / (n - 1)
            normalized.extend((metric, percentile) for metric, _ in ordered[index:end])
            index = end
        return normalized
    raise ValueError("score normalization must be raw, max, or percentile")


def propagate_once(
    base_scores: Mapping[str, float],
    edges: Iterable[Edge],
    propagation_lambda: float,
) -> tuple[dict[str, float], list[dict]]:
    if propagation_lambda < 0:
        raise ValueError("propagation_lambda must be non-negative")
    additions: dict[str, float] = defaultdict(float)
    contributions: list[dict] = []
    for edge in edges:
        source_score = float(base_scores.get(edge.source, 0.0))
        delta = propagation_lambda * edge.weight * source_score
        additions[edge.target] += delta
        contributions.append({
            "from": edge.source,
            "to": edge.target,
            "relation": edge.relation,
            "weight": edge.weight,
            "source_baro_mapped_score": source_score,
            "delta": delta,
        })
    final = {
        key: float(value) + additions.get(key, 0.0)
        for key, value in base_scores.items()
    }
    contributions.sort(key=lambda row: (-row["delta"], row["from"], row["to"]))
    return final, contributions


def hierarchical_rankings(
    case: LatentScopeCase,
    final_entity_scores: Mapping[str, float],
    metric_scores: Mapping[str, float],
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for level in ("host", "pod", "service"):
        rows = [
            {"id": entity.id, "key": entity.key, "score": float(final_entity_scores[entity.id])}
            for entity in case.entities
            if entity.kind == level
        ]
        result[level] = sorted(rows, key=lambda row: (-row["score"], row["key"]))
    metric_rows = []
    for metric, score in metric_scores.items():
        descriptor = case.metric_descriptor(metric)
        metric_rows.append({
            "id": entity_id("metric", metric),
            "key": metric,
            "score": float(score),
            "descriptor": descriptor,
        })
    result["metric"] = sorted(metric_rows, key=lambda row: (-row["score"], row["key"]))
    result["all_entities"] = sorted(
        [
            {"id": entity.id, "key": entity.key, "kind": entity.kind,
             "score": float(final_entity_scores[entity.id])}
            for entity in case.entities
        ],
        key=lambda row: (-row["score"], row["kind"], row["key"]),
    )
    return result


def _impact_adjacency(case: LatentScopeCase) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target, _ in case.rcc_edges:
        source_kind = source.split("::", 1)[0]
        if source_kind == "host":
            adjacency[source].append(target)  # host -> pod
        elif source_kind == "service":
            adjacency[target].append(source)  # pod -> owning service
    services = {entity.key for entity in case.entities if entity.kind == "service"}
    for caller, callee in case.service_dependencies:
        if caller in services and callee in services:
            adjacency[entity_id("service", callee)].append(entity_id("service", caller))
    return {key: sorted(set(values)) for key, values in adjacency.items()}


def _shortest_path(adjacency: Mapping[str, list[str]], source: str, target: str) -> list[str]:
    queue = deque([[source]])
    seen = {source}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for neighbor in adjacency.get(path[-1], []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append([*path, neighbor])
    return []


def build_impact_paths(
    case: LatentScopeCase,
    rankings: Mapping[str, list[dict]],
    candidates_per_level: int = 5,
) -> list[dict]:
    adjacency = _impact_adjacency(case)
    targets: list[tuple[str, dict]] = []
    for trigger in case.triggers:
        if trigger.get("service"):
            targets.append((entity_id("service", trigger["service"]), trigger))
        elif trigger.get("pod"):
            targets.append((entity_id("pod", trigger["pod"]), trigger))
        elif trigger.get("node"):
            targets.append((entity_id("host", trigger["node"]), trigger))
    paths: list[dict] = []
    for level in ("host", "pod", "service"):
        for candidate in rankings[level][:candidates_per_level]:
            options = [
                (_shortest_path(adjacency, candidate["id"], target), trigger)
                for target, trigger in targets
            ]
            options = [(path, trigger) for path, trigger in options if path]
            if not options:
                continue
            path, trigger = min(options, key=lambda item: (len(item[0]), item[0]))
            paths.append({
                "candidate": candidate,
                "impact_path": path,
                "observed_trigger": trigger,
                "provenance": ["rccs.json:rcc_edges", "service_deps.json", "label.json:trigger"],
            })
    return paths
