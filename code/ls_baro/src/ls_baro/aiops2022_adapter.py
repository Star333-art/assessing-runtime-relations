"""Adapter for the fault-injected AIOps 2022 HipsterShop training data."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
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


LABEL_RE = re.compile(r"groundtruth-k8s-(?P<bed>\d)-(?P<date>\d{4}-\d{2}-\d{2})\.csv$")
CASE_RE = re.compile(r"\d{4}-\d{2}-\d{2}-cloudbed\d$")
SERVICE_SUFFIX_RE = re.compile(r"-(?:grpc|http)$")
REPLICA_SUFFIX_RE = re.compile(r"-\d+$")
SERVICE_KPIS = ("rr", "sr", "mrt", "count")


@dataclass(frozen=True)
class AIOps2022CaseSpec:
    case_id: str
    subset: str
    timestamp: int
    level: str
    cmdb_id: str
    failure_type: str
    seconds_from_previous: int | None
    seconds_to_next: int | None

    def isolated(self, guard_seconds: int) -> bool:
        return (
            (self.seconds_from_previous is None or self.seconds_from_previous >= guard_seconds)
            and (self.seconds_to_next is None or self.seconds_to_next >= guard_seconds)
        )


def canonical_service(value: str) -> str:
    return SERVICE_SUFFIX_RE.sub("", value.strip())


def service_for_pod(pod: str, services: set[str]) -> str:
    workload = REPLICA_SUFFIX_RE.sub("", pod)
    matches = sorted(
        service
        for service in services
        if workload == service or re.fullmatch(re.escape(service) + r"\d+", workload)
    )
    if len(matches) == 0:
        # Some infrastructure/helper Pods (for example redis-cart) have no
        # row in the service-level KPI file. Their workload name still gives
        # an observed ownership key; the adapter creates the service entity
        # without inventing service metrics for it.
        return workload
    if len(matches) != 1:
        raise ValueError(f"pod {pod!r} has {len(matches)} service matches: {matches}")
    return matches[0]


def _entity_id(kind: str, name: str) -> str:
    return f"aiops2022:{kind}:{quote(name, safe='')}"


def _metric_id(descriptor: dict[str, str]) -> str:
    raw = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"aiops2022:metric:{quote(raw, safe='')}"


class AIOps2022Adapter:
    """Build canonical RCA inputs from one extracted training-data directory.

    The adapter keeps at most one cloudbed subset in memory. Cases should be
    processed in subset order so hundreds of fault windows reuse one CSV scan.
    """

    def __init__(
        self,
        dataset_root: str | Path,
        *,
        baseline_seconds: int = 600,
        detection_seconds: int = 600,
        trace_row_limit: int = 0,
    ) -> None:
        if baseline_seconds <= 0 or detection_seconds <= 0:
            raise ValueError("baseline and detection windows must be positive")
        self.dataset_root = Path(dataset_root).resolve()
        self.baseline_seconds = baseline_seconds
        self.detection_seconds = detection_seconds
        self.trace_row_limit = trace_row_limit
        self.case_root, self.label_root = self._locate_roots()
        self._specs = self._read_specs()
        self._by_id = {spec.case_id: spec for spec in self._specs}
        self._cached_subset: str | None = None
        self._cached: dict | None = None

    def _locate_roots(self) -> tuple[Path, Path]:
        label_dirs = []
        for path in self.dataset_root.rglob("training_data_with_faults_groundtruth"):
            if not path.is_dir() or "__MACOSX" in path.parts:
                continue
            if any(LABEL_RE.fullmatch(file.name) for file in path.glob("*.csv")):
                label_dirs.append(path)
        label_dirs.sort()
        if not label_dirs:
            raise FileNotFoundError(
                "no real ground-truth directory containing groundtruth-k8s-*.csv "
                f"was found below {self.dataset_root}"
            )
        for label_root in label_dirs:
            candidates = [label_root.parent / "training_data_with_faults" / "tar"]
            candidates.extend(
                path for path in self.dataset_root.rglob("tar")
                if path.is_dir()
                and "__MACOSX" not in path.parts
                and path.parent.name == "training_data_with_faults"
            )
            case_root = next(
                (
                    path for path in candidates
                    if path.is_dir()
                    and any(CASE_RE.fullmatch(child.name) for child in path.iterdir() if child.is_dir())
                ),
                None,
            )
            if case_root is not None:
                return case_root, label_root
        raise FileNotFoundError(
            "no real training_data_with_faults/tar directory with cloudbed subsets was found"
        )

    def _read_specs(self) -> tuple[AIOps2022CaseSpec, ...]:
        specs: list[AIOps2022CaseSpec] = []
        for path in sorted(self.label_root.glob("*.csv")):
            match = LABEL_RE.fullmatch(path.name)
            if not match:
                continue
            subset = f"{match.group('date')}-cloudbed{match.group('bed')}"
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = sorted(csv.DictReader(handle), key=lambda row: int(row["timestamp"]))
            for index, row in enumerate(rows):
                timestamp = int(row["timestamp"])
                previous = timestamp - int(rows[index - 1]["timestamp"]) if index else None
                following = (
                    int(rows[index + 1]["timestamp"]) - timestamp
                    if index + 1 < len(rows) else None
                )
                specs.append(AIOps2022CaseSpec(
                    case_id=f"{subset}:{index:03d}:{timestamp}",
                    subset=subset,
                    timestamp=timestamp,
                    level=row["level"].strip().lower(),
                    cmdb_id=row["cmdb_id"].strip(),
                    failure_type=row["failure_type"].strip(),
                    seconds_from_previous=previous,
                    seconds_to_next=following,
                ))
        if not specs:
            raise ValueError(f"no AIOps 2022 labels found below {self.label_root}")
        return tuple(sorted(specs, key=lambda row: (row.subset, row.timestamp, row.case_id)))

    def cases(self) -> tuple[AIOps2022CaseSpec, ...]:
        return self._specs

    def source_fingerprint(self) -> str:
        """Fingerprint source metadata so checkpoints are not reused after data changes."""
        rows = []
        for root in (self.case_root, self.label_root):
            for path in sorted(root.rglob("*.csv")):
                stat = path.stat()
                rows.append(
                    f"{path.relative_to(self.dataset_root)}|{stat.st_size}|{stat.st_mtime_ns}"
                )
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()

    def load(self, case_id: str) -> LSDBAdapterOutput:
        try:
            spec = self._by_id[case_id]
        except KeyError as exc:
            raise KeyError(f"unknown AIOps 2022 case: {case_id}") from exc
        if self._cached_subset != spec.subset:
            self._cached = self._load_subset(spec.subset)
            self._cached_subset = spec.subset
        assert self._cached is not None

        start = spec.timestamp - self.baseline_seconds
        end = spec.timestamp + self.detection_seconds
        observations = []
        for metric_id, samples in self._cached["samples"].items():
            selected = [(ts, value) for ts, value in samples if start <= ts <= end]
            if any(ts < spec.timestamp for ts, _ in selected) and any(
                ts >= spec.timestamp for ts, _ in selected
            ):
                observations.append(MetricSeries(
                    metric_id=metric_id,
                    timestamps=tuple(ts for ts, _ in selected),
                    values=tuple(value for _, value in selected),
                ))
        if not observations:
            raise ValueError(f"case {case_id}: no metrics span both analysis windows")

        level = "host" if spec.level == "node" else spec.level
        root_id = _entity_id(level, spec.cmdb_id)
        if root_id not in self._cached["entity_ids"]:
            raise ValueError(f"case {case_id}: root cause {level}:{spec.cmdb_id} is absent")
        evaluation = EvaluationRecord(
            case_id=spec.case_id,
            root_causes=({
                "type": level,
                "key": spec.cmdb_id,
                "failure_type": spec.failure_type,
            },),
            observed_triggers=(),
            window_source=(
                f"groundtruth timestamp ± fixed windows "
                f"({self.baseline_seconds}s pre/{self.detection_seconds}s post)"
            ),
        )
        graph = RuntimeGraph(
            entities=self._cached["entities"],
            relations=self._cached["relations"],
            metric_bindings=tuple(
                binding for binding in self._cached["bindings"]
                if binding.metric_id in {series.metric_id for series in observations}
            ),
            data_source="AIOps 2022 HipsterShop training_data_with_faults",
            graph_version=self._cached["graph_version"],
            unavailable_kinds=("physical_server", "process", "task"),
            metadata={
                "case_id": spec.case_id,
                "subset": spec.subset,
                "failure_type": spec.failure_type,
            },
        )
        return LSDBAdapterOutput(
            graph=graph,
            observations=tuple(sorted(observations, key=lambda row: row.metric_id)),
            window=AnalysisWindow(start, spec.timestamp - 1, spec.timestamp, end),
            evaluation=evaluation,
        )

    def _load_subset(self, subset: str) -> dict:
        root = self.case_root / subset
        if not root.is_dir():
            raise FileNotFoundError(f"AIOps 2022 subset is not extracted: {root}")

        service_path = root / "metric" / "service" / "metric_service.csv"
        services: set[str] = set()
        service_rows: list[dict[str, str]] = []
        with service_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                service_rows.append(row)
                services.add(canonical_service(row["service"]))

        samples: dict[str, list[tuple[int, float]]] = {}
        binding_owner: dict[str, str] = {}
        nodes: set[str] = set()
        pods: set[str] = set()
        pod_to_node: dict[str, str] = {}
        pod_to_service: dict[str, str] = {}

        def add(descriptor: dict[str, str], timestamp: str, value: str, owner: str) -> None:
            number = float(value)
            if not math.isfinite(number):
                return
            metric_id = _metric_id(descriptor)
            samples.setdefault(metric_id, []).append((int(timestamp), number))
            previous = binding_owner.setdefault(metric_id, owner)
            if previous != owner:
                raise ValueError(f"metric {metric_id} has conflicting owners")

        for path in sorted((root / "metric" / "container").glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    node, separator, pod = row["cmdb_id"].partition(".")
                    if not separator:
                        raise ValueError(f"malformed container ID: {row['cmdb_id']}")
                    service = service_for_pod(pod, services)
                    # Keep workload-only services (for example redis-cart)
                    # in the runtime graph even when service-level KPIs do
                    # not contain a row for them.
                    services.add(service)
                    if pod in pod_to_node and pod_to_node[pod] != node:
                        raise ValueError(f"pod {pod} maps to multiple nodes")
                    pod_to_node[pod] = node
                    pod_to_service[pod] = service
                    nodes.add(node)
                    pods.add(pod)
                    add(
                        {"node": node, "pod": pod, "service": service, "kpi": row["kpi_name"]},
                        row["timestamp"], row["value"], _entity_id("pod", pod),
                    )

        for path in sorted((root / "metric" / "node").glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    node = row["cmdb_id"].strip()
                    nodes.add(node)
                    add(
                        {"node": node, "kpi": row["kpi_name"]},
                        row["timestamp"], row["value"], _entity_id("host", node),
                    )

        for row in service_rows:
            service = canonical_service(row["service"])
            for kpi in SERVICE_KPIS:
                if kpi in row and row[kpi] != "":
                    add(
                        {"service": service, "kpi": kpi}, row["timestamp"], row[kpi],
                        _entity_id("service", service),
                    )

        entities = tuple(
            [RuntimeEntity(_entity_id("host", name), "host", name) for name in sorted(nodes)]
            + [RuntimeEntity(_entity_id("pod", name), "pod", name) for name in sorted(pods)]
            + [RuntimeEntity(_entity_id("service", name), "service", name) for name in sorted(services)]
        )
        relations: list[Relation] = []
        for pod in sorted(pods):
            node = pod_to_node[pod]
            service = pod_to_service[pod]
            relations.extend((
                Relation(
                    id=f"aiops2022:deployment:{quote(node, safe='')}:{quote(pod, safe='')}",
                    source=_entity_id("host", node), target=_entity_id("pod", pod),
                    kind="deployment", source_ref="metric/container:cmdb_id",
                ),
                Relation(
                    id=f"aiops2022:ownership:{quote(service, safe='')}:{quote(pod, safe='')}",
                    source=_entity_id("service", service), target=_entity_id("pod", pod),
                    kind="ownership", source_ref="metric/container:workload-name",
                ),
            ))
        for source, target in self._trace_dependencies(root, services):
            relations.append(Relation(
                id=f"aiops2022:dependency:{quote(source, safe='')}:{quote(target, safe='')}",
                source=_entity_id("service", source), target=_entity_id("service", target),
                kind="dependency", source_ref="trace/all/trace_jaeger-span.csv",
            ))
        bindings = tuple(
            MetricBinding(metric_id=metric_id, entity_id=owner, source_ref="raw metric identity")
            for metric_id, owner in sorted(binding_owner.items())
        )
        for rows in samples.values():
            rows.sort(key=lambda item: item[0])
        graph_payload = "\n".join(
            [entity.id for entity in entities]
            + [f"{row.kind}|{row.source}|{row.target}" for row in relations]
            + [f"{row.metric_id}|{row.entity_id}" for row in bindings]
        ).encode("utf-8")
        return {
            "entities": entities,
            "entity_ids": {entity.id for entity in entities},
            "relations": tuple(relations),
            "bindings": bindings,
            "samples": samples,
            "graph_version": hashlib.sha256(graph_payload).hexdigest(),
        }

    def _trace_dependencies(self, root: Path, services: set[str]) -> list[tuple[str, str]]:
        path = root / "trace" / "all" / "trace_jaeger-span.csv"
        if not path.is_file():
            raise FileNotFoundError(f"trace file is missing: {path}")
        edges: set[tuple[str, str]] = set()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=1):
                source_matches = []
                pod = row["cmdb_id"].split(".", 1)[-1]
                try:
                    source_matches = [service_for_pod(pod, services)]
                except ValueError:
                    pass
                operation = row["operation_name"].lower()
                target_matches = [service for service in services if service in operation]
                if len(source_matches) == 1 and len(target_matches) == 1:
                    edge = (source_matches[0], target_matches[0])
                    if edge[0] != edge[1]:
                        edges.add(edge)
                if self.trace_row_limit and index >= self.trace_row_limit:
                    break
        return sorted(edges)
