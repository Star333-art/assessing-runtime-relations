"""Apply the relation-mechanism protocol to MicroCERCL Pod roots.

Only the 615 directory-exact Pod cases are used (Hipster and SockShop).
Ownership is inferred by the public adapter's naming rule rather than directly
observed in the original dataset, so this is a portability check with weaker
relation provenance than LSDB/AIOps2025.
"""
from __future__ import annotations

from collections import Counter
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

OUT = ROOT / "output/microcercl_relation_mechanisms_20261028"
SOURCE = ROOT / "output/microcercl_full/results.jsonl"
AUDIT = ROOT / "output/microcercl_layer_evaluation_20261023/label_audit.csv"
RELATIONS = {"deployment": "host", "ownership": "service"}
PERMUTATIONS = 100
REWIRES = 20
METRICS = [f"top{k}" for k in range(1, 6)] + [f"tie_top{k}" for k in range(1, 6)] + ["avg5", "tie_avg5"]
REPORTED = ["tie_top1", "tie_top3", "tie_top5", "tie_avg5"]


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases():
    audit = pd.read_csv(AUDIT)
    audit = audit[audit.pod_reference_mode == "directory_exact"].set_index("case_id")
    assert len(audit) == 615
    cases = []
    with SOURCE.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item["arm"] != "mapping-only" or item["case_id"] not in audit.index:
                continue
            result = item["result"]
            entities = []
            for kind in ("host", "pod", "service"):
                entities.extend(dict(id=e["id"], kind=kind, name=e["key"])
                                for e in result["rankings"][kind])
            row = audit.loc[item["case_id"]]
            case = prepare(dict(dataset="microcercl", case_id=item["case_id"], level="pod",
                target=row.pod_reference, entities=entities, relations=result["runtime_topology"],
                base_scores=result["base_scores"]))
            case["application"] = "Hipster" if row.application == "hipster_chaos" else "SockShop"
            cases.append(case)
    assert len(cases) == 615
    assert Counter(c["application"] for c in cases) == {"Hipster": 269, "SockShop": 346}
    return cases


def oriented_pairs(case, relation):
    expected_source = RELATIONS[relation]
    kinds = dict(zip(case["ids"], case["kinds"]))
    pairs = []
    for edge in case["relations"]:
        if edge["kind"] != relation:
            continue
        source, target = edge["source"], edge["target"]
        if kinds[source] == expected_source and kinds[target] == "pod":
            pairs.append((source, target))
        elif kinds[target] == expected_source and kinds[source] == "pod":
            pairs.append((target, source))
        else:
            raise AssertionError((case["case_id"], relation, kinds[source], kinds[target]))
    assert len(pairs) == len(set(pairs))
    return pairs


def contrasts(rows):
    records = []
    comparisons = [
        ("native_real", "native_mapping", "native-total"),
        ("masked_real", "masked_mapping", "masked-total"),
        ("masked_real", "masked_permuted_source", "evidence-alignment"),
        ("masked_real", "masked_uniform_source", "uniform-structure"),
        ("masked_real", "masked_degree_prior", "degree-prior"),
        ("masked_real", "masked_rewired", "relation-semantics"),
    ]
    scopes = [("All", rows), *rows.groupby("application")]
    for scope, data in scopes:
        for relation, group in data.groupby("relation"):
            pivot = group.pivot(index="case_id", columns="condition")
            for left, right, interpretation in comparisons:
                for metric in REPORTED:
                    values = pivot[metric][left] - pivot[metric][right]
                    records.append(dict(scope=scope, relation=relation, root_layer="pod",
                        contrast=f"{left}-{right}", interpretation=interpretation, metric=metric,
                        left_mean=float(pivot[metric][left].mean()),
                        right_mean=float(pivot[metric][right].mean()),
                        **paired_interval(values, "microcercl", scope, relation, left, right, metric)))
    return pd.DataFrame(records)


def classifications(table):
    result = []
    avg = table[table.metric == "tie_avg5"]
    for (scope, relation), group in avg.groupby(["scope", "relation"]):
        get = {r.interpretation: r for r in group.itertuples()}
        native, total = get["native-total"], get["masked-total"]
        permuted, uniform = get["evidence-alignment"], get["uniform-structure"]
        if total.ci95_low_pp > 0 and permuted.ci95_low_pp > 0 and uniform.ci95_low_pp > 0:
            mechanism = "evidence-supported compensation"
        elif total.ci95_low_pp > 0:
            mechanism = "recovery not separable from controls"
        elif total.ci95_high_pp < 0:
            mechanism = "harmful under complete target-layer masking"
        else:
            mechanism = "unsupported or uncertain recovery"
        native_label = ("harmful under native evidence" if native.ci95_high_pp < 0 else
                        "beneficial under native evidence" if native.ci95_low_pp > 0 else
                        "native effect uncertain")
        result.append(dict(scope=scope, relation=relation, native_effect=native_label,
            complete_mask_mechanism=mechanism,
            total_diff_pp=total.difference_pp,
            total_ci=f"[{total.ci95_low_pp:.2f}, {total.ci95_high_pp:.2f}]",
            real_permuted_pp=permuted.difference_pp,
            real_permuted_ci=f"[{permuted.ci95_low_pp:.2f}, {permuted.ci95_high_pp:.2f}]",
            real_uniform_pp=uniform.difference_pp,
            real_uniform_ci=f"[{uniform.ci95_low_pp:.2f}, {uniform.ci95_high_pp:.2f}]",
            real_degree_pp=get["degree-prior"].difference_pp,
            real_degree_ci=f"[{get['degree-prior'].ci95_low_pp:.2f}, {get['degree-prior'].ci95_high_pp:.2f}]",
            real_rewired_pp=get["relation-semantics"].difference_pp,
            real_rewired_ci=f"[{get['relation-semantics'].ci95_low_pp:.2f}, {get['relation-semantics'].ci95_high_pp:.2f}]"))
    return pd.DataFrame(result)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, integrity = [], []
    cases = load_cases()
    for index, case in enumerate(cases, 1):
        for relation, source_layer in RELATIONS.items():
            pairs = oriented_pairs(case, relation)
            if not pairs:
                integrity.append(dict(application=case["application"], case_id=case["case_id"],
                    relation=relation, edges=0, replicate=-1, accepted_swaps=0,
                    fraction_edges_changed=0.0, exact_degrees=True, duplicate_free=True,
                    usable=False))
                continue
            source_ix = np.asarray([i for i, kind in enumerate(case["kinds"]) if kind == source_layer])
            pod_mask = np.asarray([kind == "pod" for kind in case["kinds"]])
            native = case["base"].copy()
            masked = native.copy(); masked[pod_mask] = 0.0
            base_conditions = {
                "native_mapping": native,
                "native_real": apply(case, native, relation),
                "masked_mapping": masked,
                "masked_real": apply(case, masked, relation),
            }
            for condition, values in base_conditions.items():
                rows.append(dict(application=case["application"], case_id=case["case_id"],
                    relation=relation, condition=condition, **measure(case, values)))

            uniform = np.zeros(len(case["ids"])); uniform[source_ix] = 1.0
            rows.append(dict(application=case["application"], case_id=case["case_id"],
                relation=relation, condition="masked_uniform_source",
                **measure(case, apply(case, uniform, relation))))

            degree = Counter()
            for source, target in pairs:
                degree[source] += 1; degree[target] += 1
            degree_values = np.asarray([degree[eid] for eid in case["ids"]], dtype=float)
            rows.append(dict(application=case["application"], case_id=case["case_id"],
                relation=relation, condition="masked_degree_prior", **measure(case, degree_values)))

            permuted_metrics = []
            for rep in range(PERMUTATIONS):
                base = masked.copy()
                rng = np.random.default_rng(_metric_seed("micro-source-permutation",
                    case["case_id"], relation, rep))
                base[source_ix] = rng.permutation(base[source_ix])
                permuted_metrics.append(measure(case, apply(case, base, relation)))
            averaged = {metric: float(np.mean([x[metric] for x in permuted_metrics])) for metric in METRICS}
            averaged.update({key: float(np.mean([x[key] for x in permuted_metrics]))
                             for key in ("rank", "root_score", "tie_size")})
            rows.append(dict(application=case["application"], case_id=case["case_id"],
                relation=relation, condition="masked_permuted_source", **averaged))

            original_source = Counter(s for s, _ in pairs)
            original_target = Counter(t for _, t in pairs)
            rewired_metrics = []
            for rep in range(REWIRES):
                rewired, swaps, changed = _rewire(pairs, _metric_seed("micro-rewire",
                    case["case_id"], relation, rep))
                exact = Counter(s for s, _ in rewired) == original_source and Counter(t for _, t in rewired) == original_target
                duplicate_free = len(rewired) == len(set(rewired))
                assert exact and duplicate_free
                integrity.append(dict(application=case["application"], case_id=case["case_id"],
                    relation=relation, edges=len(pairs), replicate=rep, accepted_swaps=swaps,
                    fraction_edges_changed=changed, exact_degrees=exact,
                    duplicate_free=duplicate_free, usable=True))
                rewired_metrics.append(measure(case, apply_pairs(case, masked, rewired)))
            averaged = {metric: float(np.mean([x[metric] for x in rewired_metrics])) for metric in METRICS}
            averaged.update({key: float(np.mean([x[key] for x in rewired_metrics]))
                             for key in ("rank", "root_score", "tie_size")})
            rows.append(dict(application=case["application"], case_id=case["case_id"],
                relation=relation, condition="masked_rewired", **averaged))
        if index % 100 == 0:
            print(f"Processed {index}/615", flush=True)

    rows = pd.DataFrame(rows); audit = pd.DataFrame(integrity)
    rows.to_csv(OUT / "mechanism_control_cases.csv", index=False)
    audit.to_csv(OUT / "rewiring_integrity.csv", index=False)
    summary_parts = []
    for scope, data in [("All", rows), *rows.groupby("application")]:
        part = data.groupby(["relation", "condition"], as_index=False).agg(
            cases=("case_id", "nunique"), **{metric: (metric, "mean") for metric in METRICS})
        part.insert(0, "scope", scope); summary_parts.append(part)
    summary = pd.concat(summary_parts, ignore_index=True)
    summary.to_csv(OUT / "mechanism_control_summary.csv", index=False)
    paired = contrasts(rows); paired.to_csv(OUT / "mechanism_control_paired_ci.csv", index=False)
    labels = classifications(paired); labels.to_csv(OUT / "mechanism_classification.csv", index=False)

    verification = dict(complete=True, cases=615, applications={"Hipster": 269, "SockShop": 346},
        root_layer="pod", ownership_provenance="adapter-derived naming rule; not directly observed",
        permutations=PERMUTATIONS, rewirings=REWIRES,
        all_rewirings_preserve_degrees=bool(audit.exact_degrees.all()),
        all_rewirings_duplicate_free=bool(audit.duplicate_free.all()),
        source_sha256={str(p): sha(p) for p in [Path(__file__), SOURCE, AUDIT,
            ROOT / "tools/evaluate_relation_evidence_mechanisms.py"]})
    (OUT / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")

    lines = ["# MicroCERCL relation-mechanism portability check", "",
        "The LSDB/AIOps2025 protocol is reused without changing mapped scores, candidates, propagation equation, lambda, or the Pod-root evaluation layer.",
        "The 615 directory-exact cases comprise Hipster (269) and SockShop (346). Ownership is adapter-derived from naming rather than directly observed, so this is weaker provenance evidence.", "",
        "## Classification using tie-expected Avg@5", "",
        "| Scope | Relation | Native effect | Complete-mask mechanism | Total pp (95% CI) | Real-permuted pp (95% CI) | Real-uniform pp (95% CI) | Real-degree pp (95% CI) | Real-rewired pp (95% CI) |",
        "|---|---|---|---|---|---|---|---|---|"]
    for row in labels.itertuples():
        lines.append(f"| {row.scope} | {row.relation} | {row.native_effect} | {row.complete_mask_mechanism} | {row.total_diff_pp:.2f} {row.total_ci} | {row.real_permuted_pp:.2f} {row.real_permuted_ci} | {row.real_uniform_pp:.2f} {row.real_uniform_ci} | {row.real_degree_pp:.2f} {row.real_degree_ci} | {row.real_rewired_pp:.2f} {row.real_rewired_ci} |")
    lines += ["", "## Performance", "",
        "| Scope | Relation | Condition | N | Tie Top-1 | Tie Top-3 | Tie Top-5 | Tie Avg@5 |",
        "|---|---|---|---:|---:|---:|---:|---:|"]
    order = ["native_mapping", "native_real", "masked_mapping", "masked_real",
             "masked_permuted_source", "masked_uniform_source", "masked_degree_prior", "masked_rewired"]
    summary["order"] = summary.condition.map({x: i for i, x in enumerate(order)})
    for row in summary.sort_values(["scope", "relation", "order"]).itertuples():
        lines.append(f"| {row.scope} | {row.relation} | {row.condition} | {row.cases} | {row.tie_top1:.2%} | {row.tie_top3:.2%} | {row.tie_top5:.2%} | {row.tie_avg5:.4f} |")
    lines += ["", "## Limits", "",
        "The injected target is a directory-encoded Pod. Service and Host attributions are not treated as independent root causes. Relation provenance and repeated injections limit generalization; the result demonstrates protocol reuse, not production deployment validity."]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(labels.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
