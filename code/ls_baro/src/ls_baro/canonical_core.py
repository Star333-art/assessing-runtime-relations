"""Canonical graph operations; numeric policy matches the frozen Scheme C."""
from collections import defaultdict
import math

from .model import Edge


def aggregate(graph, scores, descriptors, metric_keys, config):
    if config.entity_aggregation not in {'max', 'top3_mean', 'top5_mean', 'p95'}:
        raise ValueError('unsupported entity_aggregation')
    if config.mapping_policy not in {'all_links', 'most_specific'}:
        raise ValueError('unsupported mapping_policy')
    entities = {e.id: e for e in graph.entities}
    owners = defaultdict(list)
    for binding in graph.metric_bindings:
        key = metric_keys[binding.metric_id]
        if key in scores and binding.entity_id not in [b.entity_id for b in owners[key]]:
            owners[key].append(binding)
    assigned = defaultdict(list)
    mappings = []
    for metric, score in scores.items():
        candidates = owners[metric]
        if not candidates:
            continue
        if config.mapping_policy == 'most_specific' and len(candidates) > 1:
            # Preserve LSDB's published policy. Other adapters must resolve
            # ambiguous ownership themselves, without invented type priorities.
            descriptor = descriptors.get(metric, {})
            selected = None
            for kind, field in [('pod', 'pod'), ('service', 'service'), ('host', 'node')]:
                selected = next((b for b in candidates if entities[b.entity_id].kind == kind
                                 and entities[b.entity_id].name == str(descriptor.get(field, ''))), None)
                if selected is not None:
                    break
            if selected is None:
                priority = {'pod': 0, 'service': 1, 'host': 2}
                if any(entities[b.entity_id].kind not in priority for b in candidates):
                    raise ValueError(f'ambiguous metric ownership: {metric}; resolve in adapter')
                selected = min(candidates, key=lambda b: (priority[entities[b.entity_id].kind],
                                                         entities[b.entity_id].name, b.entity_id))
            candidates = [selected]
        for binding in candidates:
            assigned[binding.entity_id].append(score)
            if 'mapping' in config.relation_groups:
                mappings.append(dict(source=binding.metric_id, target=binding.entity_id,
                                     weight=1.0, relation='observes', provenance=binding.source_ref))
    base = {}
    for entity in graph.entities:
        values = sorted(assigned[entity.id], reverse=True)
        if not values:
            value = 0.0
        elif config.entity_aggregation == 'max':
            value = values[0]
        elif config.entity_aggregation == 'p95':
            value = sorted(values)[max(0, math.ceil(.95 * len(values)) - 1)]
        else:
            top = values[:3 if config.entity_aggregation == 'top3_mean' else 5]
            value = sum(top) / len(top)
        base[entity.id] = value
    return base, mappings


def propagation(graph, config):
    if config.direction not in {'forward', 'backward', 'bidirectional', 'relation-aware'}:
        raise ValueError('unsupported direction')
    if config.edge_normalization not in {'none', 'row', 'reference_row'}:
        raise ValueError('unsupported edge_normalization')
    selected = []
    for relation in graph.relations:
        group = 'containment' if relation.kind in {'deployment', 'ownership'} else relation.kind
        if (relation.kind in {'deployment', 'ownership', 'dependency'}
                and (group in config.relation_groups or relation.kind in config.relation_groups)):
            if relation.source != relation.target:
                selected.append(relation)
    # Legacy construction visits containment first, then caller/callee pairs.
    # Keep addition order stable to preserve exact floating point results.
    by_id = {e.id: e for e in graph.entities}
    selected = [r for r in selected if r.kind != 'dependency'] + sorted(
        [r for r in selected if r.kind == 'dependency'],
        key=lambda r: (by_id[r.source].name, by_id[r.target].name, r.id))
    def orientations(relation):
        if config.direction == 'forward':
            return [(relation.source, relation.target, 'forward')]
        elif config.direction == 'backward':
            return [(relation.target, relation.source, 'backward')]
        elif config.direction == 'bidirectional':
            return [(relation.source, relation.target, 'forward'),
                    (relation.target, relation.source, 'backward')]
        elif relation.kind == 'dependency':
            return [(relation.source, relation.target, 'relation-aware-forward')]
        else:
            return [(relation.target, relation.source, 'relation-aware-backward')]

    # Sensitivity control: the reference degree uses all supported runtime
    # relations before filtering, under the same direction policy. It never
    # depends on scores or root labels. Original row/none behavior is retained.
    reference_count = defaultdict(int)
    if config.edge_normalization == 'reference_row':
        for relation in graph.relations:
            if relation.kind in {'deployment', 'ownership', 'dependency'} and relation.source != relation.target:
                for source, _, _ in orientations(relation):
                    reference_count[source] += 1
    evidence = []
    for relation in selected:
        label = 'calls' if relation.kind == 'dependency' else 'contains'
        for source, target, direction in orientations(relation):
            evidence.append((Edge(source, target, 1.0, f'{label}:{direction}', relation.source_ref), relation))
    row_count = defaultdict(int)
    dependency_count = defaultdict(int)
    for edge, _ in evidence:
        row_count[edge.source] += 1
    for relation in selected:
        if relation.kind == 'dependency':
            dependency_count[relation.source] += 1
    edges, provenance = [], {}
    for edge, relation in evidence:
        if config.edge_normalization == 'reference_row':
            denominator = reference_count[edge.source]
        elif config.edge_normalization == 'row':
            denominator = row_count[edge.source]
        else:
            denominator = dependency_count.get(edge.source, 1) if relation.kind == 'dependency' else 1
        edges.append(Edge(edge.source, edge.target, 1.0 / denominator, edge.relation, edge.provenance))
        provenance[(edge.source, edge.target, edge.relation)] = relation
    return edges, provenance


def rankings(graph, final, scores, descriptors, runtime_metrics):
    result = {}
    for kind in sorted({e.kind for e in graph.entities}):
        result[kind] = sorted([
            dict(id=e.id, key=e.name, score=float(final[e.id]))
            for e in graph.entities if e.kind == kind
        ], key=lambda r: (-r['score'], r['key'], r['id']))
    result['metric'] = sorted([
        dict(id=runtime_metrics[key], key=key, score=float(score), descriptor=descriptors[key])
        for key, score in scores.items()
    ], key=lambda r: (-r['score'], r['key']))
    result['all_entities'] = sorted([
        dict(id=e.id, key=e.name, kind=e.kind, score=float(final[e.id])) for e in graph.entities
    ], key=lambda r: (-r['score'], r['kind'], r['key'], r['id']))
    return result
