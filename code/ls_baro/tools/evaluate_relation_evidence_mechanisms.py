"""Symmetric evidence/structure controls for LSDB and AIOps2025.

The scorer, mapped entity bases, candidate sets, propagation equation, lambda,
and native root-layer evaluation contract are frozen.  For Deployment and
Ownership, the experiment compares:

* mapping-only and true-relation propagation under native evidence;
* complete masking of the evaluated root layer;
* true topology with source-layer anomaly scores permuted within entity type;
* true topology with uniform source-layer scores;
* an incident-degree candidate prior; and
* typed, exact-degree-preserving endpoint rewiring.

The controls are operational contrasts.  They do not claim a causal
decomposition of graph effects.
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

from ls_baro.topology import Edge, propagate_once
from complete_entity_observation_controls import load_cases, measure, apply
from evaluate_aiops2025_observability_and_rewire import _metric_seed, _rewire


OUT = ROOT / "output/relation_evidence_mechanisms_20261028"
RELATIONS = {
    "deployment": ("host", "pod"),
    "ownership": ("service", "pod"),
}
PERMUTATION_REPLICATES = 100
REWIRE_REPLICATES = 20
LAMBDA = 0.1
METRICS = [f"top{k}" for k in range(1, 6)] + [f"tie_top{k}" for k in range(1, 6)] + ["avg5", "tie_avg5"]
REPORTED = ["tie_top1", "tie_top3", "tie_top5", "tie_avg5"]


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relation_pairs(case, kind: str):
    """Return consistently oriented unique bipartite pairs."""
    expected_source, expected_target = RELATIONS[kind]
    kinds = dict(zip(case["ids"], case["kinds"]))
    pairs = []
    for relation in case["relations"]:
        if relation["kind"] != kind:
            continue
        source, target = relation["source"], relation["target"]
        if kinds[source] == expected_source and kinds[target] == expected_target:
            pairs.append((source, target))
        elif kinds[source] == expected_target and kinds[target] == expected_source:
            pairs.append((target, source))
        else:
            raise AssertionError((case["dataset"], case["case_id"], kind,
                                  kinds[source], kinds[target]))
    if len(set(pairs)) != len(pairs):
        raise AssertionError((case["dataset"], case["case_id"], kind, "duplicate edges"))
    return pairs


def apply_pairs(case, base: np.ndarray, pairs) -> np.ndarray:
    expanded = []
    for source, target in pairs:
        expanded.extend(((source, target), (target, source)))
    degree = Counter(source for source, _ in expanded)
    edges = [Edge(source, target, 1.0 / degree[source], "typed", "mechanism-control")
             for source, target in expanded]
    final, _ = propagate_once(dict(zip(case["ids"], map(float, base))), edges, LAMBDA)
    return np.asarray([final[eid] for eid in case["ids"]], dtype=float)


def paired_interval(values, *key):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(_metric_seed("mechanism-bootstrap", *key))
    means = values[rng.integers(0, len(values), size=(5000, len(values)))].mean(axis=1)
    return {
        "difference_pp": 100 * float(values.mean()),
        "ci95_low_pp": 100 * float(np.quantile(means, .025)),
        "ci95_high_pp": 100 * float(np.quantile(means, .975)),
        "paired_cases": len(values),
    }


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    return (rows.groupby(["dataset", "relation", "root_layer", "condition"], as_index=False)
            .agg(cases=("case_id", "nunique"), **{metric: (metric, "mean") for metric in METRICS}))


def contrast_table(rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for (dataset, relation, layer), group in rows.groupby(["dataset", "relation", "root_layer"]):
        for left, right, stage in (
            ("native_real", "native_mapping", "native-total"),
            ("masked_real", "masked_mapping", "masked-total"),
            ("masked_real", "masked_permuted_source", "evidence-alignment"),
            ("masked_real", "masked_uniform_source", "uniform-structure"),
            ("masked_real", "masked_degree_prior", "degree-prior"),
            ("masked_real", "masked_rewired", "relation-semantics"),
        ):
            for metric in REPORTED:
                pivot = group.pivot(index="case_id", columns="condition", values=metric)
                if left not in pivot or right not in pivot:
                    continue
                paired = pivot[[left, right]].dropna()
                records.append(dict(dataset=dataset, relation=relation, root_layer=layer,
                    contrast=f"{left}-{right}", interpretation=stage, metric=metric,
                    left_mean=float(paired[left].mean()), right_mean=float(paired[right].mean()),
                    **paired_interval(paired[left] - paired[right], dataset, relation, layer,
                                      left, right, metric)))
    return pd.DataFrame(records)


def classify(contrasts: pd.DataFrame) -> pd.DataFrame:
    """Predeclared conservative mechanism labels using tie-expected Avg@5."""
    rows = []
    avg = contrasts[contrasts.metric == "tie_avg5"]
    for (dataset, relation, layer), group in avg.groupby(["dataset", "relation", "root_layer"]):
        lookup = {r.interpretation: r for r in group.itertuples()}
        native = lookup["native-total"]
        total = lookup["masked-total"]
        evidence = lookup["evidence-alignment"]
        uniform = lookup["uniform-structure"]
        degree = lookup["degree-prior"]
        rewired = lookup["relation-semantics"]
        if total.ci95_low_pp > 0 and evidence.ci95_low_pp > 0 and uniform.ci95_low_pp > 0:
            label = "evidence-supported compensation"
            reason = "masked real relation exceeds mapping, permuted-source, and uniform-source controls"
        elif total.ci95_low_pp > 0:
            label = "recovery not separable from controls"
            reason = "masked real relation improves on mapping, but at least one evidence/structure contrast is uncertain"
        elif total.ci95_high_pp < 0:
            label = "harmful under complete target-layer masking"
            reason = "masked real relation is below masked mapping"
        else:
            label = "unsupported or uncertain recovery"
            reason = "masked real relation has no clear improvement on mapping"
        native_label = ("harmful under native evidence" if native.ci95_high_pp < 0 else
                        "beneficial under native evidence" if native.ci95_low_pp > 0 else
                        "native effect uncertain")
        rows.append(dict(dataset=dataset, relation=relation, root_layer=layer,
            masked_mechanism_label=label, masked_reason=reason,
            native_effect_label=native_label,
            masked_total_diff_pp=total.difference_pp,
            masked_total_ci=f"[{total.ci95_low_pp:.2f}, {total.ci95_high_pp:.2f}]",
            real_minus_permuted_pp=evidence.difference_pp,
            real_minus_permuted_ci=f"[{evidence.ci95_low_pp:.2f}, {evidence.ci95_high_pp:.2f}]",
            real_minus_uniform_pp=uniform.difference_pp,
            real_minus_uniform_ci=f"[{uniform.ci95_low_pp:.2f}, {uniform.ci95_high_pp:.2f}]",
            real_minus_degree_pp=degree.difference_pp,
            real_minus_degree_ci=f"[{degree.ci95_low_pp:.2f}, {degree.ci95_high_pp:.2f}]",
            real_minus_rewired_pp=rewired.difference_pp,
            real_minus_rewired_ci=f"[{rewired.ci95_low_pp:.2f}, {rewired.ci95_high_pp:.2f}]"))
    return pd.DataFrame(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cases, sources, replay_checks = load_cases()
    records, integrity = [], []

    for case in cases:
        id_to_kind = dict(zip(case["ids"], case["kinds"]))
        for relation, endpoints in RELATIONS.items():
            if case["level"] not in endpoints:
                continue
            source_layer = endpoints[1] if case["level"] == endpoints[0] else endpoints[0]
            pairs = relation_pairs(case, relation)
            if not pairs:
                # Keep mapping rows for coverage audit, but mechanism controls
                # cannot be interpreted without at least one relation edge.
                integrity.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                    relation=relation, root_layer=case["level"], source_layer=source_layer,
                    edges=0, replicate=-1, accepted_swaps=0, fraction_edges_changed=0.0,
                    exact_source_degree=True, exact_target_degree=True, duplicate_free=True,
                    usable_for_relation_mechanism=False))
                continue

            target_mask = np.asarray([kind == case["level"] for kind in case["kinds"]])
            source_ix = np.asarray([i for i, kind in enumerate(case["kinds"]) if kind == source_layer], dtype=int)
            native = case["base"].copy()
            masked = native.copy(); masked[target_mask] = 0.0

            conditions = {
                "native_mapping": native,
                "native_real": apply(case, native, relation),
                "masked_mapping": masked,
                "masked_real": apply(case, masked, relation),
            }
            for name, values in conditions.items():
                score = values if isinstance(values, np.ndarray) else np.asarray(values)
                records.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                    relation=relation, root_layer=case["level"], source_layer=source_layer,
                    condition=name, replicate=-1, relation_edges=len(pairs), **measure(case, score)))

            # Uniform source: target and all unrelated layers are zero; only
            # the opposite endpoint layer carries a constant positive score.
            uniform = np.zeros(len(case["ids"]), dtype=float)
            uniform[source_ix] = 1.0
            uniform_values = apply(case, uniform, relation)
            records.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                relation=relation, root_layer=case["level"], source_layer=source_layer,
                condition="masked_uniform_source", replicate=-1, relation_edges=len(pairs),
                **measure(case, uniform_values)))

            # Incident degree is a label-free candidate prior.
            degree = Counter()
            for source, target in pairs:
                degree[source] += 1; degree[target] += 1
            degree_values = np.asarray([degree[eid] for eid in case["ids"]], dtype=float)
            records.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                relation=relation, root_layer=case["level"], source_layer=source_layer,
                condition="masked_degree_prior", replicate=-1, relation_edges=len(pairs),
                **measure(case, degree_values)))

            # Source-score permutation preserves the exact topology and the
            # within-layer score multiset but destroys score/entity alignment.
            permutation_metrics = []
            for replicate in range(PERMUTATION_REPLICATES):
                permuted = masked.copy()
                rng = np.random.default_rng(_metric_seed("source-permutation", case["dataset"],
                    case["case_id"], relation, case["level"], replicate))
                permuted[source_ix] = rng.permutation(permuted[source_ix])
                values = apply(case, permuted, relation)
                permutation_metrics.append(measure(case, values))
            averaged = {metric: float(np.mean([row[metric] for row in permutation_metrics]))
                        for metric in METRICS}
            averaged.update({key: float(np.mean([row[key] for row in permutation_metrics]))
                             for key in ("rank", "root_score", "tie_size")})
            records.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                relation=relation, root_layer=case["level"], source_layer=source_layer,
                condition="masked_permuted_source", replicate=PERMUTATION_REPLICATES,
                relation_edges=len(pairs), **averaged))

            # Typed directed bipartite 2-switches preserve exact degree
            # sequences. Average controls within case before case bootstrap.
            rewired_metrics = []
            original_source_degree = Counter(s for s, _ in pairs)
            original_target_degree = Counter(t for _, t in pairs)
            for replicate in range(REWIRE_REPLICATES):
                rewired, swaps, changed = _rewire(pairs, _metric_seed("mechanism-rewire",
                    case["dataset"], case["case_id"], relation, replicate))
                source_ok = Counter(s for s, _ in rewired) == original_source_degree
                target_ok = Counter(t for _, t in rewired) == original_target_degree
                duplicate_free = len(rewired) == len(set(rewired))
                assert source_ok and target_ok and duplicate_free
                assert all(id_to_kind[s] == endpoints[0] and id_to_kind[t] == endpoints[1]
                           for s, t in rewired)
                integrity.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                    relation=relation, root_layer=case["level"], source_layer=source_layer,
                    edges=len(pairs), replicate=replicate, accepted_swaps=swaps,
                    fraction_edges_changed=changed, exact_source_degree=source_ok,
                    exact_target_degree=target_ok, duplicate_free=duplicate_free,
                    usable_for_relation_mechanism=True))
                rewired_metrics.append(measure(case, apply_pairs(case, masked, rewired)))
            averaged = {metric: float(np.mean([row[metric] for row in rewired_metrics]))
                        for metric in METRICS}
            averaged.update({key: float(np.mean([row[key] for row in rewired_metrics]))
                             for key in ("rank", "root_score", "tie_size")})
            records.append(dict(dataset=case["dataset"], case_id=case["case_id"],
                relation=relation, root_layer=case["level"], source_layer=source_layer,
                condition="masked_rewired", replicate=REWIRE_REPLICATES,
                relation_edges=len(pairs), **averaged))

    rows = pd.DataFrame(records)
    audit = pd.DataFrame(integrity)
    rows.to_csv(OUT / "mechanism_control_cases.csv", index=False)
    audit.to_csv(OUT / "rewiring_integrity.csv", index=False)
    summary = summarize(rows)
    summary.to_csv(OUT / "mechanism_control_summary.csv", index=False)
    contrasts = contrast_table(rows)
    contrasts.to_csv(OUT / "mechanism_control_paired_ci.csv", index=False)
    labels = classify(contrasts)
    labels.to_csv(OUT / "mechanism_classification.csv", index=False)

    relation_coverage = (audit.groupby(["dataset", "relation", "root_layer"], as_index=False)
        .agg(total_cases=("case_id", "nunique"), mean_edges=("edges", "mean"),
             replicates=("replicate", lambda x: int((x >= 0).sum())),
             swap_success_rate=("accepted_swaps", lambda x: float((x > 0).mean())),
             mean_fraction_edges_changed=("fraction_edges_changed", "mean"),
             all_source_degrees_exact=("exact_source_degree", "all"),
             all_target_degrees_exact=("exact_target_degree", "all"),
             all_duplicate_free=("duplicate_free", "all")))
    usable = (audit[audit.usable_for_relation_mechanism]
              .groupby(["dataset", "relation", "root_layer"])["case_id"].nunique())
    relation_coverage["usable_cases"] = [int(usable.get((r.dataset, r.relation, r.root_layer), 0))
                                          for r in relation_coverage.itertuples()]
    relation_coverage["excluded_no_edge"] = relation_coverage.total_cases - relation_coverage.usable_cases
    relation_coverage.to_csv(OUT / "rewiring_summary.csv", index=False)

    protocol = {
        "experiment": "symmetric relation evidence/structure controls",
        "datasets": {"lsdb": 88, "aiops2025": 272},
        "relations": RELATIONS,
        "evaluation": "native root layer only; exact-tie expected Top-k and Avg@5 primary",
        "propagation": "frozen one-step bidirectional source-row normalization, lambda=0.1",
        "target_mask": "set every base score in evaluated root layer to zero",
        "permuted_source": f"{PERMUTATION_REPLICATES} deterministic within-source-layer permutations per case; topology and score multiset fixed",
        "uniform_source": "target/unrelated bases zero; every opposite-endpoint source base set to one",
        "degree_prior": "incident count of selected typed relation; no telemetry or label input",
        "rewiring": f"{REWIRE_REPLICATES} typed directed bipartite 2-switch replicates; exact source/target degrees and edge count preserved",
        "uncertainty": "5000 paired case bootstrap replicates after averaging random controls within case",
        "classification": {
            "evidence-supported compensation": "masked real > mapping, permuted-source, and uniform-source with Avg@5 CI lower bounds > 0",
            "recovery not separable from controls": "masked real > mapping, but at least one evidence/structure contrast remains uncertain",
            "harmful": "masked real - mapping Avg@5 CI upper bound < 0",
            "unsupported_or_uncertain": "all other cases",
        },
        "interpretation_limit": "operational contrasts, not an identified causal decomposition",
    }
    dump(OUT / "protocol.json", protocol)

    source_hashes = {str(path): sha(path) for path in [Path(__file__),
        ROOT / "tools/complete_entity_observation_controls.py",
        ROOT / "tools/evaluate_aiops2025_observability_and_rewire.py",
        *[ROOT / "src/ls_baro" / name for name in ("canonical_core.py", "topology.py", "scoring.py")]]}
    verification = {
        "complete": True,
        "historical_replay_checks": len(replay_checks),
        "historical_replay_all_passed": all(row["rank_equal"] for row in replay_checks),
        "control_case_rows": len(rows),
        "rewiring_audit_rows": len(audit),
        "all_rewirings_preserve_source_degree": bool(audit.exact_source_degree.all()),
        "all_rewirings_preserve_target_degree": bool(audit.exact_target_degree.all()),
        "all_rewirings_duplicate_free": bool(audit.duplicate_free.all()),
        "source_sha256": source_hashes,
        "root_labels_used_for": "native-layer evaluation only; not score permutation, uniform control, degree prior, or rewiring",
        "mechanism_cohort": "cases with at least one selected typed relation edge; total and excluded no-edge counts reported",
    }
    dump(OUT / "verification.json", verification)

    lines = [
        "# Relation evidence mechanisms: symmetric controls", "",
        "Frozen mapped scores, candidates, propagation equation, lambda, and native-layer evaluation are unchanged.",
        "Source-score permutations preserve the true topology and source-layer score multiset while destroying score/entity alignment.",
        "Typed rewiring preserves relation type, edge count, and every source/target degree. Intervals are paired case-bootstrap 95% intervals.", "",
        "## Mechanism classification (tie-expected Avg@5)", "",
        "Labels are conservative operational decisions, not a causal decomposition.", "",
        "| Dataset | Relation | Root layer | Native effect | Complete-mask mechanism | Total recovery pp (95% CI) | Real-permuted pp (95% CI) | Real-uniform pp (95% CI) | Real-degree pp (95% CI) | Real-rewired pp (95% CI) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in labels.itertuples():
        lines.append(f"| {row.dataset} | {row.relation} | {row.root_layer} | {row.native_effect_label} | {row.masked_mechanism_label} | {row.masked_total_diff_pp:.2f} {row.masked_total_ci} | {row.real_minus_permuted_pp:.2f} {row.real_minus_permuted_ci} | {row.real_minus_uniform_pp:.2f} {row.real_minus_uniform_ci} | {row.real_minus_degree_pp:.2f} {row.real_minus_degree_ci} | {row.real_minus_rewired_pp:.2f} {row.real_minus_rewired_ci} |")
    lines += ["", "## Symmetric performance table", "",
        "| Dataset | Relation | Root layer | Condition | N | Tie Top-1 | Tie Top-3 | Tie Top-5 | Tie Avg@5 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|"]
    order = ["native_mapping", "native_real", "masked_mapping", "masked_real",
             "masked_permuted_source", "masked_uniform_source", "masked_degree_prior", "masked_rewired"]
    summary["condition_order"] = summary.condition.map({name: i for i, name in enumerate(order)})
    for row in summary.sort_values(["dataset", "relation", "root_layer", "condition_order"]).itertuples():
        lines.append(f"| {row.dataset} | {row.relation} | {row.root_layer} | {row.condition} | {row.cases} | {row.tie_top1:.2%} | {row.tie_top3:.2%} | {row.tie_top5:.2%} | {row.tie_avg5:.4f} |")
    lines += ["", "## Rewiring integrity", "",
        "Mechanism contrasts require at least one selected relation edge. Cases without an observed edge remain in the full-dataset native analysis but are excluded from these relation-mechanism contrasts.", "",
        "| Dataset | Relation | Root layer | Total cases | Usable cases | Excluded: no edge | Mean edges | Swap success | Mean changed edges | Exact degrees | Duplicate-free |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for row in relation_coverage.itertuples():
        exact = bool(row.all_source_degrees_exact and row.all_target_degrees_exact)
        lines.append(f"| {row.dataset} | {row.relation} | {row.root_layer} | {row.total_cases} | {row.usable_cases} | {row.excluded_no_edge} | {row.mean_edges:.1f} | {row.swap_success_rate:.2%} | {row.mean_fraction_edges_changed:.2%} | {exact} | {bool(row.all_duplicate_free)} |")
    lines += ["", "## Interpretation rule", "",
        "Evidence-supported compensation requires a positive complete-mask gain over Mapping and positive real-minus-permuted and real-minus-uniform Avg@5 intervals. A positive gain over Mapping without those controls is reported as recovery not separable from controls. A native or masked decrease is reported only when the paired interval lies wholly below zero.", "",
        "## Limits", "",
        "The controls identify whether recovery exceeds specific null models. They do not prove physical fault propagation, universal dataset transfer, or medical-domain accuracy. A confidence interval crossing zero is uncertainty, not equivalence."]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(labels.to_string(index=False), flush=True)
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
