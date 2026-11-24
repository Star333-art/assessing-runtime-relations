"""Replay eligible-cohort rewiring and clustered sensitivity diagnostics."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from evaluate_mechanism_holm import EXPECTED_CASES, CONDITIONS

REPOSITORY = Path(__file__).resolve().parents[3]
TASK = ["dataset", "relation", "root_layer"]
INPUTS = dict(core_cases="relation_mechanism_cases.csv", microcercl_cases="microcercl_relation_cases.csv",
    filtered_cases="microcercl_shared_source_case_results.csv", core_integrity="relation_rewiring_integrity.csv",
    micro_deployment_integrity="microcercl_deployment_rewiring_integrity.csv",
    micro_ownership_integrity="microcercl_rewiring_integrity.csv", labels="microcercl_case_metadata.csv",
    observation_integrity="aiops2025_observation_rewiring_integrity.csv")


def eligible_cases(core, micro, filtered):
    parts = [core[TASK + ["case_id", "condition"]]]
    for frame, relation in ((micro, "deployment"), (filtered, "ownership")):
        frame = frame[frame.relation.eq(relation)].copy()
        frame["dataset"], frame["root_layer"] = "microcercl", "pod"
        parts.append(frame[TASK + ["case_id", "condition"]])
    rows = pd.concat(parts, ignore_index=True)
    rows = rows[rows.condition.isin(CONDITIONS)]
    keys = TASK + ["case_id"]
    if rows.duplicated(keys + ["condition"]).any():
        raise ValueError("Duplicate case-condition rows in mechanism cohorts.")
    if not rows.groupby(keys).condition.apply(lambda x: set(x) == set(CONDITIONS)).all():
        raise ValueError("Each eligible case must have all six matched conditions.")
    cases = rows[keys].drop_duplicates()
    if cases.groupby(TASK).size().to_dict() != EXPECTED_CASES:
        raise ValueError("Unexpected eligible cohorts.")
    return cases


def validate_integrity(frame, eligible, repeats=20):
    """Exclude no-edge placeholders, retaining genuine zero-swap replicates."""
    keys = TASK + ["case_id"]
    rows = frame.merge(eligible, on=keys, how="inner", validate="many_to_one")
    rows = rows[rows.replicate.ge(0)].copy()
    if rows.duplicated(keys + ["replicate"]).any():
        raise ValueError("Duplicate rewiring replicate.")
    if set(map(tuple, rows[keys].drop_duplicates().to_numpy())) != set(map(tuple, eligible.to_numpy())):
        raise ValueError("Missing rewiring audit for eligible cases.")
    if not rows.groupby(keys).replicate.apply(lambda x: set(x) == set(range(repeats))).all():
        raise ValueError("Each eligible case requires exactly replicates 0 through 19.")
    flags = ["exact_source_degree", "exact_target_degree", "duplicate_free"]
    if not rows[flags].apply(lambda x: x.astype(str).str.lower().eq("true")).all().all():
        raise ValueError("A retained rewire failed exact degree or duplicate checks.")
    if (rows.edges.le(0).any() or rows.accepted_swaps.lt(0).any()
            or not rows.fraction_edges_changed.between(0, 1).all()):
        raise ValueError("Invalid eligible rewiring values.")
    return rows


def summarize_integrity(rows):
    records = []
    for task, group in rows.groupby(TASK):
        changed = float(group.fraction_edges_changed.mean())
        records.append(dict(zip(TASK, task), cases=group.case_id.nunique(), replicates=len(group),
            successful_replicates=int(group.accepted_swaps.gt(0).sum()),
            zero_swap_replicates=int(group.accepted_swaps.eq(0).sum()),
            no_nontrivial_cases=int(group.groupby("case_id").accepted_swaps.max().eq(0).sum()),
            minimum=int(group.accepted_swaps.min()), median=float(group.accepted_swaps.median()),
            maximum=int(group.accepted_swaps.max()), mean_changed=changed, mean_retained=1-changed,
            all_degrees_exact=True, all_duplicate_free=True))
    return pd.DataFrame(records)


def cluster_sensitivity(wide, cluster):
    records = []
    for app, frame in wide.groupby("application"):
        groups = list(frame.groupby(cluster))
        for control in CONDITIONS[1:]:
            sums = np.array([(g.masked_real-g[control]).sum() for _, g in groups])
            counts = np.array([len(g) for _, g in groups])
            draws = np.random.default_rng(20261106).integers(0, len(groups), (5000, len(groups)))
            boot = sums[draws].sum(axis=1)/counts[draws].sum(axis=1)
            leave = (sums.sum()-sums)/(counts.sum()-counts) if len(groups)>1 else np.array([np.nan])
            record = dict(application=app, control=control, low=float(np.quantile(boot,.025)), high=float(np.quantile(boot,.975)))
            if cluster == "pod_reference":
                record.update(cases=len(frame), pod_groups=len(groups), difference=float(sums.sum()/counts.sum()),
                    leave_one_pod_min=float(leave.min()), leave_one_pod_max=float(leave.max()))
            else:
                record.update(groups=len(groups), leave_min=float(leave.min()), leave_max=float(leave.max()))
            records.append(record)
    return pd.DataFrame(records)


def run(args):
    paths = {name: getattr(args, name).resolve() for name in INPUTS}
    output = args.output.resolve()
    if any(path == output or path.is_relative_to(output) for path in paths.values()):
        raise ValueError("Use an output separate from the frozen input directory.")
    data = {name: pd.read_csv(path, dtype={"case_id": str}) for name, path in paths.items()}
    eligible = eligible_cases(data["core_cases"], data["microcercl_cases"], data["filtered_cases"])
    frames = [data["core_integrity"]]
    for name, relation in (("micro_deployment_integrity", "deployment"), ("micro_ownership_integrity", "ownership")):
        frame = data[name].copy()
        frame = frame[frame.relation.eq(relation)]
        frame["dataset"], frame["root_layer"] = "microcercl", "pod"
        frame["exact_source_degree"] = frame["exact_target_degree"] = frame.exact_degrees
        frames.append(frame)
    summary = summarize_integrity(validate_integrity(pd.concat(frames, ignore_index=True), eligible))
    labels = data["labels"][["case_id", "pod_reference", "deployment_hosts"]]
    if labels.duplicated("case_id").any():
        raise ValueError("Duplicate metadata case IDs.")
    wide = data["filtered_cases"].pivot(index=["application", "case_id"], columns="condition", values="tie_avg5").reset_index()
    wide = wide.merge(labels, on="case_id", validate="one_to_one", how="left")
    if len(wide) != 615 or wide[["pod_reference", "deployment_hosts"]].isna().any().any():
        raise ValueError("All 615 cases require Pod and host-placement metadata.")
    wide["placement_group"] = wide.deployment_hosts.map(lambda x: "|".join(sorted(json.loads(x))))
    output.mkdir(parents=True, exist_ok=True)
    def save(name, frame):
        frame.to_csv(output / f"{name}.csv", index=False, lineterminator="\n")
    save("eligible_cases", eligible)
    save("rewiring_task_summary", summary)
    cs = summary[summary.dataset.ne("microcercl")].rename(columns={"mean_changed": "changed"})
    save("lsdb_swap_counts", cs[TASK+["replicates", "minimum", "median", "maximum", "changed"]])
    save("aiops2025_swap_counts", summary[summary.dataset.eq("aiops2025")])
    ms = summary[summary.dataset.eq("microcercl")].rename(columns={"minimum":"swaps_min", "median":"swaps_median", "maximum":"swaps_max"})
    save("microcercl_swap_summary", ms[["relation", "replicates", "swaps_min", "swaps_median", "swaps_max", "mean_changed", "mean_retained"]])
    save("microcercl_matched_cases", wide.drop(columns=["deployment_hosts", "placement_group"]))
    save("placement_case_list", wide)
    save("pod_cluster_sensitivity", cluster_sensitivity(wide, "pod_reference"))
    save("placement_cluster_sensitivity", cluster_sensitivity(wide, "placement_group"))
    full = data["core_integrity"].groupby(TASK, as_index=False).agg(cases=("case_id", "nunique"),
        rows=("replicate", "size"), actual_replicates=("replicate", lambda x: int(x.ge(0).sum())),
        no_edge_placeholders=("replicate", lambda x: int(x.lt(0).sum())))
    save("full_cohort_relation_diagnostics", full)
    obs = data["observation_integrity"]
    save("full_cohort_observation_swap_counts", pd.DataFrame([
        dict(relation=r, cases=obs.case_id.nunique(), replicates=len(obs), minimum=int(obs[r+"_accepted_swaps"].min()),
            median=float(obs[r+"_accepted_swaps"].median()), maximum=int(obs[r+"_accepted_swaps"].max()),
            retained=float(1-obs[r+"_fraction_edges_changed"].mean())) for r in ("deployment", "ownership")]))
    protocol = dict(seed=20261106, repeats=5000, cluster="native Pod identity within application",
        weighting="case-weighted ratio of resampled cluster sums to sizes",
        limitation="Pod clusters do not establish independent deployments; exploratory only",
        rewiring_cohort="same task-specific eligible case IDs as the mechanism contrasts",
        rewiring_replicates_per_eligible_case=20, includes_genuine_zero_swap_replicates=True,
        excludes_no_edge_placeholders=True, full_cohort_diagnostics="separate full_cohort_*.csv outputs",
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        inputs={n:dict(filename=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for n,p in paths.items()})
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(summary.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=REPOSITORY / "results/core_evidence")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "reproduced/final_requirements")
    for name in INPUTS:
        parser.add_argument("--"+name.replace("_", "-"), type=Path)
    args = parser.parse_args()
    for name, filename in INPUTS.items():
        if getattr(args, name) is None:
            setattr(args, name, args.evidence_dir / filename)
    run(args)


if __name__ == "__main__":
    main()
