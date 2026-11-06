"""Audit how AIOps2022 and RCAEval RE2 support the mechanism paper.

This script does not rerun either historical experiment. It performs paired
case summaries from the saved case-level outputs, records provenance, and
assigns conservative evidence roles relative to the new mechanism protocol.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output/extended_dataset_roles_20261028"
DEFAULT_AI_DIR = ROOT / "output/aiops2022-native-20220321-cloudbed2"
DEFAULT_RE2_DIR = ROOT.parent / "baro-main/output/re2_heterogeneous_dedup_components_l0p05_forward"


def seed(*parts):
    raw = "|".join(map(str, parts)).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def interval(values, *key):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed(*key))
    draws = values[rng.integers(0, len(values), size=(5000, len(values)))].mean(axis=1)
    return dict(difference_pp=100 * float(values.mean()),
                ci95_low_pp=100 * float(np.quantile(draws, .025)),
                ci95_high_pp=100 * float(np.quantile(draws, .975)), cases=len(values))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="Replay the AIOps2022 and RCAEval RE2 supporting analyses."
    )
    parser.add_argument("--aiops2022-dir", type=Path, default=DEFAULT_AI_DIR)
    parser.add_argument("--re2-dir", type=Path, default=DEFAULT_RE2_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.output.resolve()
    ai_dir = args.aiops2022_dir.resolve()
    re2_dir = args.re2_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    ai = pd.read_csv(ai_dir / "cases.csv")
    re2 = pd.read_csv(re2_dir / "component_ablation_cases.csv")
    # The historical component runner stored reciprocal rank in its `avg5`
    # field. Recompute the declared Avg@5 definition from the saved rank.
    saved_re2_avg5_is_rr = bool(np.allclose(re2["avg5"], 1.0 / re2["rank"]))
    re2["avg5_recomputed"] = np.where(re2["rank"] <= 5, (6.0 - re2["rank"]) / 5.0, 0.0)
    ai_records = []
    for scope, group in ai.groupby("scope"):
        pivot = group.pivot(index="case_id", columns="arm")
        for left, right in (("Containment-only", "Mapping-only"),
                            ("Full", "Dependency-only")):
            for metric in ("top1", "top3", "top5", "mrr"):
                values = pivot[metric][left] - pivot[metric][right]
                ai_records.append(dict(dataset="AIOps2022", subset="2022-03-21-cloudbed2",
                    scope=scope, comparison=f"{left}-{right}", metric=metric,
                    left_mean=float(pivot[metric][left].mean()),
                    right_mean=float(pivot[metric][right].mean()),
                    **interval(values, "aiops2022", scope, left, right, metric)))
    ai_paired = pd.DataFrame(ai_records)
    ai_paired.to_csv(out / "aiops2022_paired_ci.csv", index=False)

    re2_records = []
    for component in ("ownership", "ownership_service_calls",
                      "ownership_operation_calls", "full"):
        pivot = re2[re2.component.isin(["baro_only", component])].pivot(
            index="dataset_id", columns="component")
        for metric in ("top1", "top3", "top5", "avg5_recomputed"):
            values = pivot[metric][component] - pivot[metric]["baro_only"]
            re2_records.append(dict(dataset="RCAEval RE2", component=component,
                comparison=f"{component}-baro_only",
                metric="avg5" if metric == "avg5_recomputed" else metric,
                component_mean=float(pivot[metric][component].mean()),
                baro_mean=float(pivot[metric]["baro_only"].mean()),
                **interval(values, "re2", component, metric)))
    re2_paired = pd.DataFrame(re2_records)
    re2_paired.to_csv(out / "re2_component_paired_ci.csv", index=False)

    roles = [
        dict(dataset="AIOps2022", cases=51, role="adapter applicability boundary",
             usable_for_core_mechanism=False,
             reason="Only one of the available cloudbed subsets was processed; the saved run has no matched score-permutation, uniform-source, or degree-preserving rewiring controls."),
        dict(dataset="RCAEval RE2", cases=90, role="supporting heterogeneous-relation ablation",
             usable_for_core_mechanism=False,
             reason="Complete case-level component ablation exists, but the graph has Metric/LogSource/Operation/Service entities and no native Host-Pod Deployment; scoring, direction, lambda, and propagation steps differ from the core protocol."),
    ]
    pd.DataFrame(roles).to_csv(out / "dataset_roles.csv", index=False)

    verification = dict(complete=True,
        aiops2022=dict(available_labels=300, evaluated_isolated_cases=51,
                       subsets_evaluated=1, saved_rows=len(ai)),
        re2=dict(cases=90, components=sorted(re2.component.unique().tolist()), saved_rows=len(re2),
                 historical_avg5_field_equals_reciprocal_rank=saved_re2_avg5_is_rr,
                 avg5_used_in_this_audit="recomputed from rank as mean Top-1 through Top-5"),
        source_sha256={str(path): sha(path) for path in [
            Path(__file__), ai_dir / "cases.csv", ai_dir / "summary.json",
            re2_dir / "component_ablation_cases.csv", re2_dir / "run_config.json"]})
    (out / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")

    lines = ["# Extended-dataset evidence roles", "",
        "These saved experiments remain useful, but they do not satisfy the full evidence-versus-structure protocol and are therefore supporting or boundary analyses rather than core mechanism evidence.", "",
        "## AIOps2022 exploratory subset", "",
        "The source contains 300 labels, while the saved isolated experiment evaluates 51 cases from one cloudbed subset. It is retained to show adapter/task sensitivity, not as a cross-dataset confirmation.", "",
        "| Scope | Comparison | Metric | Before | After | Difference pp | Paired 95% CI pp |",
        "|---|---|---|---:|---:|---:|---|"]
    for row in ai_paired.itertuples():
        lines.append(f"| {row.scope} | {row.comparison} | {row.metric} | {row.right_mean:.4f} | {row.left_mean:.4f} | {row.difference_pp:.2f} | [{row.ci95_low_pp:.2f}, {row.ci95_high_pp:.2f}] |")
    lines += ["", "## RCAEval RE2 heterogeneous component ablation", "",
        "The 90-case saved experiment compares BARO-only Service rankings with LogSource/Operation ownership and call relations. It uses forward two-step propagation with lambda=0.05, so it is not pooled with the one-step bidirectional core protocol. The historical case file's `avg5` field equals reciprocal rank; the table below recomputes Avg@5 from the saved rank as the mean of Top-1 through Top-5.", "",
        "| Component vs BARO only | Metric | BARO | Component | Difference pp | Paired 95% CI pp |",
        "|---|---|---:|---:|---:|---|"]
    for row in re2_paired.itertuples():
        lines.append(f"| {row.component} | {row.metric} | {row.baro_mean:.4f} | {row.component_mean:.4f} | {row.difference_pp:.2f} | [{row.ci95_low_pp:.2f}, {row.ci95_high_pp:.2f}] |")
    lines += ["", "## Use in the paper", "",
        "AIOps2022 is an applicability-boundary result: the partial adapter run did not reproduce a stable containment gain and lacks the controls needed for mechanism classification.",
        "RE2 is a supporting relation-ablation result: adding heterogeneous ownership/call relations changes little relative to a strong BARO ranking and cannot answer the Host/Pod/Service observation-masking question.",
        "Neither result is discarded, but neither is used to claim transferable evidence compensation. Their role is to delimit when the core protocol can and cannot be instantiated from the available graph and labels."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(ai_paired.to_string(index=False), flush=True)
    print(re2_paired.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
