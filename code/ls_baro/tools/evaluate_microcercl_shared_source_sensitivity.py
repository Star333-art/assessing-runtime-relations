"""MicroCERCL Ownership sensitivity after removing shared-source service metrics.

The public dataset documents ``svc_metric.csv`` as service resource metrics
formed by averaging the service's instances.  Because ``instance.csv`` is the
Pod-level source, ``svc_metric.csv`` is not an independent cross-layer source.
This script removes every scored metric mapped from ``svc_metric.csv`` while
freezing the entities, candidate sets, relations, windows, scorer outputs for
all retained metrics, one-step propagation rule, lambda, and Pod labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from complete_entity_observation_controls import prepare, measure, apply
from evaluate_aiops2025_observability_and_rewire import _metric_seed, _rewire
from evaluate_relation_evidence_mechanisms import apply_pairs, paired_interval


SOURCE = ROOT / "output/microcercl_full/results.jsonl"
AUDIT = ROOT / "output/microcercl_layer_evaluation_20261023/label_audit.csv"
ORIGINAL = ROOT / "output/microcercl_relation_mechanisms_20261028/mechanism_control_cases.csv"
OUT = ROOT / "output/microcercl_shared_source_sensitivity_20261103"
REMOVED_FILE = "svc_metric.csv"
PERMUTATIONS = 100
REWIRES = 20
METRICS = [f"top{k}" for k in range(1, 6)] + [f"tie_top{k}" for k in range(1, 6)] + ["avg5", "tie_avg5"]
REPORTED = ["tie_top1", "tie_top3", "tie_top5", "tie_avg5"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def source_file(mapping: dict) -> str:
    return Path(mapping["provenance"]).name


def aggregate_from_mappings(entity_ids, mappings, metric_scores, excluded_files=()):
    excluded_files = set(excluded_files)
    assigned = defaultdict(list)
    for mapping in mappings:
        if source_file(mapping) in excluded_files:
            continue
        if mapping["source"] in metric_scores:
            assigned[mapping["target"]].append(float(metric_scores[mapping["source"]]))
    return {entity_id: (max(assigned[entity_id]) if assigned[entity_id] else 0.0)
            for entity_id in entity_ids}


def oriented_pairs(case):
    kinds = dict(zip(case["ids"], case["kinds"]))
    pairs = []
    for edge in case["relations"]:
        if edge["kind"] != "ownership":
            continue
        source, target = edge["source"], edge["target"]
        if kinds[source] == "service" and kinds[target] == "pod":
            pairs.append((source, target))
        elif kinds[target] == "service" and kinds[source] == "pod":
            pairs.append((target, source))
        else:
            raise AssertionError((case["case_id"], kinds[source], kinds[target]))
    if len(pairs) != len(set(pairs)):
        raise AssertionError((case["case_id"], "duplicate ownership edges"))
    return pairs


def load_cases():
    label_audit = pd.read_csv(AUDIT)
    label_audit = label_audit[label_audit.pod_reference_mode == "directory_exact"].set_index("case_id")
    assert len(label_audit) == 615
    cases, validation, metric_rows = [], [], []
    with SOURCE.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item["arm"] != "mapping-only" or item["case_id"] not in label_audit.index:
                continue
            result = item["result"]
            entities = []
            for kind in ("host", "pod", "service"):
                entities.extend(dict(id=e["id"], kind=kind, name=e["key"])
                                for e in result["rankings"][kind])
            entity_ids = [e["id"] for e in entities]
            metric_scores = {row["id"]: float(row["score"]) for row in result["rankings"]["metric"]}
            mappings = result["entity_mappings"]
            reconstructed = aggregate_from_mappings(entity_ids, mappings, metric_scores)
            max_error = max(abs(reconstructed[eid] - float(result["base_scores"][eid]))
                            for eid in entity_ids)
            if max_error > 1e-8:
                raise AssertionError((item["case_id"], "base reconstruction", max_error))
            filtered = aggregate_from_mappings(entity_ids, mappings, metric_scores, (REMOVED_FILE,))
            row = label_audit.loc[item["case_id"]]
            case = prepare(dict(dataset="microcercl", case_id=item["case_id"], level="pod",
                target=row.pod_reference, entities=entities, relations=result["runtime_topology"],
                base_scores=filtered))
            case["application"] = "Hipster" if row.application == "hipster_chaos" else "SockShop"
            cases.append(case)
            validation.append(dict(case_id=item["case_id"], application=case["application"],
                mapped_scored_metrics=len(mappings), scored_metrics=len(metric_scores),
                removed_mapped_metrics=sum(source_file(m) == REMOVED_FILE for m in mappings),
                max_original_base_reconstruction_error=max_error,
                service_entities=sum(e["kind"] == "service" for e in entities),
                service_nonzero_before=sum(float(result["base_scores"][e["id"]]) != 0.0
                                           for e in entities if e["kind"] == "service"),
                service_nonzero_after=sum(filtered[e["id"]] != 0.0
                                          for e in entities if e["kind"] == "service")))
            owner_kind = {e["id"]: e["kind"] for e in entities}
            seen = set()
            for mapping in mappings:
                key = (mapping["source"], mapping["target"])
                if key in seen:
                    continue
                seen.add(key)
                metric_rows.append(dict(case_id=item["case_id"], application=case["application"],
                    source_file=source_file(mapping), owner_layer=owner_kind[mapping["target"]],
                    metric_id=mapping["source"], removed=source_file(mapping) == REMOVED_FILE))
    assert len(cases) == 615
    assert Counter(c["application"] for c in cases) == {"Hipster": 269, "SockShop": 346}
    return cases, pd.DataFrame(validation), pd.DataFrame(metric_rows)


def run_controls(cases):
    rows, integrity, source_stats = [], [], []
    for index, case in enumerate(cases, 1):
        pairs = oriented_pairs(case)
        if not pairs:
            raise AssertionError((case["case_id"], "no ownership edges"))
        source_ix = np.asarray([i for i, kind in enumerate(case["kinds"]) if kind == "service"])
        pod_mask = np.asarray([kind == "pod" for kind in case["kinds"]])
        native = case["base"].copy()
        masked = native.copy(); masked[pod_mask] = 0.0
        source_stats.append(dict(case_id=case["case_id"], application=case["application"],
            service_sources=len(source_ix), service_nonzero=int(np.count_nonzero(native[source_ix])),
            service_zero_fraction=float(np.mean(native[source_ix] == 0.0))))
        for condition, values in {
            "native_mapping": native,
            "native_real": apply(case, native, "ownership"),
            "masked_mapping": masked,
            "masked_real": apply(case, masked, "ownership"),
        }.items():
            rows.append(dict(application=case["application"], case_id=case["case_id"],
                relation="ownership", condition=condition, **measure(case, values)))

        uniform = np.zeros(len(case["ids"])); uniform[source_ix] = 1.0
        rows.append(dict(application=case["application"], case_id=case["case_id"],
            relation="ownership", condition="masked_uniform_source",
            **measure(case, apply(case, uniform, "ownership"))))

        degree = Counter()
        for source, target in pairs:
            degree[source] += 1; degree[target] += 1
        degree_values = np.asarray([degree[eid] for eid in case["ids"]], dtype=float)
        rows.append(dict(application=case["application"], case_id=case["case_id"],
            relation="ownership", condition="masked_degree_prior", **measure(case, degree_values)))

        permuted_metrics = []
        for replicate in range(PERMUTATIONS):
            base = masked.copy()
            rng = np.random.default_rng(_metric_seed("micro-shared-source-permutation",
                case["case_id"], replicate))
            base[source_ix] = rng.permutation(base[source_ix])
            permuted_metrics.append(measure(case, apply(case, base, "ownership")))
        averaged = {metric: float(np.mean([x[metric] for x in permuted_metrics])) for metric in METRICS}
        averaged.update({key: float(np.mean([x[key] for x in permuted_metrics]))
                         for key in ("rank", "root_score", "tie_size")})
        rows.append(dict(application=case["application"], case_id=case["case_id"],
            relation="ownership", condition="masked_permuted_source", **averaged))

        original_source = Counter(s for s, _ in pairs)
        original_target = Counter(t for _, t in pairs)
        rewired_metrics = []
        for replicate in range(REWIRES):
            rewired, swaps, changed = _rewire(pairs, _metric_seed("micro-shared-source-rewire",
                case["case_id"], replicate))
            exact = (Counter(s for s, _ in rewired) == original_source
                     and Counter(t for _, t in rewired) == original_target)
            duplicate_free = len(rewired) == len(set(rewired))
            assert exact and duplicate_free
            integrity.append(dict(application=case["application"], case_id=case["case_id"],
                relation="ownership", edges=len(pairs), replicate=replicate,
                accepted_swaps=swaps, fraction_edges_changed=changed,
                exact_degrees=exact, duplicate_free=duplicate_free))
            rewired_metrics.append(measure(case, apply_pairs(case, masked, rewired)))
        averaged = {metric: float(np.mean([x[metric] for x in rewired_metrics])) for metric in METRICS}
        averaged.update({key: float(np.mean([x[key] for x in rewired_metrics]))
                         for key in ("rank", "root_score", "tie_size")})
        rows.append(dict(application=case["application"], case_id=case["case_id"],
            relation="ownership", condition="masked_rewired", **averaged))
        if index % 100 == 0:
            print(f"Processed {index}/615", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(integrity), pd.DataFrame(source_stats)


def summaries(rows):
    parts = []
    for scope, data in [("All", rows), *rows.groupby("application")]:
        part = data.groupby(["relation", "condition"], as_index=False).agg(
            cases=("case_id", "nunique"), **{metric: (metric, "mean") for metric in METRICS})
        part.insert(0, "scope", scope); parts.append(part)
    return pd.concat(parts, ignore_index=True)


def contrasts(rows):
    records = []
    comparisons = [
        ("native_real", "native_mapping", "native-total"),
        ("masked_real", "masked_mapping", "masked-total"),
        ("masked_real", "masked_permuted_source", "score-endpoint-alignment"),
        ("masked_real", "masked_uniform_source", "uniform-score-structure"),
        ("masked_real", "masked_degree_prior", "degree-prior"),
        ("masked_real", "masked_rewired", "endpoint-association"),
    ]
    for scope, data in [("All", rows), *rows.groupby("application")]:
        pivot = data.pivot(index="case_id", columns="condition")
        for left, right, interpretation in comparisons:
            for metric in REPORTED:
                values = pivot[metric][left] - pivot[metric][right]
                records.append(dict(scope=scope, relation="ownership", root_layer="pod",
                    contrast=f"{left}-{right}", interpretation=interpretation, metric=metric,
                    left_mean=float(pivot[metric][left].mean()),
                    right_mean=float(pivot[metric][right].mean()),
                    **paired_interval(values, "microcercl-shared-source", scope, left, right, metric)))
    return pd.DataFrame(records)


def comparison_with_original(rows):
    original = pd.read_csv(ORIGINAL)
    original = original[original.relation == "ownership"]
    records = []
    for scope, current in [("All", rows), *rows.groupby("application")]:
        old = original if scope == "All" else original[original.application == scope]
        paired = current.merge(old, on=["case_id", "application", "relation", "condition"],
                               suffixes=("_filtered", "_original"), validate="one_to_one")
        for condition, group in paired.groupby("condition"):
            for metric in REPORTED:
                delta = group[f"{metric}_filtered"] - group[f"{metric}_original"]
                records.append(dict(scope=scope, condition=condition, metric=metric,
                    filtered_mean=float(group[f"{metric}_filtered"].mean()),
                    original_mean=float(group[f"{metric}_original"].mean()),
                    **paired_interval(delta, "microcercl-filter-vs-original", scope, condition, metric)))
    return pd.DataFrame(records)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cases, validation, metric_rows = load_cases()
    validation.to_csv(OUT / "base_reconstruction_and_filter_audit.csv", index=False)
    metric_rows.to_csv(OUT / "scored_metric_source_cases.csv", index=False)
    metric_summary = (metric_rows.groupby(["source_file", "owner_layer", "removed"], as_index=False)
        .agg(case_count=("case_id", "nunique"), scored_mappings=("metric_id", "count"),
             unique_metric_ids=("metric_id", "nunique")))
    metric_summary.to_csv(OUT / "scored_metric_source_summary.csv", index=False)

    rows, integrity, source_stats = run_controls(cases)
    rows.to_csv(OUT / "mechanism_control_cases.csv", index=False)
    integrity.to_csv(OUT / "rewiring_integrity.csv", index=False)
    source_stats.to_csv(OUT / "service_source_score_coverage.csv", index=False)
    summary = summaries(rows); summary.to_csv(OUT / "mechanism_control_summary.csv", index=False)
    paired = contrasts(rows); paired.to_csv(OUT / "mechanism_control_paired_ci.csv", index=False)
    original_comparison = comparison_with_original(rows)
    original_comparison.to_csv(OUT / "filtered_vs_original_paired_ci.csv", index=False)

    avg = paired[(paired.scope == "All") & (paired.metric == "tie_avg5")]
    lookup = {row.interpretation: row for row in avg.itertuples()}
    alignment = lookup["score-endpoint-alignment"]
    endpoint = lookup["endpoint-association"]
    mechanism = ("alignment-dependent recovery"
                 if all(lookup[name].ci95_low_pp > 0 for name in
                        ("masked-total", "score-endpoint-alignment", "uniform-score-structure",
                         "degree-prior", "endpoint-association"))
                 else "additional advantage not established against all specified controls")

    protocol = {
        "experiment": "MicroCERCL shared-source metric exclusion sensitivity",
        "cases": 615,
        "applications": {"Hipster": 269, "SockShop": 346},
        "root_layer": "pod",
        "known_conditions": "public injection time and native Pod root layer; ranking within Pod candidates",
        "removed_source": "svc_metric.csv",
        "reason": "official dataset documentation defines service resource metrics as averages of instances",
        "retained_service_sources": ["latency.csv", "success_rate.csv", "svc_qps.csv"],
        "frozen": ["scored values of retained metrics", "entities", "candidate sets", "relations",
                   "target labels", "one-step propagation", "lambda=0.1", "tie-expected ranking"],
        "aggregation": "signed maximum over retained scored metrics mapped to each entity",
        "permutations": PERMUTATIONS,
        "rewirings": REWIRES,
        "bootstrap": "5000 paired case resamples; nominal exploratory 95% intervals",
        "interpretation": mechanism,
        "causal_limit": "Alignment dependence does not establish independent telemetry or causal fault propagation",
        "official_source": "https://github.com/WDCloudEdge/MicroCERCL#metrics",
    }
    dump(OUT / "protocol.json", protocol)
    verification = {
        "complete": True,
        "cases": len(cases),
        "all_original_bases_exactly_reconstructed": bool(
            (validation.max_original_base_reconstruction_error <= 1e-8).all()),
        "maximum_original_base_reconstruction_error": float(
            validation.max_original_base_reconstruction_error.max()),
        "all_rewirings_preserve_degrees": bool(integrity.exact_degrees.all()),
        "all_rewirings_duplicate_free": bool(integrity.duplicate_free.all()),
        "source_sha256": {str(path): sha(path) for path in
            [Path(__file__), SOURCE, AUDIT, ORIGINAL,
             ROOT / "src/ls_baro/microcercl_adapter.py",
             ROOT / "tools/evaluate_microcercl_relation_mechanisms.py"]},
    }
    dump(OUT / "verification.json", verification)

    definitions = pd.DataFrame([
        ["node.csv", "Host", "CPU, memory, network and edge-network measurements", "direct host measurements", False],
        ["instance.csv", "Pod", "CPU, memory and network-transmit measurements for each instance", "direct instance/Pod measurements", False],
        ["svc_metric.csv", "Service", "CPU, memory, filesystem and network resource measurements", "average of the service's instances", True],
        ["latency.csv", "Service", "service latency percentiles", "service-level request-performance series", False],
        ["success_rate.csv", "Service", "service success rate", "service-level request-performance series", False],
        ["svc_qps.csv", "Service", "service queries per second", "service-level request-performance series", False],
    ], columns=["file", "bound_layer", "metrics", "documented_source", "excluded_in_sensitivity"])
    definitions.to_csv(OUT / "metric_source_definitions.csv", index=False)

    lines = [
        "# MicroCERCL shared-source metric sensitivity", "",
        "`svc_metric.csv` is excluded because the official dataset documentation describes its service resource metrics as averages of service instances. The experiment retains `latency.csv`, `success_rate.csv`, and `svc_qps.csv`, freezes all retained metric scores and the graph, and reevaluates Ownership -> Pod.", "",
        "The result is interpreted as alignment-dependent ranking recovery, not independent cross-layer evidence or causal fault propagation.", "",
        "## Metric sources", "",
        "| File | Bound layer | Contents | Source status | Sensitivity policy |",
        "|---|---|---|---|---|",
    ]
    for row in definitions.itertuples():
        lines.append(f"| {row.file} | {row.bound_layer} | {row.metrics} | {row.documented_source} | {'exclude' if row.excluded_in_sensitivity else 'retain'} |")
    lines += ["", "## Ownership -> Pod after excluding `svc_metric.csv`", "",
        "| Scope | Condition | N | Tie Top-1 | Tie Top-3 | Tie Top-5 | Tie Avg@5 |",
        "|---|---|---:|---:|---:|---:|---:|"]
    order = ["native_mapping", "native_real", "masked_mapping", "masked_real",
             "masked_permuted_source", "masked_uniform_source", "masked_degree_prior", "masked_rewired"]
    summary["order"] = summary.condition.map({name: i for i, name in enumerate(order)})
    for row in summary.sort_values(["scope", "order"]).itertuples():
        lines.append(f"| {row.scope} | {row.condition} | {row.cases} | {row.tie_top1:.2%} | {row.tie_top3:.2%} | {row.tie_top5:.2%} | {row.tie_avg5:.4f} |")
    lines += ["", "## Paired Avg@5 contrasts", "",
        "| Scope | Contrast | Difference pp | Nominal exploratory 95% CI |",
        "|---|---|---:|---:|"]
    for row in paired[paired.metric == "tie_avg5"].itertuples():
        lines.append(f"| {row.scope} | {row.contrast} | {row.difference_pp:.2f} | [{row.ci95_low_pp:.2f}, {row.ci95_high_pp:.2f}] |")
    lines += ["", "## Overall interpretation", "",
        f"Classification: **{mechanism}**.", "",
        f"Real minus score-permuted Avg@5: {alignment.difference_pp:.2f} pp [{alignment.ci95_low_pp:.2f}, {alignment.ci95_high_pp:.2f}].",
        f"Real minus degree-preserving rewiring Avg@5: {endpoint.difference_pp:.2f} pp [{endpoint.ci95_low_pp:.2f}, {endpoint.ci95_high_pp:.2f}].", "",
        "These contrasts test dependence on the retained service score-to-endpoint alignment. They do not make the retained Service series statistically independent of Pod behavior, and they do not identify a causal fault-propagation path."]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-9:]), flush=True)
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
