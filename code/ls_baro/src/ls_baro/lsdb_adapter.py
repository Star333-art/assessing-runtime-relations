"""Translate LatentScope Dataset B files into the canonical engine contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from .contracts import (
    AnalysisWindow,
    EvaluationRecord,
    LSDBAdapterOutput,
    MetricBinding,
    MetricSeries,
    Relation,
    RuntimeEntity,
    RuntimeGraph,
)
from .dataset import LatentScopeCase


def _entity_id(kind: str, key: str) -> str:
    return f"lsdb:{kind}:{quote(key, safe='')}"


def _metric_id(key: str) -> str:
    return f"lsdb:metric:{quote(key, safe='')}"


class LSDBAdapter:
    """Build the shared graph/observations/window and isolated labels for one case."""

    def load(self, dataset_root: str | Path, case_id: str | int) -> LSDBAdapterOutput:
        root = Path(dataset_root)
        case = LatentScopeCase.load(root, case_id)
        entity_ids = {entity.id: _entity_id(entity.kind, entity.key) for entity in case.entities}
        entities = tuple(
            RuntimeEntity(id=entity_ids[entity.id], kind=entity.kind, name=entity.key)
            for entity in case.entities
        )
        relations: list[Relation] = []
        relation_occurrences: dict[tuple[str, str, str], int] = {}
        for source, target, _linked_metrics in case.rcc_edges:
            source_entity = next(entity for entity in case.entities if entity.id == source)
            target_entity = next(entity for entity in case.entities if entity.id == target)
            if source_entity.kind == "host" and target_entity.kind == "pod":
                kind, source_ref = "deployment", "rccs.json:rcc_edges"
            elif source_entity.kind == "service" and target_entity.kind == "pod":
                kind, source_ref = "ownership", "rccs.json:rcc_edges"
            else:
                # Keep observed edges in the graph with their original semantics;
                # the engine only propagates the two explicitly supported groups.
                kind, source_ref = "observed", "rccs.json:rcc_edges"
            signature = (kind, entity_ids[source], entity_ids[target])
            occurrence = relation_occurrences.get(signature, 0)
            relation_occurrences[signature] = occurrence + 1
            relations.append(Relation(
                id=(f"lsdb:relation:{kind}:{entity_ids[source]}:"
                    f"{entity_ids[target]}:{occurrence}"),
                source=entity_ids[source],
                target=entity_ids[target],
                kind=kind,
                source_ref=source_ref,
            ))
        service_ids = {
            entity.key: entity_ids[entity.id]
            for entity in case.entities if entity.kind == "service"
        }
        for caller, callee in sorted(set(case.service_dependencies)):
            if caller not in service_ids or callee not in service_ids:
                continue
            relations.append(Relation(
                id=f"lsdb:dependency:{quote(caller, safe='')}:{quote(callee, safe='')}",
                source=service_ids[caller], target=service_ids[callee],
                kind="dependency", source_ref="service_deps.json",
            ))

        bindings = tuple(sorted({
            MetricBinding(
                metric_id=_metric_id(metric),
                entity_id=entity_ids[entity_id],
                source_ref="rccs.json:rccs.linked_metrics",
            )
            for entity_id, metrics in case.linked_metrics.items()
            for metric in metrics
        }, key=lambda binding: (binding.metric_id, binding.entity_id)))
        unique_metrics = {row["key"]: row for row in case.metrics}
        observations = tuple(
            MetricSeries(
                metric_id=_metric_id(row["key"]),
                timestamps=tuple(timestamp for timestamp, _ in row["samples"]),
                values=tuple(value for _, value in row["samples"]),
            )
            for row in unique_metrics.values()
        )
        timestamps = [timestamp for series in observations for timestamp in series.timestamps]
        if not timestamps:
            raise ValueError(f"case {case.case_id}: no metric observations")
        first, last = min(timestamps), max(timestamps)
        trigger_time = case.trigger_time
        window = AnalysisWindow(
            baseline_start=first,
            baseline_end=min(last, trigger_time - 1),
            detection_start=max(first, trigger_time),
            detection_end=last,
        )
        if window.baseline_start > window.baseline_end or window.detection_start > window.detection_end:
            raise ValueError(f"case {case.case_id}: trigger does not split the metric window")

        topology_files = [
            root / "data" / case.case_id / "rccs.json",
            root / "labels" / "service_deps.json",
        ]
        graph_version = hashlib.sha256(
            b"".join(path.read_bytes() for path in topology_files)
        ).hexdigest()
        graph = RuntimeGraph(
            entities=entities,
            relations=tuple(relations),
            metric_bindings=bindings,
            data_source="LatentScope Dataset B",
            graph_version=graph_version,
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
