from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .dataset import LatentScopeCase
from .pipeline import root_cause_rank, root_cause_rank_details, run_case_from_metric_ranking
from .scoring import SCORING_SCHEMA_VERSION, score_with_official_baro


CHECKPOINT_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class Experiment:
    name: str
    family: str
    propagation_lambda: float = 0.2
    topology_components: str = "all"
    direction: str = "relation-aware"
    score_normalization: str = "raw"
    entity_aggregation: str = "max"
    edge_normalization: str = "row"
    mapping_policy: str = "most_specific"


def fixed_policy_experiments() -> list[Experiment]:
    rows = [
        Experiment("M0_mapping_only", "baseline", propagation_lambda=0.0,
                   topology_components="mapping-only"),
        Experiment("C_containment", "component", topology_components="containment"),
        Experiment("D_dependency", "component", topology_components="dependency"),
        Experiment("CD_full", "component"),
    ]
    rows.extend(
        Experiment(f"lambda_{str(value).replace('.', 'p')}", "lambda", propagation_lambda=value)
        for value in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0)
    )
    rows.extend(
        Experiment(f"direction_{value.replace('-', '_')}", "direction", direction=value)
        for value in ("forward", "backward", "bidirectional", "relation-aware")
    )
    rows.extend(
        Experiment(f"normalization_{value}", "normalization", score_normalization=value)
        for value in ("raw", "max", "percentile")
    )
    rows.extend(
        Experiment(f"aggregation_{value}", "aggregation", entity_aggregation=value)
        for value in ("max", "top3_mean", "top5_mean", "p95")
    )
    # Names are intentionally family-specific even when settings overlap, so
    # every planned comparison has an explicit reference row.
    return rows


def factorial_experiments() -> list[Experiment]:
    """Return the 732 unique configurations in the complete design.

    Lambda zero has no direction/component effect, so it is evaluated once for
    every mapping representation (12 rows). Positive lambda is crossed with all
    four directions and three topology components (720 rows).
    """
    normalizations = ("raw", "max", "percentile")
    aggregations = ("max", "top3_mean", "top5_mean", "p95")
    directions = ("forward", "backward", "bidirectional", "relation-aware")
    components = ("containment", "dependency", "all")
    positive_lambdas = (0.05, 0.1, 0.2, 0.5, 1.0)
    rows = [
        Experiment(
            name=f"F_M0_{normalization}_{aggregation}",
            family="factorial_mapping_only",
            propagation_lambda=0.0,
            topology_components="mapping-only",
            direction="relation-aware",
            score_normalization=normalization,
            entity_aggregation=aggregation,
        )
        for normalization in normalizations
        for aggregation in aggregations
    ]
    rows.extend(
        Experiment(
            name=(
                f"F_{component}_{normalization}_{aggregation}_"
                f"l{str(value).replace('.', 'p')}_{direction.replace('-', '_')}"
            ),
            family=f"factorial_{component}",
            propagation_lambda=value,
            topology_components=component,
            direction=direction,
            score_normalization=normalization,
            entity_aggregation=aggregation,
        )
        for normalization in normalizations
        for aggregation in aggregations
        for value in positive_lambdas
        for direction in directions
        for component in components
    )
    if len(rows) != 732:
        raise AssertionError(f"factorial design must contain 732 rows, got {len(rows)}")
    return rows


def _selection_key(summary: dict) -> tuple[float, ...]:
    """Deterministic staged selection: accuracy first, then simpler lambda."""
    return (
        summary["macro_top3"],
        summary["macro_mrr"],
        summary["min_level_top5"],
        summary["overall"]["top3"],
        -summary["propagation_lambda"],
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_json_atomic(path: Path, payload: object) -> None:
    """Write a checkpoint atomically so interruption cannot leave valid-looking JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _experiment_fingerprint(experiment: Experiment) -> tuple[str, dict]:
    # Names/families describe a comparison and do not change numeric results.
    # Excluding them lets identical configurations be reused across stages.
    configuration = {
        "propagation_lambda": experiment.propagation_lambda,
        "topology_components": experiment.topology_components,
        "direction": experiment.direction,
        "score_normalization": experiment.score_normalization,
        "entity_aggregation": experiment.entity_aggregation,
        "edge_normalization": experiment.edge_normalization,
        "mapping_policy": experiment.mapping_policy,
    }
    return _stable_hash({
        "schema": CHECKPOINT_SCHEMA_VERSION,
        "configuration": configuration,
    })[:20], configuration


def _case_fingerprint(
    dataset_root: Path,
    case: LatentScopeCase,
    scores: list[tuple[str, float]],
    global_input_hash: str,
) -> str:
    rcc_hash = hashlib.sha256(
        (dataset_root / "data" / case.case_id / "rccs.json").read_bytes()
    ).hexdigest()
    score_hash = _stable_hash(scores)
    return _stable_hash({
        "schema": CHECKPOINT_SCHEMA_VERSION,
        "case_id": case.case_id,
        "trigger_time": case.trigger_time,
        "global_inputs": global_input_hash,
        "rccs": rcc_hash,
        "baro_scores": score_hash,
    })


def _load_checkpoint(
    path: Path,
    experiment_fingerprint: str,
    case_fingerprint: str,
) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        or payload.get("experiment_fingerprint") != experiment_fingerprint
        or payload.get("case_fingerprint") != case_fingerprint
        or not isinstance(payload.get("row"), dict)
    ):
        return None
    return payload["row"]


def _cached_scores(
    case: LatentScopeCase,
    cache_dir: Path,
    baro_root: Path | None,
    dataset_root: Path,
    score_mode: str,
    metric_policy: str,
) -> list[tuple[str, float]]:
    path = cache_dir / f"{case.case_id}.json"
    metric_path = dataset_root / "data" / case.case_id / "metrics.json"
    scorer_path = (
        baro_root / "baro" / "root_cause_analysis.py"
        if baro_root is not None else None
    )
    input_hash = _stable_hash({
        "scoring_schema": SCORING_SCHEMA_VERSION,
        "ls_scoring_sha256": hashlib.sha256(
            Path(__file__).with_name("scoring.py").read_bytes()
        ).hexdigest(),
        "score_mode": score_mode,
        "metric_policy": metric_policy,
        "metrics_sha256": hashlib.sha256(metric_path.read_bytes()).hexdigest(),
        "baro_sha256": (
            hashlib.sha256(scorer_path.read_bytes()).hexdigest()
            if scorer_path is not None and scorer_path.exists() else None
        ),
    })
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if (
            payload.get("trigger_time") == case.trigger_time
            and payload.get("input_hash") == input_hash
        ):
            return [(metric, float(score)) for metric, score in payload["scores"]]
    scores = score_with_official_baro(
        case.baro_input(), case.trigger_time, baro_root,
        score_mode=score_mode, metric_policy=metric_policy,
    )
    _write_json(path, {
        "case_id": case.case_id,
        "trigger_time": case.trigger_time,
        "score_source": score_mode,
        "metric_policy": metric_policy,
        "input_hash": input_hash,
        "scores": scores,
    })
    return scores


def _trigger_category(case: LatentScopeCase) -> str:
    categories = set()
    for trigger in case.triggers:
        kpi = str(trigger.get("kpi", "")).lower()
        if "cpu" in kpi:
            categories.add("cpu")
        elif "memory" in kpi or "mem" in kpi:
            categories.add("memory")
        elif any(token in kpi for token in ("fs_", "disk", "io_", "sector")):
            categories.add("disk_io")
        elif any(token in kpi for token in ("network", "socket", "packet")):
            categories.add("network_socket")
        elif any(token in kpi for token in ("mrt", "latency", "duration")):
            categories.add("latency_mrt")
        else:
            categories.add("other")
    return "+".join(sorted(categories)) if categories else "unknown"


def _rates(rows: list[dict], prefix: str = "") -> dict:
    n = len(rows)
    if not n:
        return {"cases": 0, "top1": 0.0, "top3": 0.0, "top5": 0.0, "mrr": 0.0}
    return {
        "cases": n,
        "top1": sum(float(row[f"{prefix}top1"]) for row in rows) / n,
        "top3": sum(float(row[f"{prefix}top3"]) for row in rows) / n,
        "top5": sum(float(row[f"{prefix}top5"]) for row in rows) / n,
        "mrr": sum(1.0 / float(row[f"{prefix}rank"]) for row in rows) / n,
    }


def _baro_rates(rows: list[dict]) -> dict:
    """Aggregate deterministic Top-K and Avg@5 exactly as BARO does."""
    n = len(rows)
    if not n:
        return {
            "cases": 0, "top1": 0.0, "top3": 0.0,
            "top5": 0.0, "avg5": 0.0,
        }
    return {
        "cases": n,
        "top1": sum(float(row["baro_top1"]) for row in rows) / n,
        "top3": sum(float(row["baro_top3"]) for row in rows) / n,
        "top5": sum(float(row["baro_top5"]) for row in rows) / n,
        "avg5": sum(float(row["baro_avg5"]) for row in rows) / n,
    }


def run_policy(
    dataset_root: Path,
    baro_root: Path | None,
    output: Path,
    selected_cases: list[str] | None = None,
    selection_mode: str = "core",
    resume: bool = True,
    score_mode: str = "baro_dataframe_equivalent",
    metric_policy: str = "exclude_last_seen",
    core_lambda: float = 0.05,
    core_direction: str = "relation-aware",
    core_score_normalization: str = "raw",
    core_entity_aggregation: str = "max",
    core_edge_normalization: str = "row",
    core_mapping_policy: str = "most_specific",
) -> dict:
    if selection_mode not in {"core", "sequential", "fixed"}:
        raise ValueError("selection_mode must be core, sequential, or fixed")
    labels = json.loads((dataset_root / "labels" / "label.json").read_text(encoding="utf-8"))
    case_ids = selected_cases or sorted(labels, key=int)
    score_configuration = {
        "scoring_schema": SCORING_SCHEMA_VERSION,
        "score_mode": score_mode,
        "metric_policy": metric_policy,
    }
    cache_namespace = _stable_hash(score_configuration)[:16]
    cache_dir = output / "_cache" / "baro_scores" / cache_namespace
    checkpoint_dir = output / "_checkpoints"
    global_hasher = hashlib.sha256()
    for source in (
        dataset_root / "labels" / "label.json",
        dataset_root / "labels" / "service_deps.json",
        Path(__file__),
        Path(__file__).with_name("pipeline.py"),
        Path(__file__).with_name("topology.py"),
        Path(__file__).with_name("scoring.py"),
    ):
        global_hasher.update(source.read_bytes())
    global_input_hash = global_hasher.hexdigest()
    cases = []
    score_cache = {}
    case_fingerprints = {}
    for case_id in case_ids:
        case = LatentScopeCase.load(dataset_root, case_id)
        cases.append(case)
        score_cache[case_id] = _cached_scores(
            case, cache_dir, baro_root, dataset_root, score_mode, metric_policy
        )
        case_fingerprints[case_id] = _case_fingerprint(
            dataset_root,
            case,
            score_cache[case_id],
            global_input_hash,
        )
        print(f"BARO cache ready: case {case_id}")

    summaries = []
    all_case_rows = []
    executed_experiments: list[Experiment] = []
    selection_history: list[dict] = []

    def evaluate(experiment: Experiment, progress: str) -> dict:
        experiment_rows = []
        checkpoint_hits = 0
        experiment_hash, checkpoint_configuration = _experiment_fingerprint(experiment)
        for case in cases:
            checkpoint_path = checkpoint_dir / experiment_hash / f"{case.case_id}.json"
            row = (
                _load_checkpoint(
                    checkpoint_path,
                    experiment_hash,
                    case_fingerprints[case.case_id],
                )
                if resume
                else None
            )
            if row is not None:
                checkpoint_hits += 1
                row = dict(row)
                row["experiment"] = experiment.name
                row["family"] = experiment.family
            else:
                result = run_case_from_metric_ranking(
                    case,
                    score_cache[case.case_id],
                    propagation_lambda=experiment.propagation_lambda,
                    topology_components=experiment.topology_components,
                    direction=experiment.direction,
                    score_normalization=experiment.score_normalization,
                    entity_aggregation=experiment.entity_aggregation,
                    edge_normalization=experiment.edge_normalization,
                    mapping_policy=experiment.mapping_policy,
                    score_mode=score_mode,
                    metric_policy=metric_policy,
                )
                level_detail = root_cause_rank_details(result, "level")
                global_detail = root_cause_rank_details(result, "global")
                if level_detail is None or global_detail is None:
                    raise RuntimeError(f"case {case.case_id}: ground truth is absent from ranking")
                baro_rank = root_cause_rank(result)
                if baro_rank is None:
                    raise RuntimeError(f"case {case.case_id}: ground truth is absent from BARO ranking")
                baro_hits = {
                    k: float(baro_rank <= k) for k in range(1, 6)
                }
                truth_level = result["ground_truth"][0]["type"]
                row = {
                    "experiment": experiment.name,
                    "family": experiment.family,
                    "case_id": case.case_id,
                    "truth_level": truth_level,
                    "truth_key": result["ground_truth"][0]["key"],
                    "trigger_category": _trigger_category(case),
                    "rank": level_detail["rank"],
                    "rank_min": level_detail["rank_min"],
                    "rank_max": level_detail["rank_max"],
                    "tie_size": level_detail["tie_size"],
                    "top1": level_detail["top1"],
                    "top3": level_detail["top3"],
                    "top5": level_detail["top5"],
                    "baro_rank": baro_rank,
                    "baro_top1": baro_hits[1],
                    "baro_top2": baro_hits[2],
                    "baro_top3": baro_hits[3],
                    "baro_top4": baro_hits[4],
                    "baro_top5": baro_hits[5],
                    "baro_avg5": sum(baro_hits.values()) / 5.0,
                    "global_rank": global_detail["rank"],
                    "global_rank_min": global_detail["rank_min"],
                    "global_rank_max": global_detail["rank_max"],
                    "global_tie_size": global_detail["tie_size"],
                    "global_top1": global_detail["top1"],
                    "global_top3": global_detail["top3"],
                    "global_top5": global_detail["top5"],
                    "level_candidates": len(result["hierarchical_rankings"][truth_level]),
                    "global_candidates": len(result["hierarchical_rankings"]["all_entities"]),
                    "shared_mapping_metrics": result["mapping_diagnostics"]["metrics_linked_to_multiple_entities"],
                    "propagation_edges_sharing_evidence": result["mapping_diagnostics"]["propagation_edges_sharing_mapped_evidence"],
                    "impact_path_exists": int(any(
                        p["candidate"]["key"] == result["ground_truth"][0]["key"]
                        for p in result["impact_paths"]
                    )),
                }
                _write_json_atomic(checkpoint_path, {
                    "schema_version": CHECKPOINT_SCHEMA_VERSION,
                    "experiment_fingerprint": experiment_hash,
                    "case_fingerprint": case_fingerprints[case.case_id],
                    "configuration": checkpoint_configuration,
                    "row": row,
                })
            experiment_rows.append(row)
            all_case_rows.append(row)

        by_level = defaultdict(list)
        by_trigger = defaultdict(list)
        for row in experiment_rows:
            by_level[row["truth_level"]].append(row)
            by_trigger[row["trigger_category"]].append(row)
        overall = _rates(experiment_rows)
        baro_overall = _baro_rates(experiment_rows)
        global_overall = _rates(experiment_rows, "global_")
        levels = {level: _rates(by_level[level]) for level in ("host", "pod", "service")}
        macro_top3 = sum(levels[level]["top3"] for level in levels) / len(levels)
        macro_mrr = sum(levels[level]["mrr"] for level in levels) / len(levels)
        summary = {
            **asdict(experiment),
            "overall": overall,
            "baro_overall": baro_overall,
            "global_overall": global_overall,
            "by_level": levels,
            "by_trigger_category": {
                category: _rates(rows) for category, rows in sorted(by_trigger.items())
            },
            "macro_top3": macro_top3,
            "macro_mrr": macro_mrr,
            "min_level_top5": min(levels[level]["top5"] for level in levels),
            "top5_true_root_path_coverage": sum(r["impact_path_exists"] for r in experiment_rows) / len(experiment_rows),
            "checkpoint_hits": checkpoint_hits,
        }
        summaries.append(summary)
        executed_experiments.append(experiment)
        print(
            f"[{progress}] {experiment.name}: "
            f"BARO Top1={baro_overall['top1']:.3f} "
            f"Top3={baro_overall['top3']:.3f} "
            f"Top5={baro_overall['top5']:.3f} "
            f"Avg@5={baro_overall['avg5']:.3f} "
            f"reused={checkpoint_hits}/{len(cases)}"
        )
        return summary

    if selection_mode == "core":
        core_base = Experiment(
            "V3_M0_mapping_only", "core_component",
            propagation_lambda=0.0,
            topology_components="mapping-only",
            direction=core_direction,
            score_normalization=core_score_normalization,
            entity_aggregation=core_entity_aggregation,
            edge_normalization=core_edge_normalization,
            mapping_policy=core_mapping_policy,
        )
        experiments = [
            core_base,
            replace(core_base, name="V3_C_containment", propagation_lambda=core_lambda, topology_components="containment"),
            replace(core_base, name="V3_D_dependency", propagation_lambda=core_lambda, topology_components="dependency"),
            replace(core_base, name="V3_CD_full", propagation_lambda=core_lambda, topology_components="all"),
        ]
        for index, experiment in enumerate(experiments, start=1):
            evaluate(experiment, f"{index}/{len(experiments)} core")
        baseline_rows = {
            row["case_id"]: row
            for row in all_case_rows if row["experiment"] == "V3_M0_mapping_only"
        }
        for summary in summaries:
            if summary["name"] == "V3_M0_mapping_only":
                continue
            comparison_rows = [
                row for row in all_case_rows if row["experiment"] == summary["name"]
            ]
            summary["paired_vs_mapping"] = {
                "level_rank": {
                    "improved": sum(row["rank"] < baseline_rows[row["case_id"]]["rank"] for row in comparison_rows),
                    "same": sum(row["rank"] == baseline_rows[row["case_id"]]["rank"] for row in comparison_rows),
                    "worsened": sum(row["rank"] > baseline_rows[row["case_id"]]["rank"] for row in comparison_rows),
                },
                "global_rank": {
                    "improved": sum(row["global_rank"] < baseline_rows[row["case_id"]]["global_rank"] for row in comparison_rows),
                    "same": sum(row["global_rank"] == baseline_rows[row["case_id"]]["global_rank"] for row in comparison_rows),
                    "worsened": sum(row["global_rank"] > baseline_rows[row["case_id"]]["global_rank"] for row in comparison_rows),
                },
            }
        best_configuration = None
    elif selection_mode == "fixed":
        experiments = fixed_policy_experiments()
        for index, experiment in enumerate(experiments, start=1):
            evaluate(experiment, f"{index}/{len(experiments)} fixed")
        best_configuration = max(summaries, key=_selection_key)
    else:
        # 12 mapping representations + 6 initial lambdas + 4 directions +
        # 5 positive-lambda refinements + 4 final component ablations.
        total = 31
        completed = 0

        def run_stage(
            stage: str,
            candidates: list[Experiment],
            require_positive_lambda: bool = False,
        ) -> Experiment:
            nonlocal completed
            stage_summaries = []
            for candidate in candidates:
                completed += 1
                stage_summaries.append(evaluate(candidate, f"{completed}/{total} {stage}"))
            overall_best = max(stage_summaries, key=_selection_key)
            overall_best["best_in_stage"] = True
            eligible = (
                [row for row in stage_summaries if row["propagation_lambda"] > 0]
                if require_positive_lambda
                else stage_summaries
            )
            best = max(eligible, key=_selection_key)
            best["selected_for_next_stage"] = True
            selection_history.append({
                "stage": stage,
                "restriction": (
                    "positive lambda required for subsequent topology stages"
                    if require_positive_lambda
                    else "none"
                ),
                "unconstrained_best": {
                    key: overall_best[key]
                    for key in (
                        "name", "propagation_lambda", "topology_components",
                        "direction", "score_normalization", "entity_aggregation",
                        "edge_normalization", "macro_top3", "macro_mrr",
                        "min_level_top5",
                    )
                },
                "selected": {
                    key: best[key]
                    for key in (
                        "name", "propagation_lambda", "topology_components",
                        "direction", "score_normalization", "entity_aggregation",
                        "edge_normalization", "macro_top3", "macro_mrr",
                        "min_level_top5",
                    )
                },
            })
            print(f"selected after {stage}: {best['name']}")
            return Experiment(**{
                key: best[key]
                for key in (
                    "name", "family", "propagation_lambda", "topology_components",
                    "direction", "score_normalization", "entity_aggregation",
                    "edge_normalization",
                )
            })

        # Stage 1 jointly selects the mapping representation at lambda=0.
        # Joint selection avoids locking a normalization before testing how it
        # interacts with entity aggregation.
        best = run_stage("mapping_representation", [
            Experiment(
                f"S1_mapping_{normalization}_{aggregation}",
                "mapping_representation",
                propagation_lambda=0.0,
                score_normalization=normalization,
                entity_aggregation=aggregation,
            )
            for normalization in ("raw", "max", "percentile")
            for aggregation in ("max", "top3_mean", "top5_mean", "p95")
        ])
        # Stage 2 scans lambda using the selected mapping representation. Lambda
        # zero remains an honest global competitor, but a positive winner is
        # selected for the direction stage so topology is not disabled.
        best = run_stage("lambda_initial", [
            replace(
                best,
                name=f"S2_lambda_{str(value).replace('.', 'p')}",
                family="lambda",
                propagation_lambda=value,
                direction="relation-aware",
                topology_components="all",
            )
            for value in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0)
        ], require_positive_lambda=True)
        # Stage 3 inherits the selected mapping representation and positive lambda.
        best = run_stage("direction", [
            Experiment(
                name=f"S3_direction_{value.replace('-', '_')}",
                family="direction",
                propagation_lambda=best.propagation_lambda,
                topology_components="all",
                direction=value,
                score_normalization=best.score_normalization,
                entity_aggregation=best.entity_aggregation,
                edge_normalization=best.edge_normalization,
            )
            for value in ("relation-aware", "forward", "backward", "bidirectional")
        ])
        # Stage 4 re-scans positive lambda after direction selection, handling
        # the lambda-direction interaction without letting lambda zero collapse it.
        best = run_stage("lambda_refinement", [
            replace(
                best,
                name=f"S4_lambda_{str(value).replace('.', 'p')}",
                family="lambda_refinement",
                propagation_lambda=value,
            )
            for value in (0.05, 0.1, 0.2, 0.5, 1.0)
        ])
        best_configuration = asdict(best)

        # Stage 5 is an ablation, not another tuning step: every component uses
        # the final hyperparameters selected above.
        component_candidates = [
            replace(
                best,
                name="S5_M0_mapping_only",
                family="component",
                propagation_lambda=0.0,
                topology_components="mapping-only",
            ),
            replace(best, name="S5_C_containment", family="component", topology_components="containment"),
            replace(best, name="S5_D_dependency", family="component", topology_components="dependency"),
            replace(best, name="S5_CD_full", family="component", topology_components="all"),
        ]
        for candidate in component_candidates:
            completed += 1
            evaluate(candidate, f"{completed}/{total} component")

    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "ablation_summary.json", {
        "dataset": "LatentScope Dataset B",
        "cases": len(cases),
        "selection_mode": selection_mode,
        "resume_enabled": resume,
        "score_configuration": score_configuration,
        "evaluation_protocol": {
            "level_rank": "ground truth ranked among entities of its known label type",
            "global_rank": "ground truth ranked jointly among Host, Pod, and Service entities",
            "ties": "average rank and fractional Top-K credit",
            "baro_compatible": (
                "deterministic ranked-list position; Top-K is binary per case; "
                "Avg@5=(Top1+Top2+Top3+Top4+Top5)/5"
            ),
        },
        "selection_policy": {
            "primary": "macro_top3 across host/pod/service",
            "secondary": "macro_mrr",
            "constraint": "inspect that no single-level Top-5 drops by more than 0.05",
            "note": "Trigger KPI category is observational and is not a fault label.",
        },
        "selection_history": selection_history,
        "best_configuration_before_component_ablation": best_configuration,
        "experiments": summaries,
    })

    summary_fields = [
        "name", "family", "propagation_lambda", "topology_components", "direction",
        "score_normalization", "entity_aggregation", "edge_normalization",
        "mapping_policy",
        "baro_top1", "baro_top3", "baro_top5", "baro_avg5",
        "overall_top1", "overall_top3", "overall_top5", "overall_mrr",
        "global_top1", "global_top3", "global_top5", "global_mrr",
        "host_top1", "host_top3", "host_top5", "host_mrr",
        "pod_top1", "pod_top3", "pod_top5", "pod_mrr",
        "service_top1", "service_top3", "service_top5", "service_mrr",
        "macro_top3", "macro_mrr", "min_level_top5", "top5_true_root_path_coverage",
        "selected_for_next_stage",
        "best_in_stage",
        "checkpoint_hits",
    ]
    with (output / "ablation_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields)
        writer.writeheader()
        for summary in summaries:
            flat = {key: summary[key] for key in asdict(executed_experiments[0])}
            for metric in ("top1", "top3", "top5", "avg5"):
                flat[f"baro_{metric}"] = summary["baro_overall"][metric]
            for metric in ("top1", "top3", "top5", "mrr"):
                flat[f"overall_{metric}"] = summary["overall"][metric]
                flat[f"global_{metric}"] = summary["global_overall"][metric]
                for level in ("host", "pod", "service"):
                    flat[f"{level}_{metric}"] = summary["by_level"][level][metric]
            for key in ("macro_top3", "macro_mrr", "min_level_top5", "top5_true_root_path_coverage"):
                flat[key] = summary[key]
            flat["selected_for_next_stage"] = summary.get("selected_for_next_stage", False)
            flat["best_in_stage"] = summary.get("best_in_stage", False)
            flat["checkpoint_hits"] = summary["checkpoint_hits"]
            writer.writerow(flat)

    case_fields = list(all_case_rows[0]) if all_case_rows else []
    with (output / "ablation_cases.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=case_fields)
        writer.writeheader()
        writer.writerows(all_case_rows)
    _write_json(output / "policy.json", {
        "selection_mode": selection_mode,
        "resume_enabled": resume,
        "score_configuration": score_configuration,
        "selection_history": selection_history,
        "executed_experiments": [asdict(row) for row in executed_experiments],
    })
    return {
        "cases": len(cases),
        "experiments": len(executed_experiments),
        "selection_mode": selection_mode,
        "output": str(output),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LatentScope ablation policy")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--baro-root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output") / "ablation")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--selection-mode",
        choices=["core", "sequential", "fixed"],
        default="core",
        help="core runs the four calibrated propagation controls",
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
    parser.add_argument("--core-lambda", type=float, default=0.05)
    parser.add_argument(
        "--core-direction",
        choices=["forward", "backward", "bidirectional", "relation-aware"],
        default="relation-aware",
    )
    parser.add_argument(
        "--core-score-normalization",
        choices=["raw", "max", "percentile"], default="raw",
    )
    parser.add_argument(
        "--core-entity-aggregation",
        choices=["max", "top3_mean", "top5_mean", "p95"], default="max",
    )
    parser.add_argument(
        "--core-edge-normalization", choices=["none", "row"], default="row"
    )
    parser.add_argument(
        "--core-mapping-policy",
        choices=["all_links", "most_specific"],
        default="most_specific",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        default=True,
        help="ignore reusable experiment-case checkpoints and recompute",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_policy(
        args.dataset_root,
        args.baro_root,
        args.output,
        selected_cases=args.case or None,
        selection_mode=args.selection_mode,
        resume=args.resume,
        score_mode=args.score_mode,
        metric_policy=args.metric_policy,
        core_lambda=args.core_lambda,
        core_direction=args.core_direction,
        core_score_normalization=args.core_score_normalization,
        core_entity_aggregation=args.core_entity_aggregation,
        core_edge_normalization=args.core_edge_normalization,
        core_mapping_policy=args.core_mapping_policy,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
