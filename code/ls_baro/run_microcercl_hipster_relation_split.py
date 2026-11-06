"""Eight-arm deployment/ownership split on the MicroCERCL Hipster subset."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ls_baro.contracts import RCAConfig
from ls_baro.engine import RCAEngine
from ls_baro.microcercl_adapter import MicroCERCLAdapter, case_paths


ARMS = {
    "Mapping-only": (),
    "Deployment": ("deployment",),
    "Ownership": ("ownership",),
    "Dependency": ("dependency",),
    "Containment": ("deployment", "ownership"),
    "Deployment+Dependency": ("deployment", "dependency"),
    "Ownership+Dependency": ("ownership", "dependency"),
    "Full": ("deployment", "ownership", "dependency"),
}
OLD_ARMS = {
    "mapping-only": "Mapping-only",
    "containment-only": "Containment",
    "dependency-only": "Dependency",
    "Full": "Full",
}


def load_historical(path: Path, case_ids: set[str]) -> dict:
    """Stream the old large results file and retain compact rank/score signatures."""
    old = {}
    needle = b'"application": "hipster_chaos"'
    with path.open("rb") as stream:
        for line in stream:
            if needle not in line:
                continue
            item = json.loads(line)
            if item["case_id"] not in case_ids or item["arm"] not in OLD_ARMS:
                continue
            result = item["result"]
            old[(item["case_id"], OLD_ARMS[item["arm"]])] = {
                "service_rank": item["service_rank"],
                "global_rank": item["global_rank"],
                "base_scores": result["base_scores"],
                "final_scores": result["final_scores"],
                "rankings": {k: _rank_sig(v) for k, v in result["rankings"].items()},
                "mappings": _mapping_sig(result["entity_mappings"]),
                "contributions": _contribution_sig(result["contributions"]),
                "propagation_edges": _edge_sig(result["propagation_edges"]),
            }
    expected = len(case_ids) * len(OLD_ARMS)
    if len(old) != expected:
        raise AssertionError(f"historical arm coverage {len(old)} != {expected}")
    return old


def _rank_sig(rows):
    return [(r.get("id"), r.get("kind"), r.get("key"), r.get("score"), r.get("rank")) for r in rows]


def _mapping_sig(rows):
    return sorted((r.get("source"), r.get("target"), r.get("weight"),
                   r.get("relation"), r.get("provenance")) for r in rows)


def _contribution_sig(rows):
    return sorted((r.get("from"), r.get("to"), r.get("relation"), r.get("weight"),
                   r.get("source_baro_mapped_score"), r.get("delta"), r.get("relation_kind"),
                   r.get("relation_id"), r.get("source_ref")) for r in rows)


def _edge_sig(rows):
    return sorted((r.get("source"), r.get("target"), r.get("relation"), r.get("weight")) for r in rows)


def _close_dict(left, right, tol=1e-12):
    if set(left) != set(right):
        return False, float("inf")
    error = max((abs(float(left[k]) - float(right[k])) for k in left), default=0.0)
    return error <= tol, error


def compare_old(row, result, old):
    checks = {}
    for field, current in (("base_scores", result.base_scores), ("final_scores", result.final_scores)):
        checks[field], err = _close_dict(current, old[field])
        checks[field + "_max_abs_error"] = err
    checks["rankings"] = {k: _rank_sig(v) for k, v in result.rankings.items()} == old["rankings"]
    checks["mappings"] = _mapping_sig(result.entity_mappings) == old["mappings"]
    checks["contributions"] = _contribution_sig(result.contributions) == old["contributions"]
    checks["propagation_edges"] = _edge_sig(result.propagation_edges) == old["propagation_edges"]
    checks["service_rank"] = row["service_rank"] == old["service_rank"]
    checks["global_rank"] = row["global_rank"] == old["global_rank"]
    checks["passed"] = all(v for k, v in checks.items() if not k.endswith("_max_abs_error"))
    return checks


def write_csv(path: Path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows):
    out = []
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm]
        n = len(rs)
        if not n:
            continue
        item = {"application": "hipster_chaos", "arm": arm, "n": n}
        for k in (1, 3, 5):
            item[f"service_top{k}"] = sum(r["service_rank"] <= k for r in rs) / n
            item[f"global_top{k}"] = sum(r["global_rank"] <= k for r in rs) / n
        item["service_mrr"] = sum(1 / r["service_rank"] for r in rs) / n
        item["global_mrr"] = sum(1 / r["global_rank"] for r in rs) / n
        item["service_avg5"] = sum(sum(r["service_rank"] <= k for k in range(1, 6)) / 5 for r in rs) / n
        out.append(item)
    return out


def paired(rows):
    index = {(r["arm"], r["case_id"]): r for r in rows}
    comparisons = [("Mapping-only", name) for name in ARMS if name != "Mapping-only"]
    for before, kinds in ARMS.items():
        for rel in ("deployment", "ownership", "dependency"):
            if rel in kinds:
                continue
            after = next(name for name, selected in ARMS.items() if set(selected) == set(kinds) | {rel})
            if (before, after) not in comparisons:
                comparisons.append((before, after))
    result = []
    for before, after in comparisons:
        pairs = [(index[(before, r["case_id"])], index[(after, r["case_id"])])
                 for r in rows if r["arm"] == before]
        item = {"before": before, "after": after, "n": len(pairs)}
        for scope, field in (("service", "service_rank"), ("global", "global_rank")):
            item[f"{scope}_rank_improved"] = sum(b[field] < a[field] for a, b in pairs)
            item[f"{scope}_rank_same"] = sum(b[field] == a[field] for a, b in pairs)
            item[f"{scope}_rank_worsened"] = sum(b[field] > a[field] for a, b in pairs)
            for k in (1, 3, 5):
                item[f"{scope}_top{k}_gains"] = sum((b[field] <= k) and (a[field] > k) for a, b in pairs)
                item[f"{scope}_top{k}_losses"] = sum((b[field] > k) and (a[field] <= k) for a, b in pairs)
        result.append(item)
    return result


def run(dataset_root: Path, output: Path, historical_path: Path, limit: int = 0):
    output.mkdir(parents=True, exist_ok=True)
    cases = [p for p in case_paths(dataset_root) if p.parent.parent.name == "hipster_chaos"]
    if limit:
        cases = cases[:limit]
    case_ids = {str(p.relative_to(dataset_root)) for p in cases}
    historical = load_historical(historical_path, case_ids)
    rows, validations, audits = [], [], []
    started = time.time()
    for idx, case_path in enumerate(cases, 1):
        request = MicroCERCLAdapter().load(case_path)
        if len(request.evaluation.root_causes) != 1 or request.evaluation.root_causes[0]["kind"] != "service":
            raise AssertionError(f"expected one service root: {request.audit['case_id']}")
        engine = RCAEngine()
        reference = None
        cached_metric_scores = None
        for arm, kinds in ARMS.items():
            config = RCAConfig(relation_groups=("mapping", *kinds),
                               propagation_lambda=0.0 if not kinds else 0.1,
                               direction="bidirectional", score_normalization="raw",
                               entity_aggregation="max", edge_normalization="row",
                               mapping_policy="most_specific", score_mode="baro_dataframe_equivalent",
                               metric_policy="exclude_last_seen")
            result = engine.analyze(request.graph, request.observations, request.window, config)
            invariant = (result.base_scores, result.entity_mappings,
                         {kind: sorted(r["id"] for r in ranked)
                          for kind, ranked in result.rankings.items() if kind != "metric"},
                         result.rankings["metric"])
            if reference is None:
                reference = invariant
                cached_metric_scores = [(r["key"], r["score"]) for r in result.rankings["metric"]]
                engine = RCAEngine(scorer=lambda *_: cached_metric_scores)
            elif invariant != reference:
                raise AssertionError(f"base, mapping, candidates or metric scores changed: {request.audit['case_id']}")
            if {e["relation_kind"] for e in result.propagation_edges} - set(kinds):
                raise AssertionError("unselected relation was propagated")

            truth = request.evaluation.root_causes[0]
            service_rank = next((n for n, e in enumerate(result.rankings["service"], 1)
                                 if e["id"] == truth["id"]), None)
            global_rank = next((n for n, e in enumerate(result.rankings["all_entities"], 1)
                                if e["id"] == truth["id"]), None)
            if service_rank is None or global_rank is None:
                raise AssertionError("root absent from ranking")
            root_deltas = Counter()
            for contribution in result.contributions:
                if contribution["to"] == truth["id"]:
                    root_deltas[contribution["relation_kind"]] += contribution["delta"]
            row = {"case_id": request.audit["case_id"], "application": "hipster_chaos",
                   "fault": request.audit["fault"], "arm": arm,
                   "service_rank": service_rank, "global_rank": global_rank,
                   "root_base_score": result.base_scores[truth["id"]],
                   "root_final_score": result.final_scores[truth["id"]],
                   "root_deployment_delta": root_deltas["deployment"],
                   "root_ownership_delta": root_deltas["ownership"],
                   "root_dependency_delta": root_deltas["dependency"],
                   "propagation_edges": len(result.propagation_edges),
                   "deployment_edges": sum(e["relation_kind"] == "deployment" for e in result.propagation_edges),
                   "ownership_edges": sum(e["relation_kind"] == "ownership" for e in result.propagation_edges),
                   "dependency_edges": sum(e["relation_kind"] == "dependency" for e in result.propagation_edges)}
            old_arm = {"Mapping-only": "mapping-only", "Containment": "containment-only",
                       "Dependency": "dependency-only", "Full": "Full"}.get(arm)
            if old_arm:
                checks = compare_old(row, result, historical[(row["case_id"], arm)])
                if not checks["passed"]:
                    raise AssertionError(f"historical regression failed {row['case_id']} {arm}: {checks}")
                validations.append({"case_id": row["case_id"], "arm": arm, **checks})
            rows.append(row)

        audits.append({**request.audit,
                       "base_scores_sha256": hashlib.sha256(json.dumps(reference[0], sort_keys=True).encode()).hexdigest(),
                       "metric_scores_sha256": hashlib.sha256(json.dumps(cached_metric_scores, sort_keys=True).encode()).hexdigest(),
                       "all_arms_same_base_mapping_candidates": True})
        if idx % 10 == 0 or idx == len(cases):
            print(f"{idx}/{len(cases)} Hipster cases completed; elapsed={time.time()-started:.1f}s", flush=True)

    summaries, paired_rows = summarize(rows), paired(rows)
    validation = {"cases": len(cases), "historical_arm_case_comparisons": len(validations),
                  "all_passed": all(v["passed"] for v in validations),
                  "max_base_score_abs_error": max((v["base_scores_max_abs_error"] for v in validations), default=0.0),
                  "max_final_score_abs_error": max((v["final_scores_max_abs_error"] for v in validations), default=0.0)}
    protocol = {"dataset": "MicroCERCL", "subset": "hipster_chaos", "cases": len(cases),
                "arms": ARMS, "lambda": 0.1, "direction": "bidirectional", "edge_normalization": "row",
                "scoring": "BARO DataFrame-equivalent; max entity aggregation; most_specific; raw scores",
                "scope": "all monitored namespaces; labels used for evaluation only; service roots only",
                "ownership": "derived from exact known service prefix after Kubernetes suffix removal; not directly observed",
                "deployment": "pre-injection graph snapshot; Host-to-Pod",
                "metrics": "deterministic integer rank over service candidates and all graph entities",
                "mapping_only": "lambda=0, equivalent no-propagation; other arms use lambda=0.1",
                "historical_regression": validation,
                "subset_selection": "299 evaluated cases; historical containment gain over mapping was the largest on service Top-3 and Top-5 among the three subsets, with bookinfo 207 and sock_shop 376 cases"}
    (output / "relation_cases.csv").write_text("", encoding="utf-8")
    write_csv(output / "relation_cases.csv", rows)
    write_csv(output / "relation_summary.csv", summaries)
    write_csv(output / "paired_changes.csv", paired_rows)
    write_csv(output / "historical_validation.csv", validations)
    (output / "protocol.json").write_text(json.dumps({"protocol": protocol, "case_audits": audits}, ensure_ascii=False, indent=2), encoding="utf-8")
    table = ["# MicroCERCL Hipster relation split", "",
             "One subset was selected: Hipster (299 evaluable cases). Eight relation configurations use fixed BARO-compatible scoring, bidirectional propagation, lambda 0.1, and per-source outgoing-edge normalization.",
             "Ownership is derived from an exact known service prefix after removing the Kubernetes suffix. The MicroCERCL labels identify Service roots only.", "",
             "| Configuration | Service Top-1 | Service Top-3 | Service Top-5 | Service MRR | Global Top-1 | Global Top-3 | Global Top-5 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:
        table.append(f"| {s['arm']} | {s['service_top1']:.4f} | {s['service_top3']:.4f} | {s['service_top5']:.4f} | {s['service_mrr']:.4f} | {s['global_top1']:.4f} | {s['global_top3']:.4f} | {s['global_top5']:.4f} |")
    table += ["", "Paired rank movement", "", "| Before | After | Service improved | Same | Worsened | Global improved | Same | Worsened |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for p in paired_rows:
        if p["before"] == "Mapping-only" or p["before"] == "Containment":
            table.append(f"| {p['before']} | {p['after']} | {p['service_rank_improved']} | {p['service_rank_same']} | {p['service_rank_worsened']} | {p['global_rank_improved']} | {p['global_rank_same']} | {p['global_rank_worsened']} |")
    table += ["", f"Historical four-arm regression: {validation['historical_arm_case_comparisons']} case-arm comparisons; passed={validation['all_passed']}; maximum score errors {validation['max_base_score_abs_error']:.3g} (base), {validation['max_final_score_abs_error']:.3g} (final).", "",
              "Interpretation limits: the eight arms test relation combinations under this fixed propagation rule. Deployment cannot directly contribute to a Service root score because it connects Host and Pod; with Service-only labels, deployment's direct root-localization effect cannot be evaluated. Ownership relations are inferred from naming conventions, and no claim is made that MicroCERCL directly records ownership.",
              "", "The Full arm adds dependency edges and changes per-source normalization denominators, so Full versus Containment also includes redistribution of existing edge weights."]
    (output / "REPORT.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summaries, "paired": paired_rows, "historical_validation": validation}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("D:/AIOps/data/abnormal_20240615/abnormal"))
    parser.add_argument("--historical", type=Path, default=ROOT / "output/microcercl_full/results.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "output/microcercl_hipster_relation_split_l0p1")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    run(args.root, args.output, args.historical, args.limit)
