"""Run ordered raw-data stages with explicit dataset and intermediate locations."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "src"))
from ls_baro.experiment_paths import dataset_path, output_path


def build_plan(args):
    work = args.output.resolve()
    jobs = []
    def add(name, script, *options, requires=()):
        jobs.append(dict(stage=name, command=[sys.executable, str(PACKAGE/script), *map(str,options)],
            requires=[str(p) for p in requires]))
    if "lsdb" in args.datasets:
        add("lsdb-native", "run_ls_relation_split.py", "--dataset-root", args.lsdb_root,
            "--output",work/"lsdb_relation_split_l0p1", requires=[args.lsdb_root/"labels/label.json"])
        add("lsdb-coverage", "tools/verify_lsdb_coverage.py", "--dataset-root",args.lsdb_root,
            "--output",work/"lsdb_relation_split_l0p1", requires=[work/"lsdb_relation_split_l0p1/details"])
    if "aiops2025" in args.datasets:
        add("aiops2025-native", "tools/evaluate_aiops2025_ownership.py", "--root",args.aiops2025_root,
            "--out",work/"aiops2025_ownership_pilot", requires=[args.aiops2025_root/"groundtruth.jsonl"])
        add("aiops2025-summary", "tools/summarize_aiops2025_lsdb_protocol.py",
            requires=[work/"aiops2025_ownership_pilot/case_results.csv"])
        add("aiops2025-audit", "tools/analyze_aiops2025_ownership.py", requires=[work/"aiops2025_ownership_pilot/rankings.csv"])
        add("aiops2025-observation", "tools/evaluate_aiops2025_observability_and_rewire.py",
            requires=[work/"aiops2025_lsdb_protocol_20261023/label_audit.csv"])
    if "microcercl" in args.datasets:
        add("microcercl-native", "tools/audit_microcercl.py", "--root",args.microcercl_root,
            "--output",work/"microcercl_full", "--run", requires=[args.microcercl_root])
        add("microcercl-hipster", "run_microcercl_hipster_relation_split.py", "--root",args.microcercl_root,
            "--historical",work/"microcercl_full/results.jsonl", "--output",work/"microcercl_hipster_relation_split_l0p1",
            requires=[work/"microcercl_full/results.jsonl"])
        add("microcercl-layers", "tools/evaluate_microcercl_layers.py",
            requires=[work/"microcercl_full/results.jsonl",work/"microcercl_hipster_relation_split_l0p1/relation_cases.csv"])
        add("microcercl-summary", "tools/summarize_microcercl_lsdb_protocol.py",
            requires=[work/"microcercl_layer_evaluation_20261023/case_results.csv"])
        add("microcercl-mechanisms", "tools/evaluate_microcercl_relation_mechanisms.py",
            requires=[work/"microcercl_layer_evaluation_20261023/label_audit.csv"])
        add("microcercl-filtered", "tools/evaluate_microcercl_shared_source_sensitivity.py",
            requires=[work/"microcercl_relation_mechanisms_20261028/mechanism_control_cases.csv"])
    if {"lsdb","aiops2025"} <= set(args.datasets):
        add("core-observation", "tools/complete_entity_observation_controls.py",
            requires=[work/"lsdb_relation_split_l0p1/details",work/"aiops2025_observability_rewire_20261026"])
        add("core-mechanisms", "tools/evaluate_relation_evidence_mechanisms.py",
            requires=[work/"lsdb_relation_split_l0p1/details",work/"aiops2025_observability_rewire_20261026"])
    if set(args.datasets) == {"lsdb","aiops2025","microcercl"}:
        add("lambda-sensitivity", "tools/evaluate_lambda_mask_sensitivity.py",
            requires=[work/"microcercl_layer_evaluation_20261023/label_audit.csv"])
        add("final-audit", "tools/audit_final_requirements.py", "--output",work/"final_requirements",
            "--core-cases",work/"relation_evidence_mechanisms_20261028/mechanism_control_cases.csv",
            "--microcercl-cases",work/"microcercl_relation_mechanisms_20261028/mechanism_control_cases.csv",
            "--filtered-cases",work/"microcercl_shared_source_sensitivity_20261103/mechanism_control_cases.csv",
            "--core-integrity",work/"relation_evidence_mechanisms_20261028/rewiring_integrity.csv",
            "--micro-deployment-integrity",work/"microcercl_relation_mechanisms_20261028/rewiring_integrity.csv",
            "--micro-ownership-integrity",work/"microcercl_shared_source_sensitivity_20261103/rewiring_integrity.csv",
            "--labels",work/"microcercl_layer_evaluation_20261023/label_audit.csv",
            "--observation-integrity",work/"aiops2025_observability_rewire_20261026/rewiring_integrity.csv")
    if args.stages:
        unknown = set(args.stages)-{j["stage"] for j in jobs}
        if unknown:
            raise ValueError(f"Unavailable stages for the selected datasets: {sorted(unknown)}")
        jobs = [j for j in jobs if j["stage"] in args.stages]
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", choices=["lsdb","aiops2025","microcercl"], default=["lsdb","aiops2025","microcercl"])
    for name in ("lsdb","aiops2025","microcercl"):
        parser.add_argument("--"+name+"-root", type=Path, default=dataset_path(name))
    parser.add_argument("--output", type=Path, default=output_path())
    parser.add_argument("--stages", nargs="+", help="Run only named stages using existing upstream outputs")
    parser.add_argument("--dry-run", action="store_true", help="Print the complete ordered command plan without running experiments")
    args = parser.parse_args()
    for name in ("lsdb","aiops2025","microcercl"):
        setattr(args,name+"_root",getattr(args,name+"_root").expanduser().resolve())
    plan = build_plan(args)
    environment = dict(os.environ, LS_BARO_OUTPUT_ROOT=str(args.output.resolve()),
        **{f"LS_BARO_{name.upper()}_ROOT":str(getattr(args,name+"_root")) for name in ("lsdb","aiops2025","microcercl")})
    if args.dry_run:
        print(json.dumps(plan,indent=2)); return
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/"run_plan.json").write_text(json.dumps(plan,indent=2)+"\n",encoding="utf-8",newline="\n")
    for job in plan:
        missing = [p for p in job["requires"] if not Path(p).exists()]
        if missing:
            raise FileNotFoundError(f"{job['stage']} requires {missing}; run its preceding stages or provide the correct data paths")
        print(f"Running {job['stage']}",flush=True)
        subprocess.run(job["command"],env=environment,check=True,cwd=PACKAGE)


if __name__ == "__main__":
    main()
