"""One graph-neutral scoring and relation-selective RCA entry point."""
from collections import defaultdict, deque
import json
import math
from urllib.parse import unquote

from .contracts import RCAResult, RuntimeGraph
from .scoring import score_with_official_baro
from .topology import normalize_metric_scores, propagate_once
from .canonical_core import aggregate, propagation, rankings

CONTRACT_VERSION = "1.0"


def _raw_metric_key(metric_id):
    # Compatibility boundary for existing LSDB scorer callbacks and KPI filters.
    for prefix in ('lsdb:metric:', 'aiops2022:metric:'):
        if metric_id.startswith(prefix):
            return unquote(metric_id[len(prefix):])
    return metric_id


class RCAEngine:
    def __init__(self, baro_root=None, scorer=None):
        self.baro_root = baro_root
        self.scorer = scorer

    def analyze(self, graph, observations, window, config):
        _validate_request(graph, observations, window, config)
        metric_keys = {s.metric_id: _raw_metric_key(s.metric_id) for s in observations}
        if len(set(metric_keys.values())) != len(metric_keys):
            raise ValueError('metric IDs collide after compatibility decoding')
        runtime_metrics = {key: metric for metric, key in metric_keys.items()}
        descriptors, scoring_metrics = {}, {}
        for series in observations:
            key = metric_keys[series.metric_id]
            try:
                descriptor = json.loads(key)
            except (TypeError, ValueError):
                descriptor = {}
            descriptors[key] = descriptor if isinstance(descriptor, dict) else {}
            scoring_metrics[key] = [(t, v) for t, v in zip(series.timestamps, series.values)
                                    if window.baseline_start <= t <= window.baseline_end
                                    or window.detection_start <= t <= window.detection_end]
        if self.scorer is None:
            scored = score_with_official_baro(scoring_metrics, window.detection_start,
                                            self.baro_root, score_mode=config.score_mode,
                                            metric_policy=config.metric_policy)
        else:
            scored = self.scorer(scoring_metrics, window.detection_start, self.baro_root)
        scores = dict(normalize_metric_scores(scored, config.score_normalization))
        if set(scores) - set(scoring_metrics) or any(not math.isfinite(v) for v in scores.values()):
            raise ValueError('scorer returned unknown metrics or non-finite scores')
        base, mappings = aggregate(graph, scores, descriptors, metric_keys, config)
        edges, provenance = propagation(graph, config)
        final, contributions = propagate_once(base, edges, config.propagation_lambda)
        for row in contributions:
            relation = provenance[(row['from'], row['to'], row['relation'])]
            row.update(relation_id=relation.id, relation_kind=relation.kind, source_ref=relation.source_ref)
        edge_rows = []
        for edge in edges:
            relation = provenance[(edge.source, edge.target, edge.relation)]
            edge_rows.append({**edge.to_dict(), 'relation_id': relation.id, 'relation_kind': relation.kind})
        ranked = rankings(graph, final, scores, descriptors, runtime_metrics)
        groups = {'containment' if r.kind in {'deployment', 'ownership'} else r.kind
                  for r in graph.relations}
        groups.update(r.kind for r in graph.relations)
        return RCAResult(
            rankings=ranked, base_scores=base, final_scores=final,
            entity_mappings=mappings, runtime_topology=[r.__dict__.copy() for r in graph.relations],
            propagation_edges=edge_rows, contributions=contributions,
            impact_paths=_impact_paths(graph, ranked, edge_rows),
            run_info={
                'contract_version': CONTRACT_VERSION,
                'data_source': graph.data_source, 'graph_version': graph.graph_version,
                'config': {**config.__dict__, 'relation_groups': list(config.relation_groups)},
                'analysis_window': window.__dict__.copy(),
                'missing_relation_groups': sorted(set(config.relation_groups) - groups - {'mapping'}),
                'available_entity_kinds': sorted({e.kind for e in graph.entities}),
                'unavailable_entity_kinds': list(graph.unavailable_kinds),
                'counts': dict(entities=len(graph.entities), relations=len(graph.relations),
                               metric_bindings=len(graph.metric_bindings), scored_metrics=len(scores),
                               propagation_edges=len(edges)),
            })


def _validate_request(graph, observations, window, config) -> None:
    if not math.isfinite(config.propagation_lambda) or config.propagation_lambda < 0:
        raise ValueError("propagation_lambda must be finite and non-negative")
    entity_ids = [entity.id for entity in graph.entities]
    relation_ids = [relation.id for relation in graph.relations]
    metric_ids = [series.metric_id for series in observations]
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError("runtime graph entity IDs must be unique")
    if len(relation_ids) != len(set(relation_ids)):
        raise ValueError("runtime graph relation IDs must be unique")
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("observation metric IDs must be unique")
    known_entities = set(entity_ids)
    for relation in graph.relations:
        if relation.source not in known_entities or relation.target not in known_entities:
            raise ValueError(f"relation {relation.id!r} references an unknown entity")
    known_metrics = set(metric_ids)
    for binding in graph.metric_bindings:
        if binding.metric_id not in known_metrics or binding.entity_id not in known_entities:
            raise ValueError(f"metric binding {binding!r} references an unknown object")
    for series in observations:
        if len(series.timestamps) != len(series.values):
            raise ValueError(f"metric {series.metric_id!r} timestamps and values differ in length")
        if tuple(sorted(series.timestamps)) != series.timestamps:
            raise ValueError(f"metric {series.metric_id!r} timestamps must be sorted")
    if not (
        window.baseline_start <= window.baseline_end
        < window.detection_start <= window.detection_end
    ):
        raise ValueError("analysis window must have ordered, non-overlapping baseline and detection intervals")
    allowed_groups = {"mapping", "containment", "deployment", "ownership", "dependency"}
    if not set(config.relation_groups) <= allowed_groups:
        raise ValueError("relation_groups supports mapping, containment, deployment, ownership, dependency")
    if len(config.relation_groups) != len(set(config.relation_groups)):
        raise ValueError("relation_groups must not contain duplicates")


def _impact_paths(graph: RuntimeGraph, rankings: dict, propagation_edges: list[dict]) -> list[dict]:
    """Explain paths using graph edges, without consulting root-cause labels."""
    entity_by_id = {entity.id: entity for entity in graph.entities}
    adjacency: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    propagation_pairs = {
        (edge["source"], edge["target"], edge["relation_id"]) for edge in propagation_edges
    }
    for relation in graph.relations:
        if relation.kind == "deployment":
            source, target = relation.source, relation.target
        elif relation.kind == "ownership":
            source, target = relation.target, relation.source
        elif relation.kind == "dependency":
            source, target = relation.target, relation.source
        else:
            source, target = relation.source, relation.target
        is_propagation = (source, target, relation.id) in propagation_pairs
        adjacency[source].append((target, relation.id, "propagation" if is_propagation else "explanation"))
    top = [row for row in rankings.get("all_entities", []) if row.get("kind") != "metric"][:10]
    paths = []
    for start in top:
        queue = deque([(start["id"], [start["id"]], [])])
        visited = {start["id"]}
        found = None
        while queue:
            current, nodes, edges = queue.popleft()
            if len(nodes) > 1 and any(
                entity_by_id[node].kind != entity_by_id[start["id"]].kind
                for node in nodes[1:] if node in entity_by_id
            ):
                found = (nodes, edges)
                break
            for target, relation_id, role in sorted(adjacency.get(current, [])):
                if target not in visited:
                    visited.add(target)
                    queue.append((target, [*nodes, target], [*edges, {"relation_id": relation_id, "role": role}]))
        if found:
            nodes, edges = found
            paths.append({
                "candidate": start,
                "entities": [
                    {"id": node, "kind": entity_by_id[node].kind, "name": entity_by_id[node].name}
                    for node in nodes
                ],
                "relations": edges,
            })
    return paths
