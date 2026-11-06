"""Frozen-policy transfer ablation on AIOps 2022 HipsterShop fault data."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

from ls_baro.aiops2022_adapter import AIOps2022Adapter
from ls_baro.contracts import RCAConfig
from ls_baro.engine import RCAEngine
from ls_baro.scoring import score_with_official_baro


POLICY = {
    "score_mode": "baro_dataframe_equivalent",
    "metric_policy": "exclude_last_seen",
    "mapping_policy": "most_specific",
    "score_normalization": "raw",
    "entity_aggregation": "max",
    "edge_normalization": "row",
    "direction": "bidirectional",
}
ARMS = {
    "Mapping-only": ("mapping",),
    "Containment-only": ("mapping", "containment"),
    "Dependency-only": ("mapping", "dependency"),
    "Full": ("mapping", "containment", "dependency"),
}
ARM_LAMBDAS = {
    "Mapping-only": 0.0,
    "Containment-only": 0.1,
    "Dependency-only": 0.1,
    "Full": 0.1,
}
MEASURES = ("top1", "top3", "top5", "mrr")


def raw_metric_key(metric_id: str) -> str:
    prefix = "aiops2022:metric:"
    return unquote(metric_id[len(prefix):]) if metric_id.startswith(prefix) else metric_id


def tie_aware_metrics(rows: list[dict], key: str, tolerance: float = 1e-12) -> dict:
    match = next((row for row in rows if row["key"] == key), None)
    if match is None:
        raise ValueError(f"root cause {key!r} is absent from ranking")
    score = float(match["score"])
    positions = [
        index for index, row in enumerate(rows, start=1)
        if abs(float(row["score"]) - score) <= tolerance
    ]
    rank = sum(positions) / len(positions)
    return {
        "rank": rank,
        "mrr": 1.0 / rank,
        "top1": sum(position <= 1 for position in positions) / len(positions),
        "top3": sum(position <= 3 for position in positions) / len(positions),
        "top5": sum(position <= 5 for position in positions) / len(positions),
        "tie_size": len(positions),
    }


def evaluate(result, root_cause: dict) -> dict:
    level = root_cause["type"]
    return {
        "level": tie_aware_metrics(result.rankings[level], root_cause["key"]),
        "global": tie_aware_metrics(result.rankings["all_entities"], root_cause["key"]),
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate_arm(rows: list[dict], arm: str) -> dict:
    result = {"cases": len(rows)}
    for scope in ("level", "global"):
        result[scope] = {
            measure: mean([float(row["arms"][arm][scope][measure]) for row in rows])
            for measure in ("rank", *MEASURES)
        }
    return result


def contrast(rows: list[dict], treatment: str, baseline: str) -> dict:
    per_subset: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        per_subset[row["subset"]].append(row)
    result = {"treatment": treatment, "baseline": baseline, "scopes": {}}
    for scope in ("level", "global"):
        deltas = {
            measure: [
                float(row["arms"][treatment][scope][measure])
                - float(row["arms"][baseline][scope][measure])
                for row in rows
            ]
            for measure in MEASURES
        }
        rank_changes = [
            float(row["arms"][baseline][scope]["rank"])
            - float(row["arms"][treatment][scope]["rank"])
            for row in rows
        ]
        subset_rows = {}
        subset_mrr_deltas = []
        for subset, subset_cases in sorted(per_subset.items()):
            delta = mean([
                float(row["arms"][treatment][scope]["mrr"])
                - float(row["arms"][baseline][scope]["mrr"])
                for row in subset_cases
            ])
            subset_mrr_deltas.append(delta)
            subset_rows[subset] = {
                "cases": len(subset_cases),
                "delta_mrr": delta,
                "delta_top1": mean([
                    float(row["arms"][treatment][scope]["top1"])
                    - float(row["arms"][baseline][scope]["top1"])
                    for row in subset_cases
                ]),
            }
        positive = sum(value > 1e-12 for value in subset_mrr_deltas)
        zero = sum(abs(value) <= 1e-12 for value in subset_mrr_deltas)
        negative = sum(value < -1e-12 for value in subset_mrr_deltas)
        improved = sum(value > 1e-12 for value in rank_changes)
        tied = sum(abs(value) <= 1e-12 for value in rank_changes)
        regressed = sum(value < -1e-12 for value in rank_changes)
        delta_mrr = mean(deltas["mrr"])
        if delta_mrr > 0 and positive >= (len(subset_mrr_deltas) + 1) // 2 and improved >= regressed:
            verdict = "stable_gain"
        elif delta_mrr > 0:
            verdict = "mixed_positive"
        elif abs(delta_mrr) <= 1e-12:
            verdict = "no_change"
        else:
            verdict = "no_gain"
        result["scopes"][scope] = {
            "delta": {measure: mean(values) for measure, values in deltas.items()},
            "mean_rank_improvement": mean(rank_changes),
            "case_rank_outcomes": {
                "improved": improved, "tied": tied, "regressed": regressed,
            },
            "subset_mrr_signs": {
                "positive": positive, "zero": zero, "negative": negative,
            },
            "verdict": verdict,
            "per_subset": subset_rows,
        }
    return result


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> dict:
    direction = getattr(args, "direction", POLICY["direction"])
    isolation_guard_seconds = getattr(args, "isolation_guard_seconds", 0)
    protocol_name = getattr(args, "protocol_name", "frozen-lsdb-transfer")
    policy = {**POLICY, "direction": direction}
    adapter = AIOps2022Adapter(
        args.dataset_root,
    )
    specs = [
        spec for spec in adapter.cases()
        if args.subset is None or spec.subset == args.subset
    ]
    scoped_case_count = len(specs)
    if isolation_guard_seconds:
        specs = [spec for spec in specs if spec.isolated(isolation_guard_seconds)]
    if not specs:
        raise ValueError(f"subset not found or has no labels: {args.subset}")
    if not specs:
        raise ValueError("no cases selected")

    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    signature_payload = {
        "policy": policy,
        "protocol_name": protocol_name,
        "isolation_guard_seconds": isolation_guard_seconds,
        "dataset_root": str(args.dataset_root.resolve()),
        "dataset_fingerprint": adapter.source_fingerprint(),
        "selected_case_ids": [spec.case_id for spec in specs],
        "script_sha256": script_hash,
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    output = args.output.resolve()
    checkpoint_root = output / "checkpoints"
    rows = []
    for index, spec in enumerate(specs, start=1):
        checkpoint = checkpoint_root / f"{hashlib.sha1(spec.case_id.encode()).hexdigest()}.json"
        if checkpoint.is_file() and not args.no_resume:
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("run_signature") == signature:
                rows.append(saved["case"])
                print(f"[{index}/{len(specs)}] resume {spec.case_id}", flush=True)
                continue

        print(f"[{index}/{len(specs)}] loading {spec.case_id}", flush=True)
        adapted = adapter.load(spec.case_id)
        scoring_metrics = {
            raw_metric_key(series.metric_id): list(zip(series.timestamps, series.values))
            for series in adapted.observations
        }
        metric_ranking = score_with_official_baro(
            scoring_metrics,
            adapted.window.detection_start,
            None,
            score_mode=policy["score_mode"],
            metric_policy=policy["metric_policy"],
        )
        root_cause = dict(adapted.evaluation.root_causes[0])
        arm_rows = {}
        for arm, groups in ARMS.items():
            config = RCAConfig(
                relation_groups=groups,
                propagation_lambda=ARM_LAMBDAS[arm],
                direction=policy["direction"],
                score_normalization=policy["score_normalization"],
                entity_aggregation=policy["entity_aggregation"],
                edge_normalization=policy["edge_normalization"],
                mapping_policy=policy["mapping_policy"],
                score_mode=policy["score_mode"],
                metric_policy=policy["metric_policy"],
            )
            engine = RCAEngine(
                baro_root=None,
                scorer=lambda _metrics, _time, _root, ranking=metric_ranking: ranking,
            )
            result = engine.analyze(
                adapted.graph, adapted.observations, adapted.window, config
            )
            arm_rows[arm] = evaluate(result, root_cause)
        case_row = {
            "case_id": spec.case_id,
            "subset": spec.subset,
            "timestamp": spec.timestamp,
            "root_cause": root_cause,
            "failure_type": spec.failure_type,
            "seconds_from_previous": spec.seconds_from_previous,
            "seconds_to_next": spec.seconds_to_next,
            "scored_metrics": len(metric_ranking),
            "arms": arm_rows,
        }
        atomic_json(checkpoint, {"run_signature": signature, "case": case_row})
        rows.append(case_row)
        print(f"[{index}/{len(specs)}] complete {spec.case_id}", flush=True)

    summary = {
        "dataset": "AIOps 2022 HipsterShop training_data_with_faults",
        "protocol": protocol_name,
        "selected_cases": len(rows),
        "cases_before_isolation_filter": scoped_case_count,
        "available_cases": len(adapter.cases()),
        "subsets": sorted({row["subset"] for row in rows}),
        "selection": {
            "fixed_window_seconds": {"baseline": 600, "detection": 600},
            "isolation_guard_seconds": isolation_guard_seconds,
            "excluded_for_neighboring_fault": scoped_case_count - len(specs),
            "note": "labels define windows/evaluation only and never enter scoring or propagation",
        },
        "policy": policy,
        "arm_lambdas": ARM_LAMBDAS,
        "arms": {arm: aggregate_arm(rows, arm) for arm in ARMS},
        "containment_transfer": {
            "primary": contrast(rows, "Containment-only", "Mapping-only"),
            "incremental_with_dependency": contrast(rows, "Full", "Dependency-only"),
            "predeclared_interpretation": {
                "stable_gain": (
                    "positive overall MRR delta, positive in at least half of subsets, "
                    "and no more case regressions than improvements"
                ),
            },
        },
        "run_signature": signature,
    }
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "summary.json", summary)
    with (output / "cases.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "case_id", "subset", "timestamp", "level", "root_cause", "failure_type",
            "arm", "scope", "rank", "mrr", "top1", "top3", "top5", "tie_size",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            for arm, scopes in row["arms"].items():
                for scope, metrics in scopes.items():
                    writer.writerow({
                        "case_id": row["case_id"], "subset": row["subset"],
                        "timestamp": row["timestamp"], "level": row["root_cause"]["type"],
                        "root_cause": row["root_cause"]["key"],
                        "failure_type": row["failure_type"], "arm": arm, "scope": scope,
                        **metrics,
                    })
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen LSDB four-arm transfer test on AIOps 2022."
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/aiops2022-containment-transfer"))
    parser.add_argument(
        "--subset",
        choices=[
            "2022-03-20-cloudbed1", "2022-03-20-cloudbed2", "2022-03-20-cloudbed3",
            "2022-03-21-cloudbed1", "2022-03-21-cloudbed2", "2022-03-21-cloudbed3",
            "2022-03-24-cloudbed3",
        ],
        help="run only one cloudbed subset; omitted means all seven subsets",
    )
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
