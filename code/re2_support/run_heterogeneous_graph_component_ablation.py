"""Component ablation for the deduplicated RCAEval Route-B graph.

The script reuses the verified deduplicated case runner and changes only which
non-metric relations have a non-zero propagation weight.  BARO always enters
Service once; Metric->Service remains a context edge in every component.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from tqdm import tqdm

from data_loader import DataLoader


VARIANT = "route_b_deduplicated_component_ablation"
DEFAULT_COMPONENTS = (
    "baro_only,ownership,ownership_service_calls,"
    "ownership_operation_calls,full"
)


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def aggregate(rows: list[dict]) -> list[dict]:
    """Aggregate the saved per-case metrics by fault and over all cases."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row["fault_type"]), []).append(row)
    groups["总体"] = rows
    summaries = []
    for fault, group in sorted(groups.items(), key=lambda item: item[0]):
        summaries.append({
            "fault": fault,
            "top1": sum(float(row["top1"]) for row in group) / len(group),
            "top3": sum(float(row["top3"]) for row in group) / len(group),
            "top5": sum(float(row["top5"]) for row in group) / len(group),
            "avg5": sum(float(row["avg5"]) for row in group) / len(group),
            "n": len(group),
        })
    return summaries


def replay_existing(
    output_root: Path,
    components: list[str],
    direction: str,
    propagation_lambda: float,
) -> None:
    """Rebuild component tables from the frozen per-case JSON outputs."""
    case_rows: list[dict] = []
    summary_rows: list[dict] = []
    for component in components:
        run_dir = output_root / component
        case_files = sorted(run_dir.glob("*.json"))
        if not case_files:
            raise SystemExit(f"No frozen case JSON files found in {run_dir}")
        component_rows = []
        for case_path in case_files:
            result = json.loads(case_path.read_text(encoding="utf-8"))
            row = {
                "variant": VARIANT,
                "component": component,
                "direction": direction,
                "topology_lambda": f"{propagation_lambda:g}",
                "dataset_id": result["dataset_id"],
                "fault_type": result["fault_type"],
                "ground_truth": result["ground_truth"],
                **result["metrics"],
            }
            case_rows.append(row)
            component_rows.append(row)
        for summary in aggregate(component_rows):
            summary_rows.append({
                "variant": VARIANT,
                "component": component,
                "direction": direction,
                "topology_lambda": f"{propagation_lambda:g}",
                **summary,
            })
    write_csv(output_root / "component_ablation_cases.csv", case_rows)
    write_csv(output_root / "component_ablation_summary.csv", summary_rows)
    (output_root / "component_ablation_summary.json").write_text(
        json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Replayed {len(case_rows)} case-component rows from {output_root}")


def relation_weights(component: str, args: argparse.Namespace) -> dict[str, float]:
    if component not in {
        "baro_only",
        "ownership",
        "ownership_service_calls",
        "ownership_operation_calls",
        "full",
    }:
        raise ValueError(f"unknown component: {component}")

    ownership = component in {
        "ownership",
        "ownership_service_calls",
        "ownership_operation_calls",
        "full",
    }
    service_calls = component in {"ownership_service_calls", "full"}
    operation_calls = component in {"ownership_operation_calls", "full"}
    return {
        # BARO is already present once in the Service initial score.
        "metric_observes_service": 0.0,
        "log_source_proxy_for_service": args.log_weight if ownership else 0.0,
        "operation_belongs_to_service": args.operation_weight if ownership else 0.0,
        "operation_calls_operation": (
            args.operation_call_weight if operation_calls else 0.0
        ),
        "operation_called_by_operation": (
            args.operation_call_weight if operation_calls else 0.0
        ),
        "service_calls_service": args.call_weight if service_calls else 0.0,
        "service_called_by_service": args.call_weight if service_calls else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run relation-component ablation for deduplicated Route-B."
    )
    parser.add_argument("--data-path", default="../rcaeval-data")
    parser.add_argument(
        "--output",
        default="output/re2_heterogeneous_dedup_components",
    )
    parser.add_argument("--filter", default="re2ob")
    parser.add_argument("--components", default=DEFAULT_COMPONENTS)
    parser.add_argument("--lambda", dest="propagation_lambda", type=float, default=0.05)
    parser.add_argument(
        "--direction",
        choices=["forward", "backward", "bidirectional"],
        default="forward",
    )
    parser.add_argument("--bin-seconds", type=int, default=15)
    parser.add_argument("--call-weight", type=float, default=1.0)
    parser.add_argument("--log-weight", type=float, default=0.2)
    parser.add_argument("--operation-weight", type=float, default=0.5)
    parser.add_argument("--operation-call-weight", type=float, default=1.0)
    parser.add_argument("--propagation-steps", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help=(
            "Rebuild the case and summary tables from frozen per-case JSON "
            "files without loading raw RCAEval telemetry."
        ),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_root = Path(args.output)
    if not output_root.is_absolute():
        output_root = Path(__file__).resolve().parent / output_root
    output_root.mkdir(parents=True, exist_ok=True)

    components = parse_csv_list(args.components)
    filters = parse_csv_list(args.filter)
    weights_by_component = {
        component: relation_weights(component, args) for component in components
    }
    config = {
        "variant": VARIANT,
        "data_path": args.data_path,
        "filter": filters,
        "components": components,
        "topology_lambda": args.propagation_lambda,
        "direction": args.direction,
        "bin_seconds": args.bin_seconds,
        "propagation_steps": args.propagation_steps,
        "relation_weights_by_component": weights_by_component,
        "fixed_evidence_policy": (
            "BARO initializes Service once; Metric->Service is context-only"
        ),
        "component_meanings": {
            "baro_only": "BARO Service baseline; all propagation weights are zero",
            "ownership": "LogSource->Service and Operation->Service",
            "ownership_service_calls": "ownership plus Service->Service calls",
            "ownership_operation_calls": "ownership plus Operation->Operation calls",
            "full": "ownership plus both Operation and Service call relations",
        },
    }
    config_path = output_root / "run_config.json"
    if config_path.exists() and not args.force:
        try:
            previous = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if previous is not None and previous != config:
            raise SystemExit(
                "Output contains a different configuration; use another output "
                "directory or --force."
            )
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.replay_existing:
        replay_existing(
            output_root,
            components,
            args.direction,
            args.propagation_lambda,
        )
        return

    items = list(DataLoader(args.data_path, filter_patterns=filters))
    if args.limit:
        items = items[: args.limit]

    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        print(f"Datasets found: {len(items)}")
        return

    try:
        from run_heterogeneous_graph_dedup_ablation import run_case as run_dedup_case
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Raw-telemetry regeneration requires the historical Route-B graph "
            "builder, which is not part of the verified paper package. Use "
            "--replay-existing with the frozen case outputs instead."
        ) from exc

    case_rows: list[dict] = []
    summary_rows: list[dict] = []
    for component in components:
        run_dir = output_root / component
        run_dir.mkdir(parents=True, exist_ok=True)
        weights = weights_by_component[component]
        component_rows = []
        reused = 0
        for item in tqdm(items, desc=f"component_{component}"):
            case_path = run_dir / f"{item['dataset_id']}.json"
            result = None
            if case_path.exists() and not args.force:
                try:
                    cached = json.loads(case_path.read_text(encoding="utf-8"))
                    if (
                        cached.get("component_ablation_variant") == VARIANT
                        and cached.get("component") == component
                        and cached.get("dataset_id") == item["dataset_id"]
                        and cached.get("relation_weights") == weights
                        and float(cached.get("topology_lambda"))
                        == args.propagation_lambda
                        and cached.get("direction") == args.direction
                        and int(cached.get("propagation_steps"))
                        == args.propagation_steps
                        and isinstance(cached.get("metrics"), dict)
                    ):
                        result = cached
                        reused += 1
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    result = None

            if result is None:
                result = run_dedup_case(
                    item,
                    args.propagation_lambda,
                    args.direction,
                    args.bin_seconds,
                    weights,
                    args.propagation_steps,
                )
                result["component_ablation_variant"] = VARIANT
                result["component"] = component
                result["relation_weights"] = weights
                for edge in result["graph"]["edges"]:
                    edge["active_in_scoring"] = (
                        weights.get(edge["relation"], 0.0) != 0.0
                    )
                case_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            row = {
                "variant": VARIANT,
                "component": component,
                "direction": args.direction,
                "topology_lambda": f"{args.propagation_lambda:g}",
                "dataset_id": result["dataset_id"],
                "fault_type": result["fault_type"],
                "ground_truth": result["ground_truth"],
                **result["metrics"],
            }
            case_rows.append(row)
            component_rows.append(row)

        if reused:
            print(f"component_{component}: reused {reused}/{len(items)} cases")
        for summary in aggregate(component_rows):
            summary_rows.append({
                "variant": VARIANT,
                "component": component,
                "direction": args.direction,
                "topology_lambda": f"{args.propagation_lambda:g}",
                **summary,
            })

    write_csv(output_root / "component_ablation_cases.csv", case_rows)
    write_csv(output_root / "component_ablation_summary.csv", summary_rows)
    (output_root / "component_ablation_summary.json").write_text(
        json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved: {output_root / 'component_ablation_summary.csv'}")


if __name__ == "__main__":
    main()
