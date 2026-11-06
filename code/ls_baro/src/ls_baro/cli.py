from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import root_cause_rank_details, run_case


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _case_ids(dataset_root: Path, selected: list[str], run_all: bool) -> list[str]:
    if run_all:
        labels = json.loads((dataset_root / "labels" / "label.json").read_text(encoding="utf-8"))
        return sorted(labels, key=int)
    if not selected:
        raise SystemExit("Specify --case CASE_ID or --all")
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LatentScope Dataset B topology-enhanced BARO")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--baro-root", type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--lambda", dest="propagation_lambda", type=float, default=0.2)
    parser.add_argument(
        "--topology-components",
        choices=["mapping-only", "containment", "dependency", "all"],
        default="all",
    )
    parser.add_argument(
        "--direction",
        choices=["forward", "backward", "bidirectional", "relation-aware"],
        default="relation-aware",
    )
    parser.add_argument(
        "--score-normalization",
        choices=["raw", "max", "percentile"],
        default="raw",
    )
    parser.add_argument(
        "--entity-aggregation",
        choices=["max", "top3_mean", "top5_mean", "p95"],
        default="max",
    )
    parser.add_argument(
        "--edge-normalization", choices=["none", "row"], default="none"
    )
    parser.add_argument(
        "--score-mode",
        choices=["baro_dataframe_equivalent", "legacy_dict"],
        default="baro_dataframe_equivalent",
    )
    parser.add_argument(
        "--metric-policy", choices=["all", "exclude_last_seen"],
        default="exclude_last_seen",
    )
    parser.add_argument(
        "--mapping-policy", choices=["all_links", "most_specific"],
        default="most_specific",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("output"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = []
    for case_id in _case_ids(args.dataset_root, args.case, args.all):
        result = run_case(
            args.dataset_root,
            case_id,
            baro_root=args.baro_root,
            propagation_lambda=args.propagation_lambda,
            topology_components=args.topology_components,
            direction=args.direction,
            score_normalization=args.score_normalization,
            entity_aggregation=args.entity_aggregation,
            edge_normalization=args.edge_normalization,
            score_mode=args.score_mode,
            metric_policy=args.metric_policy,
            mapping_policy=args.mapping_policy,
        )
        detail = root_cause_rank_details(result, "level")
        global_detail = root_cause_rank_details(result, "global")
        if detail is None or global_detail is None:
            raise RuntimeError(f"case {case_id}: ground truth is absent from ranking")
        _write_json(args.output / "cases" / f"{case_id}.json", result)
        rows.append({
            "case_id": case_id,
            "ground_truth": result["ground_truth"],
            "rank": detail["rank"],
            "top1": detail["top1"],
            "top3": detail["top3"],
            "top5": detail["top5"],
            "global_rank": global_detail["rank"],
            "global_top1": global_detail["top1"],
            "global_top3": global_detail["top3"],
            "global_top5": global_detail["top5"],
            "top_k": {
                level: values[: args.top_k]
                for level, values in result["hierarchical_rankings"].items()
            },
        })
        print(
            f"case {case_id}: level rank={detail['rank']:.3g}, "
            f"global rank={global_detail['rank']:.3g}"
        )
    n = len(rows)
    summary = {
        "cases": n,
        "lambda": args.propagation_lambda,
        "topology_components": args.topology_components,
        "direction": args.direction,
        "score_normalization": args.score_normalization,
        "entity_aggregation": args.entity_aggregation,
        "edge_normalization": args.edge_normalization,
        "score_mode": args.score_mode,
        "metric_policy": args.metric_policy,
        "mapping_policy": args.mapping_policy,
        "top1": sum(float(row["top1"]) for row in rows) / n if n else 0.0,
        "top3": sum(float(row["top3"]) for row in rows) / n if n else 0.0,
        "top5": sum(float(row["top5"]) for row in rows) / n if n else 0.0,
        "global_top1": sum(float(row["global_top1"]) for row in rows) / n if n else 0.0,
        "global_top3": sum(float(row["global_top3"]) for row in rows) / n if n else 0.0,
        "global_top5": sum(float(row["global_top5"]) for row in rows) / n if n else 0.0,
        "results": rows,
    }
    _write_json(args.output / "summary.json", summary)
    print(json.dumps({
        key: summary[key]
        for key in (
            "cases", "top1", "top3", "top5",
            "global_top1", "global_top3", "global_top5",
        )
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
