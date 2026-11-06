from __future__ import annotations

import csv
from argparse import Namespace

from ls_baro.aiops2022_adapter import AIOps2022Adapter
from run_aiops2022_ablation import run


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_dataset(root):
    base = root / "training_data_with_faults"
    case = base / "training_data_with_faults" / "tar" / "2022-03-20-cloudbed1"
    labels = base / "training_data_with_faults_groundtruth"
    timestamps = (400, 500, 550, 600, 650, 700)

    container_rows = []
    for pod_id, values in {
        "node-1.api-0": (0, 1, 0, 100, 110, 120),
        "node-2.web-0": (0, 1, 0, 1, 2, 1),
    }.items():
        for timestamp, value in zip(timestamps, values):
            container_rows.append({
                "timestamp": timestamp, "cmdb_id": pod_id,
                "kpi_name": "container_cpu_usage_seconds", "value": value,
            })
    write_csv(
        case / "metric" / "container" / "cpu.csv",
        ["timestamp", "cmdb_id", "kpi_name", "value"], container_rows,
    )

    node_rows = []
    for node, values in {
        "node-1": (0, 1, 0, 1, 2, 1),
        "node-2": (0, 1, 0, 5, 6, 5),
    }.items():
        for timestamp, value in zip(timestamps, values):
            node_rows.append({
                "timestamp": timestamp, "cmdb_id": node,
                "kpi_name": "system.cpu.user", "value": value,
            })
    write_csv(
        case / "metric" / "node" / "node.csv",
        ["timestamp", "cmdb_id", "kpi_name", "value"], node_rows,
    )

    service_rows = []
    for service in ("api-grpc", "web-http"):
        for index, timestamp in enumerate(timestamps):
            service_rows.append({
                "service": service, "timestamp": timestamp, "rr": index,
                "sr": 100 - index, "mrt": index + 1, "count": index + 2,
            })
    write_csv(
        case / "metric" / "service" / "metric_service.csv",
        ["service", "timestamp", "rr", "sr", "mrt", "count"], service_rows,
    )
    write_csv(
        case / "trace" / "all" / "trace_jaeger-span.csv",
        ["timestamp", "cmdb_id", "span_id", "trace_id", "duration", "type",
         "status_code", "operation_name", "parent_span"],
        [{
            "timestamp": 600, "cmdb_id": "web-0", "span_id": "1", "trace_id": "t",
            "duration": 1, "type": "client", "status_code": 200,
            "operation_name": "api request", "parent_span": "",
        }],
    )
    write_csv(
        labels / "groundtruth-k8s-1-2022-03-20.csv",
        ["timestamp", "level", "cmdb_id", "failure_type"],
        [{"timestamp": 600, "level": "node", "cmdb_id": "node-1", "failure_type": "cpu"}],
    )
    return base


def test_adapter_builds_cross_layer_graph_and_isolates_label(tmp_path):
    root = build_dataset(tmp_path)
    decoy = (
        root.parent / "__MACOSX" / "training_data_with_faults"
        / "training_data_with_faults_groundtruth"
    )
    decoy.mkdir(parents=True)
    (decoy / "._groundtruth-k8s-1-2022-03-20.csv").write_text("metadata")
    adapter = AIOps2022Adapter(root, baseline_seconds=250, detection_seconds=100)
    spec = adapter.cases()[0]
    adapted = adapter.load(spec.case_id)
    assert {entity.kind for entity in adapted.graph.entities} == {"host", "pod", "service"}
    assert {relation.kind for relation in adapted.graph.relations} == {
        "deployment", "ownership", "dependency",
    }
    assert adapted.evaluation.root_causes == ({
        "type": "host", "key": "node-1", "failure_type": "cpu",
    },)
    assert adapted.window.detection_start == 600
    assert all(series.timestamps == tuple(sorted(series.timestamps)) for series in adapted.observations)


def test_runner_emits_four_arms_and_containment_contrast(tmp_path):
    root = build_dataset(tmp_path)
    output = tmp_path / "output"
    summary = run(Namespace(
        dataset_root=root,
        output=output,
        subset=None,
        no_resume=False,
    ))
    assert set(summary["arms"]) == {
        "Mapping-only", "Containment-only", "Dependency-only", "Full",
    }
    assert summary["selected_cases"] == 1
    assert summary["containment_transfer"]["primary"]["scopes"]["level"]["delta"]["mrr"] > 0
    assert (output / "summary.json").is_file()
    assert (output / "cases.csv").is_file()
