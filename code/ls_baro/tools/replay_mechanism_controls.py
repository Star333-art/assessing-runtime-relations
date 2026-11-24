"""Replay complete-mask means, paired intervals and labels from frozen cases."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
import evaluate_relation_evidence_mechanisms as core
import evaluate_microcercl_relation_mechanisms as micro
import evaluate_microcercl_shared_source_sensitivity as filtered
from evaluate_mechanism_holm import EXPECTED_CASES, CONDITIONS
from ls_baro.mechanism_labels import RULES

REPOSITORY = Path(__file__).resolve().parents[3]


def replay(evidence):
    # Select Avg@5 without changing the original generators' seeds or algorithms.
    core.REPORTED = micro.REPORTED = filtered.REPORTED = ["tie_avg5"]
    inputs = [evidence / name for name in ("relation_mechanism_cases.csv",
        "microcercl_relation_cases.csv", "microcercl_shared_source_case_results.csv")]
    c, m, f = [pd.read_csv(path, dtype={"case_id": str}) for path in inputs]
    m = m[m.relation.eq("deployment")].copy()
    ci = core.contrast_table(c)
    mi = micro.contrasts(m)
    fi = filtered.contrasts(f)
    parts = [ci]
    for frame in (mi, fi):
        frame = frame[frame.scope.eq("All")].drop(columns="scope").copy()
        frame["dataset"] = "microcercl"
        frame["interpretation"] = frame.interpretation.replace({"score-endpoint-alignment":"evidence-alignment",
            "uniform-score-structure":"uniform-structure", "endpoint-association":"relation-semantics"})
        parts.append(frame)
    contrasts = pd.concat(parts, ignore_index=True)
    case_parts = [c]
    for frame in (m, f):
        frame["dataset"], frame["root_layer"] = "microcercl", "pod"
        case_parts.append(frame)
    cases = pd.concat(case_parts, ignore_index=True)
    keys = ["dataset", "relation", "root_layer"]
    matched = cases[cases.condition.isin(CONDITIONS)]
    if matched.duplicated(keys+["case_id", "condition"]).any():
        raise ValueError("Duplicate matched case-condition rows.")
    pivot = matched.pivot(index=keys+["case_id"], columns="condition", values="tie_avg5")
    if set(pivot.columns) != set(CONDITIONS) or pivot.isna().any().any():
        raise ValueError("Unmatched controls.")
    if matched.groupby(keys).case_id.nunique().to_dict() != EXPECTED_CASES:
        raise ValueError("Unexpected task cohorts.")
    summary = cases.groupby(keys+["condition"], as_index=False).agg(cases=("case_id","nunique"), tie_avg5=("tie_avg5","mean"))
    return summary, contrasts, core.classify(contrasts), inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=REPOSITORY / "results/core_evidence")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "reproduced/mechanism_controls")
    args = parser.parse_args()
    summary, contrasts, labels, inputs = replay(args.evidence_dir.resolve())
    out = args.output.resolve()
    if any(path.is_relative_to(out) for path in inputs):
        raise ValueError("Use an output separate from the frozen input directory.")
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in (("summary",summary), ("paired_ci",contrasts), ("classification",labels)):
        frame.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    sources = [Path(__file__), Path(core.__file__), Path(micro.__file__), Path(filtered.__file__),
        REPOSITORY / "code/ls_baro/src/ls_baro/mechanism_labels.py"]
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    protocol = dict(metric="tie_avg5", bootstrap_repeats=5000, classification_rules=RULES,
        interpretation="exploratory nominal paired case-bootstrap labels; separate from Holm sensitivity",
        source_policy="MicroCERCL Ownership excludes instance-aggregated Service resources; other tasks use original sources",
        inputs={p.name:sha(p) for p in inputs}, sources={p.relative_to(REPOSITORY).as_posix():sha(p) for p in sources})
    (out/"protocol.json").write_text(json.dumps(protocol,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(labels[["dataset","relation","root_layer","masked_mechanism_label"]].to_string(index=False))


if __name__ == "__main__":
    main()
