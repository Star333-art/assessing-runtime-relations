from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Entity, entity_id


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class LatentScopeCase:
    case_id: str
    trigger_time: int
    root_causes: list[dict[str, str]]
    triggers: list[dict[str, str]]
    metrics: list[dict[str, Any]]
    entities: list[Entity]
    linked_metrics: dict[str, list[str]]
    rcc_edges: list[tuple[str, str, list[str]]]
    service_dependencies: list[tuple[str, str]]

    @classmethod
    def load(cls, dataset_root: Path | str, case_id: str | int) -> "LatentScopeCase":
        root = Path(dataset_root)
        case_id = str(case_id)
        labels = _read_json(root / "labels" / "label.json")
        if case_id not in labels:
            raise KeyError(f"LatentScope case {case_id!r} is not in label.json")

        metric_rows = _read_json(root / "data" / case_id / "metrics.json")
        topology = _read_json(root / "data" / case_id / "rccs.json")
        dependencies = _read_json(root / "labels" / "service_deps.json")
        label = labels[case_id]

        metrics: list[dict[str, Any]] = []
        metric_keys: set[str] = set()
        for row in metric_rows:
            if not {"key", "service", "data"} <= row.keys():
                raise ValueError(f"case {case_id}: malformed metric row")
            descriptor = json.loads(row["key"])
            if "kpi" not in descriptor:
                raise ValueError(f"case {case_id}: metric key has no kpi: {row['key']}")
            samples = sorted(
                ((int(ts), float(value)) for ts, value in row["data"].items()),
                key=lambda item: item[0],
            )
            metrics.append({
                "key": row["key"],
                "service": row["service"],
                "descriptor": descriptor,
                "samples": samples,
            })
            metric_keys.add(row["key"])

        entities: list[Entity] = []
        linked_metrics: dict[str, list[str]] = {}
        entity_by_key: dict[str, Entity] = {}
        for row in topology["rccs"]:
            entity = Entity(kind=str(row["kind"]), key=str(row["key"]))
            if entity.kind not in {"host", "pod", "service"}:
                raise ValueError(f"case {case_id}: unsupported real entity kind {entity.kind!r}")
            if entity.key in entity_by_key:
                raise ValueError(f"case {case_id}: duplicate RCC key {entity.key!r}")
            unknown = set(row["linked_metrics"]) - metric_keys
            if unknown:
                raise ValueError(
                    f"case {case_id}: {entity.key} links {len(unknown)} unknown metrics"
                )
            entities.append(entity)
            entity_by_key[entity.key] = entity
            linked_metrics[entity.id] = list(row["linked_metrics"])

        rcc_edges: list[tuple[str, str, list[str]]] = []
        for row in topology["rcc_edges"]:
            try:
                source = entity_by_key[row["rcc1"]].id
                target = entity_by_key[row["rcc2"]].id
            except KeyError as exc:
                raise ValueError(f"case {case_id}: RCC edge has unknown endpoint") from exc
            rcc_edges.append((source, target, list(row.get("linked_metrics", []))))

        triggers = [json.loads(raw) for raw in label.get("trigger", [])]
        return cls(
            case_id=case_id,
            trigger_time=int(label["time"]),
            root_causes=list(label["rc"]),
            triggers=triggers,
            metrics=metrics,
            entities=entities,
            linked_metrics=linked_metrics,
            rcc_edges=rcc_edges,
            service_dependencies=[(str(a), str(b)) for a, b in dependencies],
        )

    def baro_input(self) -> dict[str, list[tuple[int, float]]]:
        return {row["key"]: row["samples"] for row in self.metrics}

    def metric_descriptor(self, metric_key: str) -> dict[str, str]:
        for row in self.metrics:
            if row["key"] == metric_key:
                return row["descriptor"]
        raise KeyError(metric_key)

    def level_availability(self) -> dict[str, Any]:
        present = {entity.kind for entity in self.entities}
        return {
            "available": [level for level in ("host", "pod", "service", "metric") if level in present or level == "metric"],
            "unavailable": ["physical_server", "process", "task"],
            "notes": {
                "host": "LatentScope node field and host RCC label",
                "pod": "LatentScope pod field; represents the Container/Pod layer",
                "metric": "Original JSON metric descriptor; no synthetic metric names",
                "unavailable": "No Physical Server, Process, or Task identifier exists in Dataset B",
            },
        }
