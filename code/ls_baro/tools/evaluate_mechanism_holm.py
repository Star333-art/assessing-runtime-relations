"""Replay the 50 complete-mask Wilcoxon comparisons and global Holm adjustment."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy.stats import wilcoxon


REPOSITORY = Path(__file__).resolve().parents[3]
EVIDENCE = REPOSITORY / "results/core_evidence"
CONDITIONS = (
    "masked_real", "masked_mapping", "masked_permuted_source",
    "masked_uniform_source", "masked_degree_prior", "masked_rewired",
)
CONTROLS = ("Mapping", "Permutation", "Uniform", "Degree", "Rewired")
TASK_COLUMNS = ["dataset", "relation", "root_layer"]
EXPECTED_CASES = {
    ("lsdb", "deployment", "host"): 12,
    ("lsdb", "deployment", "pod"): 26,
    ("lsdb", "ownership", "pod"): 26,
    ("lsdb", "ownership", "service"): 50,
    ("aiops2025", "deployment", "host"): 81,
    ("aiops2025", "deployment", "pod"): 68,
    ("aiops2025", "ownership", "pod"): 62,
    ("aiops2025", "ownership", "service"): 100,
    ("microcercl", "deployment", "pod"): 615,
    ("microcercl", "ownership", "pod"): 615,
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_cases(core_path, deployment_path, filtered_path):
    # Keep the CSV parser used by the existing analyses. No rounding or
    # near-zero threshold is applied before computing signed-rank differences.
    core = pd.read_csv(core_path, dtype={"case_id": str})
    core["application"] = ""
    parts = [core]
    for path, relation in ((deployment_path, "deployment"), (filtered_path, "ownership")):
        frame = pd.read_csv(path, dtype={"case_id": str})
        frame = frame[frame.application.isin(["Hipster", "SockShop"]) & frame.relation.eq(relation)].copy()
        frame["dataset"] = "microcercl"
        frame["root_layer"] = "pod"
        parts.append(frame)
    cases = pd.concat(parts, ignore_index=True)
    cases = cases[cases.condition.isin(CONDITIONS)].copy()
    cases["source_policy"] = np.where(
        cases.dataset.eq("microcercl") & cases.relation.eq("ownership"),
        "exclude_instance_aggregated_service_resources", "original",
    )
    return cases


def holm_adjust(p_values):
    values = np.asarray(p_values, dtype=float)
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("P-values must be finite and lie in [0, 1].")
    order = np.argsort(values, kind="stable")
    adjusted = np.minimum(1.0, np.maximum.accumulate(values[order] * np.arange(len(values), 0, -1)))
    restored = np.empty_like(adjusted)
    restored[order] = adjusted
    return restored


def evaluate(cases):
    if cases[TASK_COLUMNS + ["application", "case_id", "condition", "source_policy"]].isna().any().any():
        raise ValueError("Case identifiers, conditions, and source policy must be present.")
    values = pd.to_numeric(cases.tie_avg5, errors="raise")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("tie_avg5 must be finite and lie in [0, 1].")
    case_columns = TASK_COLUMNS + ["application", "case_id", "condition"]
    if cases.duplicated(case_columns).any():
        raise ValueError("Duplicate case-condition rows: average random controls within each case first.")
    groups = dict(tuple(cases.groupby(TASK_COLUMNS, sort=True)))
    if set(groups) != set(EXPECTED_CASES):
        raise ValueError("The input must contain exactly the ten prespecified tasks.")
    records = []
    for task, group in groups.items():
        paired = group.pivot(index=["application", "case_id"], columns="condition", values="tie_avg5")
        if set(paired.columns) != set(CONDITIONS) or paired.isna().any().any():
            raise ValueError(f"Unmatched controls for {task}.")
        if len(paired) != EXPECTED_CASES[task]:
            raise ValueError(f"Unexpected cohort size for {task}: {len(paired)}.")
        if task[0] == "microcercl":
            counts = group.groupby("application").case_id.nunique().to_dict()
            if counts != {"Hipster": 269, "SockShop": 346}:
                raise ValueError(f"Unexpected application counts for {task}: {counts}.")
        expected_policy = "exclude_instance_aggregated_service_resources" if task == ("microcercl", "ownership", "pod") else "original"
        if set(group.source_policy) != {expected_policy}:
            raise ValueError(f"Unexpected source policy for {task}.")
        for condition, control in zip(CONDITIONS[1:], CONTROLS):
            difference = paired.masked_real - paired[condition]
            nonzero = int(difference.ne(0).sum())
            if nonzero:
                test = wilcoxon(difference, alternative="greater", zero_method="wilcox", method="approx", correction=False)
                statistic, p_value = float(test.statistic), float(test.pvalue)
            else:
                statistic, p_value = 0.0, 1.0
            records.append(dict(zip(TASK_COLUMNS, task), source_policy=expected_policy,
                control=control, paired_cases=len(paired), nonzero_differences=nonzero,
                difference_pp=100 * float(difference.mean()), statistic=statistic, p_raw=p_value))
    contrasts = pd.DataFrame(records)
    contrasts["p_holm"] = holm_adjust(contrasts.p_raw)
    contrasts["raw_below_005"] = contrasts.p_raw.lt(0.05)
    contrasts["holm_below_005"] = contrasts.p_holm.lt(0.05)
    tasks = contrasts.groupby(TASK_COLUMNS + ["source_policy"], as_index=False).agg(
        comparisons=("control", "size"), raw_below_005=("raw_below_005", "sum"),
        holm_below_005=("holm_below_005", "sum"))
    return contrasts, tasks


def run(args):
    paths = {"core_cases": args.core_cases.resolve(), "microcercl_cases": args.microcercl_cases.resolve(),
             "filtered_cases": args.filtered_cases.resolve()}
    output = args.output.resolve()
    if any(path == output or path.is_relative_to(output) for path in paths.values()):
        raise ValueError("Use an output directory separate from the input snapshots.")
    cases = load_cases(paths["core_cases"], paths["microcercl_cases"], paths["filtered_cases"])
    contrasts, tasks = evaluate(cases)
    output.mkdir(parents=True, exist_ok=True)
    contrasts.to_csv(output / "paired_p_values.csv", index=False, lineterminator="\n")
    tasks.to_csv(output / "task_summary.csv", index=False, lineterminator="\n")
    summary = dict(comparisons=len(contrasts), raw_below_005=int(contrasts.raw_below_005.sum()),
        holm_below_005=int(contrasts.holm_below_005.sum()), alpha=0.05,
        interpretation="Exploratory sensitivity analysis; bootstrap classifications are retained separately.")
    protocol = dict(metric="tie_avg5", pair_unit="case; random controls already averaged within case",
        contrast="Real minus each reference/control", alternative="greater", zero_method="wilcox",
        method="approx", correction=False, all_zero_p=1.0, difference_rounding="none",
        csv_float_parser="pandas default", family_size=50, adjustment="Holm across all 50 tests",
        adjustment_before_display_rounding=True, expected_cases={"/".join(k): v for k, v in EXPECTED_CASES.items()},
        source_policies={"microcercl/ownership/pod": "exclude_instance_aggregated_service_resources",
                         "other_tasks": "original"},
        versions=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, scipy=scipy.__version__),
        source_sha256=sha256(__file__),
        inputs={name: dict(filename=path.name, sha256=sha256(path)) for name, path in paths.items()})
    for name, data in (("summary", summary), ("protocol", protocol)):
        (output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-cases", type=Path, default=EVIDENCE / "relation_mechanism_cases.csv")
    parser.add_argument("--microcercl-cases", type=Path, default=EVIDENCE / "microcercl_relation_cases.csv")
    parser.add_argument("--filtered-cases", type=Path, default=EVIDENCE / "microcercl_shared_source_case_results.csv")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "reproduced/mechanism_holm")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
