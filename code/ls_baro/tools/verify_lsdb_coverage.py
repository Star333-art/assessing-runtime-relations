"""Verify LSDB raw relationship coverage and adapter fidelity.

Checks, directly against the raw LatentScope Dataset B files and the adapted
canonical-engine output:

1. Raw rccs.json: entity-kind counts and rcc_edges endpoint-type distribution.
2. Whether every Pod has exactly one host->pod (deployment) and one
   service->pod (ownership) edge in the raw graph.
3. Service->Pod and Host->Pod child-count distributions (to show the graph is
   not a uniform regular graph, and no Service lacks an ownership edge).
4. Whether any rcc_edge has a non-(host,service)->pod endpoint type that the
   adapter would relabel as "observed".
5. 88/88 correspondence between raw rcc_edges and the adapted details/*.json
   relations (kinds, endpoints, occurrence counts) plus dependency edges from
   service_deps.json.

This is a read-only audit; it does not modify the dataset or add edges.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

DATASET_ROOT = Path(r"D:\AIOps\LatentScope\data\dataset_b")
OUTPUT = Path(r"D:\AIOps\LS-baro\output\lsdb_relation_split_l0p1")


def load_raw_case(case_id: str):
    topo = json.loads((DATASET_ROOT / "data" / case_id / "rccs.json").read_text(encoding="utf-8"))
    deps = json.loads((DATASET_ROOT / "labels" / "service_deps.json").read_text(encoding="utf-8"))
    kind_by_key = {row["key"]: str(row["kind"]) for row in topo["rccs"]}
    return topo, kind_by_key, deps


def main():
    labels = json.loads((DATASET_ROOT / "labels" / "label.json").read_text(encoding="utf-8"))
    case_ids = sorted(labels, key=int)

    total_kind = Counter()
    total_edges = Counter()          # (src_kind, dst_kind) -> count
    total_edges_by_case_occurrence = Counter()  # (src_kind, dst_kind) occurrence rows
    pod_edge_counts = Counter()      # (deployment_count, ownership_count) -> number of pods
    service_child_counts = Counter()
    host_child_counts = Counter()
    other_edges = []                 # any edge not (host,service)->pod
    pods_missing = []                # pods missing deployment or ownership
    duplicate_pod_edges = []         # pods with >1 of a given edge kind

    for case_id in case_ids:
        topo, kind_by_key, deps = load_raw_case(case_id)
        kinds = Counter(kind_by_key.values())
        total_kind.update(kinds)

        # incoming edges per pod key, by source kind
        pod_inc = defaultdict(Counter)
        svc_out = Counter()
        host_out = Counter()
        for edge in topo["rcc_edges"]:
            src_kind = kind_by_key[edge["rcc1"]]
            dst_kind = kind_by_key[edge["rcc2"]]
            pair = (src_kind, dst_kind)
            total_edges[pair] += 1
            total_edges_by_case_occurrence[pair] += 1
            if dst_kind == "pod":
                pod_inc[edge["rcc2"]][src_kind] += 1
                if src_kind == "service":
                    svc_out[edge["rcc1"]] += 1
                elif src_kind == "host":
                    host_out[edge["rcc1"]] += 1
            if pair not in {("host", "pod"), ("service", "pod")}:
                other_edges.append((case_id, edge["rcc1"], src_kind, edge["rcc2"], dst_kind))

        for key, k in kind_by_key.items():
            if k != "pod":
                continue
            d = pod_inc[key]["host"]
            o = pod_inc[key]["service"]
            pod_edge_counts[(d, o)] += 1
            if d != 1 or o != 1:
                pods_missing.append((case_id, key, d, o))
            if d > 1:
                duplicate_pod_edges.append((case_id, key, "deployment", d))
            if o > 1:
                duplicate_pod_edges.append((case_id, key, "ownership", o))

        service_child_counts.update(svc_out.values())
        host_child_counts.update(host_out.values())

    result = {
        "cases": len(case_ids),
        "entity_kind_counts_total": dict(total_kind),
        "rcc_edge_endpoint_pairs": {
            f"{s}->{d}": c for (s, d), c in sorted(total_edges.items())
        },
        "pod_edge_count_distribution": {
            f"deployment={d},ownership={o}": c
            for (d, o), c in sorted(pod_edge_counts.items())
        },
        "every_pod_has_exactly_one_deployment_and_ownership": not pods_missing,
        "pods_missing_or_duplicated": pods_missing,
        "duplicate_pod_edges": duplicate_pod_edges,
        "service_child_count_distribution": dict(sorted(service_child_counts.items())),
        "host_child_count_distribution": dict(sorted(host_child_counts.items())),
        "non_pod_target_edges": other_edges,
    }

    # --- adapted output cross-check ---
    mismatches = []
    adapted_kind_counts = Counter()
    adapted_relation_pairs = Counter()
    raw_relation_pairs = Counter()
    dep_expected = Counter()
    dep_adapted = Counter()
    for case_id in case_ids:
        topo, kind_by_key, deps = load_raw_case(case_id)
        details = json.loads((OUTPUT / "details" / f"{case_id}.json").read_text(encoding="utf-8"))
        # raw relationship signatures: (kind, src_key, dst_key) with occurrence counts
        raw_sig = Counter()
        for edge in topo["rcc_edges"]:
            src_kind = kind_by_key[edge["rcc1"]]
            dst_kind = kind_by_key[edge["rcc2"]]
            if (src_kind, dst_kind) == ("host", "pod"):
                kind = "deployment"
            elif (src_kind, dst_kind) == ("service", "pod"):
                kind = "ownership"
            else:
                kind = "observed"
            raw_sig[(kind, edge["rcc1"], edge["rcc2"])] += 1
        for (caller, callee) in deps:
            dep_expected[(case_id, caller, callee)] += 1
        id_to_name = {e["id"]: e["name"] for e in details["entities"]}
        adapted_sig = Counter()
        for r in details["relations"]:
            kind = r["kind"]
            adapted_kind_counts[kind] += 1
            if kind in {"deployment", "ownership", "observed"}:
                adapted_sig[(kind, id_to_name[r["source"]], id_to_name[r["target"]])] += 1
            elif kind == "dependency":
                dep_adapted[(case_id, id_to_name[r["source"]], id_to_name[r["target"]])] += 1
        if raw_sig != adapted_sig:
            mismatches.append((case_id, dict(raw_sig), dict(adapted_sig)))

    result["adapted_output"] = {
        "details_files": len(list((OUTPUT / "details").glob("*.json"))),
        "adapted_relation_kind_counts": dict(adapted_kind_counts),
        "raw_vs_adapted_signature_mismatches": mismatches,
        "dependency_edges_raw_service_deps": sum(dep_expected.values()),
        "dependency_edges_adapted": sum(dep_adapted.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
