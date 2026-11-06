"""Evaluate propagation strength under native, partial, and complete masking.

The graph, base scores, relation arms, mask selections, and candidates are
fixed. Only the positive one-step propagation coefficient is changed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "lambda_sensitivity_20261103"
LAMBDAS = (0.05, 0.1, 0.2)
SEEDS = tuple(range(10))
RATES = {"native": None, "partial": 0.5, "complete": 1.0}

import sys
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
from complete_entity_observation_controls import (  # noqa: E402
    ARMS, _metric_seed, load_cases,
)
from ls_baro.topology import propagate_once  # noqa: E402
from complete_entity_observation_controls import _stats  # noqa: E402


def evaluate(case, base, arm, lam):
    scores, _ = propagate_once(
        dict(zip(case["ids"], map(float, base))), case["edges"][arm], lam
    )
    values = np.asarray([scores[eid] for eid in case["ids"]], dtype=float)
    return _stats(case["eval_names"], values[case["eval_ix"]], case["target"])


def mask_base(case, layer, fraction, seed):
    base = case["base"].copy()
    if fraction is None:
        return base
    eligible = np.asarray(
        [i for i, (kind, score) in enumerate(zip(case["kinds"], case["base"]))
         if kind == layer and score != 0.0], dtype=int
    )
    if fraction == 1.0:
        selected = eligible
    elif len(eligible):
        nmask = int(round(fraction * len(eligible)))
        rng = np.random.default_rng(
            _metric_seed("lambda-mask", case["case_id"], layer, fraction, seed)
        )
        selected = np.sort(rng.choice(eligible, size=nmask, replace=False))
    else:
        selected = np.asarray([], dtype=int)
    base[selected] = 0.0
    return base


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cases, source_paths, replay = load_cases()
    rows = []
    for case in [c for c in cases if c["dataset"] == "lsdb"]:
        conditions = [("native", None, 0.0)]
        if case["level"] in ("service", "pod"):
            layer = case["level"]
            conditions += [
                (f"{layer}_partial", layer, 0.5),
                (f"{layer}_complete", layer, 1.0),
            ]
        for condition, layer, fraction in conditions:
            for seed in SEEDS if fraction is not None else (0,):
                base = mask_base(case, layer, fraction, seed) if layer else case["base"]
                for arm in ARMS:
                    for lam in LAMBDAS:
                        stats = evaluate(case, base, arm, lam)
                        rows.append(dict(
                            case_id=case["case_id"], root_layer=case["level"],
                            condition=condition, mask_fraction=fraction,
                            seed=seed, arm=arm, lambda_value=lam,
                            **stats,
                        ))
    cases_df = pd.DataFrame(rows)
    cases_df.to_csv(OUT / "lambda_sensitivity_cases.csv", index=False)

    # Average mask seeds within each case, then average cases, matching the
    # masking protocol used in the manuscript.
    case_means = cases_df.groupby(
        ["case_id", "root_layer", "condition", "mask_fraction", "arm", "lambda_value"],
        as_index=False,
    )[["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5", "rank", "tie_size"]].mean()
    summary = case_means.groupby(
        ["root_layer", "condition", "mask_fraction", "arm", "lambda_value"],
        as_index=False,
    )[["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"]].mean()
    summary.to_csv(OUT / "lambda_sensitivity_summary.csv", index=False)

    rank_check = []
    for keys, group in cases_df.groupby(["case_id", "condition", "arm"]):
        complete = group[group.condition.str.endswith("_complete")]
        if complete.empty:
            continue
        changed = 0
        seed_count = 0
        for seed, sg in complete.groupby("seed"):
            reference = sg[sg.lambda_value == 0.1].iloc[0]
            for _, row in sg[sg.lambda_value != 0.1].iterrows():
                changed += int(any(
                    not np.isclose(row[name], reference[name], atol=1e-12)
                    for name in ("tie_top1", "tie_top3", "tie_top5", "tie_avg5")
                ))
            seed_count += 1
        rank_check.append(dict(case_id=keys[0], condition=keys[1], arm=keys[2],
                               seeds=seed_count, changed_rank_seeds=changed))
    rank_check_df = pd.DataFrame(rank_check)
    rank_check_df.to_csv(OUT / "complete_mask_rank_check.csv", index=False)

    protocol = dict(
        datasets=["LSDB"], cases=88, lambdas=list(LAMBDAS), mask_seeds=list(SEEDS),
        conditions="native all cases; 50% and 100% masking only when root layer equals the masked layer",
        propagation="fixed one-step bidirectional row-normalized graph; only lambda varies",
        primary_metrics="tie-expected Top-k and Avg@5; deterministic metrics retained per case",
        source_paths=[str(p) for p in source_paths],
        source_sha256={str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths},
        replay_checks=len(replay),
        complete_mask_rank_changes=int(rank_check_df.changed_rank_seeds.sum()) if not rank_check_df.empty else 0,
    )
    (OUT / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    report = [
        "# Lambda sensitivity under direct-score masking", "",
        "The experiment holds the graph, base scores, candidate sets, mask selections, and one-step propagation operator fixed. Only positive lambda changes.",
        "Native rows use all 88 LSDB cases. Partial and complete rows are evaluated only when the masked layer is the known root layer.",
        "Under complete target-layer masking, rank changes are checked against lambda=0.1; positive lambda is expected to rescale target-layer propagated scores without changing their order.", "",
        "## Summary", "",
        summary.to_string(index=False), "",
        f"Complete-mask rank changes relative to lambda=0.1: {protocol['complete_mask_rank_changes']} seed-level changes.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(summary.to_string(index=False))
    print("Complete-mask rank changes:", protocol["complete_mask_rank_changes"])


if __name__ == "__main__":
    main()
