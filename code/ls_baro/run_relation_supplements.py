"""Frozen relation-sensitivity experiments; no target-data parameter selection."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from ls_baro.contracts import RCAConfig
from ls_baro.engine import RCAEngine
from ls_baro.lsdb_adapter import LSDBAdapter
from ls_baro.microcercl_adapter import MicroCERCLAdapter, case_paths

ARMS = {
    "Mapping-only": (), "Deployment": ("deployment",), "Ownership": ("ownership",),
    "Dependency": ("dependency",), "Containment": ("deployment", "ownership"),
    "Deployment+Dependency": ("deployment", "dependency"),
    "Ownership+Dependency": ("ownership", "dependency"),
    "Full": ("deployment", "ownership", "dependency"),
}
SENSITIVITY_ARMS = ("Ownership", "Containment", "Dependency", "Full")
DEFAULT_OUTPUT = ROOT / "output/relation_supplements_20261015"
PROTOCOL = {
    "primary_metric": "deterministic known-root-type Top-3",
    "primary_comparisons": [["Mapping-only", "Containment"], ["Dependency", "Containment"]],
    "primary_testing_family": "four tests: two comparisons on each of LSDB and Hipster; Holm correction",
    "secondary_metrics": ["layer/global deterministic Top-1/3/5", "layer/global MRR", "layer Avg@5"],
    "lambda_grid": [0.05, 0.1, 0.2], "lambda_sensitivity_arms": SENSITIVITY_ARMS,
    "normalization_control": "reference_row: degree on all supported runtime edges under the same direction; mask relations without renormalizing",
    "normalization_arms": ARMS, "normalization_lambda": 0.1, "direction": "bidirectional",
    "statistics": "two-sided exact McNemar for paired Top-N; 20000 paired percentile bootstrap resamples with seed 20261015; cluster bootstrap by exact root kind/name as a repeated-target sensitivity",
    "cluster_caveat": "exact root identity is a proxy for injection target; shared hosts, time and dynamic instance identity may leave additional dependence; few target groups imply uncertain intervals",
    "scope": "LSDB all 88 cases; MicroCERCL Hipster all 299 cases, all monitored namespaces; Service labels only on MicroCERCL",
    "selection_caveat": "Hipster selected after prior three-subset results were available; this decomposition is exploratory external evidence, not a new unseen test set",
    "label_usage": "root identity/type used only after inference; public injection times define evaluation windows",
    "ownership_caveat": "MicroCERCL ownership inferred by exact known service prefix after Kubernetes suffix removal",
    "parameter_selection": "no best lambda chosen from these experiments; historical lambda 0.1 remains the anchor",
    "global_ties": "primary uses deterministic common engine sorting; historical LSDB fractional-tie results remain separate artifacts",
}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def specifications():
    configs = [("row", name, 0.0 if name == "Mapping-only" else .1) for name in ARMS]
    configs += [("row", name, weight) for weight in (.05, .2) for name in SENSITIVITY_ARMS]
    configs += [("reference_row", name, 0.0 if name == "Mapping-only" else .1) for name in ARMS]
    return configs


def truth_entity(request):
    if len(request.evaluation.root_causes) != 1:
        raise ValueError("protocol requires one root cause")
    truth = request.evaluation.root_causes[0]
    if "id" in truth:
        return next(e for e in request.graph.entities if e.id == truth["id"])
    return next(e for e in request.graph.entities if e.kind == truth["type"] and e.name == truth["key"])


def evaluate(dataset, case_id, request, result, norm, arm, weight):
    truth = truth_entity(request)
    incoming = Counter()
    evidence = []
    for c in result.contributions:
        if c["to"] == truth.id:
            incoming[c["relation_kind"]] += c["delta"]
            evidence.append(c)
    row = {"dataset": dataset, "case_id": case_id, "truth_level": truth.kind, "truth_key": truth.name,
           "target_cluster": truth.kind + ":" + truth.name,
           "fault": request.audit["fault"] if hasattr(request, "audit") else "not_stratified",
           "normalization": norm, "arm": arm, "lambda": weight,
           "root_base_score": result.base_scores[truth.id], "root_final_score": result.final_scores[truth.id],
           **{f"root_{kind}_delta": incoming[kind] for kind in ("deployment", "ownership", "dependency")},
           "propagation_edges": len(result.propagation_edges),
           **{f"{kind}_edges": sum(e["relation_kind"] == kind for e in result.propagation_edges)
              for kind in ("deployment", "ownership", "dependency")}}
    for scope, ranked in (("layer", result.rankings[truth.kind]), ("global", result.rankings["all_entities"])):
        rank = next(i for i, e in enumerate(ranked, 1) if e["id"] == truth.id)
        root_score = result.final_scores[truth.id]
        row[f"{scope}_rank"] = rank
        row[f"{scope}_candidates"] = len(ranked)
        row[f"{scope}_tie_size"] = sum(e["score"] == root_score for e in ranked)
        for k in (1, 3, 5):
            row[f"{scope}_top{k}"] = float(rank <= k)
        row[f"{scope}_mrr"] = 1 / rank
    row["layer_avg5"] = sum(row["layer_rank"] <= k for k in range(1, 6)) / 5
    return row, evidence


def historical_index():
    old = {}
    for dataset, file in (("LSDB", ROOT / "output/lsdb_relation_split_l0p1/relation_cases.csv"),
                          ("Hipster", ROOT / "output/microcercl_hipster_relation_split_l0p1/relation_cases.csv")):
        with file.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                old[(dataset, row["case_id"], row.get("experiment", row.get("arm")))] = row
    return old


def compare_history(row, old):
    prior = old[(row["dataset"], row["case_id"], row["arm"])]
    fields = {"layer_rank": "baro_rank" if row["dataset"] == "LSDB" else "service_rank",
              "global_rank": "global_deterministic_rank" if row["dataset"] == "LSDB" else "global_rank"}
    fields.update({k: k for k in ("root_base_score", "root_final_score", "root_deployment_delta",
                                 "root_ownership_delta", "root_dependency_delta", "propagation_edges",
                                 "deployment_edges", "ownership_edges", "dependency_edges")})
    err = 0.
    for new_key, old_key in fields.items():
        a, b = float(row[new_key]), float(prior[old_key])
        err = max(err, abs(a - b))
        if not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-10):
            raise AssertionError(f"historical mismatch: {row['dataset']} {row['case_id']} {row['arm']} {new_key}")
    return {"case_id": row["case_id"], "arm": row["arm"], "passed": True, "max_abs_error": err}


def edge_key(e):
    return e["source"], e["target"], e["relation_id"]


def run_case(dataset, case_id, request, old):
    engine, invariant = RCAEngine(), None
    results, rows, evidence, validation = {}, [], [], []
    for norm, arm, weight in specifications():
        config = RCAConfig(relation_groups=("mapping", *ARMS[arm]), propagation_lambda=weight,
                           direction="bidirectional", edge_normalization=norm)
        result = engine.analyze(request.graph, request.observations, request.window, config)
        signature = (result.base_scores, result.entity_mappings, result.rankings["metric"],
                     {kind: sorted(e["id"] for e in ranked) for kind, ranked in result.rankings.items() if kind != "metric"})
        if invariant is None:
            invariant = signature
            scores = [(r["key"], r["score"]) for r in result.rankings["metric"]]
            engine = RCAEngine(scorer=lambda *_: scores)
        elif signature != invariant:
            raise AssertionError("metric scores, base scores, mapping or candidates changed between arms")
        row, root_evidence = evaluate(dataset, case_id, request, result, norm, arm, weight)
        if norm == "row" and (weight == .1 or arm == "Mapping-only"):
            validation.append(compare_history(row, old))
        results[(norm, arm, weight)] = result
        rows.append(row)
        evidence.append({"normalization": norm, "arm": arm, "lambda": weight, "root_incoming": root_evidence})

    row_full = results[("row", "Full", .1)]
    ref_full = results[("reference_row", "Full", .1)]
    if row_full.final_scores != ref_full.final_scores or row_full.contributions != ref_full.contributions:
        raise AssertionError("Full reference-row must equal Full selected-row exactly")
    reference_weights = {edge_key(e): e["weight"] for e in ref_full.propagation_edges}
    compared = 0
    for (norm, _, _), result in results.items():
        if norm == "reference_row":
            for e in result.propagation_edges:
                if e["weight"] != reference_weights[edge_key(e)]:
                    raise AssertionError("reference weight changed under a relation mask")
                compared += 1
    ref_o, ref_c = (results[("reference_row", arm, .1)] for arm in ("Ownership", "Containment"))
    if ref_o.rankings.get("service", []) != ref_c.rankings.get("service", []):
        raise AssertionError("with frozen weights, Deployment must not directly change Service scores in one hop")
    mapping, deployment = results[("row", "Mapping-only", .0)], results[("row", "Deployment", .1)]
    if mapping.rankings.get("service", []) != deployment.rankings.get("service", []):
        raise AssertionError("Deployment-only cannot directly change a Service score")
    audit = {"dataset": dataset, "case_id": case_id, "entities": len(request.graph.entities),
             "relations": dict(Counter(e.kind for e in request.graph.relations)),
             "graph_version": request.graph.graph_version,
             "window": asdict(request.window), "window_source": request.evaluation.window_source,
             "root": asdict(truth_entity(request)),
             "metric_scores_sha256": fingerprint(scores), "base_scores_sha256": fingerprint(invariant[0]),
             "same_scores_mappings_candidates": True, "full_reference_equals_full_row": True,
             "reference_weights_checked": compared, "reference_ownership_containment_service_equal": True,
             "historical_checks": validation}
    return {"rows": rows, "audit": audit, "root_evidence": evidence,
            "reference_edges": ref_full.propagation_edges, "base_scores": invariant[0]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--dataset", choices=("both", "LSDB", "Hipster"), default="both")
    ap.add_argument("--lsdb-root", type=Path, default=ROOT.parent / "LatentScope/data/dataset_b")
    ap.add_argument("--micro-root", type=Path, default=ROOT.parent / "data/abnormal_20240615/abnormal")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sources = [Path(__file__), *[ROOT / "src/ls_baro" / n for n in
               ("canonical_core.py", "contracts.py", "engine.py", "scoring.py", "topology.py", "lsdb_adapter.py", "microcercl_adapter.py")]]
    source_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    code_hash = fingerprint(source_hashes)
    write_json(args.output / "planned_protocol.json", {"protocol": PROTOCOL, "source_sha256": source_hashes})
    old = historical_index()
    rows, audits = [], []
    started = time.time()
    for dataset in ("LSDB", "Hipster"):
        if args.dataset not in ("both", dataset):
            continue
        if dataset == "LSDB":
            labels = json.loads((args.lsdb_root / "labels/label.json").read_text(encoding="utf-8"))
            cases = sorted(labels, key=int)
        else:
            cases = [p for p in case_paths(args.micro_root) if p.parent.parent.name == "hipster_chaos"]
        if args.limit:
            cases = cases[:args.limit]
        for idx, case in enumerate(cases, 1):
            request = LSDBAdapter().load(args.lsdb_root, case) if dataset == "LSDB" else MicroCERCLAdapter().load(case)
            case_id = request.evaluation.case_id
            data_hash = fingerprint({"graph": asdict(request.graph), "observations": [asdict(s) for s in request.observations],
                                     "window": asdict(request.window), "evaluation": asdict(request.evaluation)})
            checkpoint = args.output / "checkpoints" / (dataset + "_" + hashlib.sha256(case_id.encode()).hexdigest()[:24] + ".json")
            saved = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else None
            resumed = bool(saved and saved.get("source_hash") == code_hash and saved.get("input_hash") == data_hash)
            if not resumed:
                saved = {**run_case(dataset, case_id, request, old), "source_hash": code_hash, "input_hash": data_hash}
                write_json(checkpoint, saved)
            rows.extend(saved["rows"])
            audits.append(saved["audit"])
            if idx % 10 == 0 or idx == len(cases):
                print(f"{dataset} {idx}/{len(cases)}: 24 configurations/case, historical/invariant checks passed; elapsed={time.time()-started:.1f}s", flush=True)
    write_csv(args.output / "supplement_cases.csv", rows)
    history = [r for a in audits for r in a["historical_checks"]]
    write_json(args.output / "run_audit.json", {"case_counts": dict(Counter(a["dataset"] for a in audits)),
               "case_configuration_runs": len(rows), "historical_comparisons": len(history),
               "historical_max_abs_error": max((r["max_abs_error"] for r in history), default=0.),
               "all_checks_passed": True, "reference_edge_weight_checks": sum(a["reference_weights_checked"] for a in audits),
               "elapsed_seconds": time.time()-started, "source_sha256": source_hashes,
               "case_audits": audits})
    print(f"DONE: {len(audits)} cases, {len(rows)} case-configurations, {len(history)} historical comparisons", flush=True)


if __name__ == "__main__":
    main()
