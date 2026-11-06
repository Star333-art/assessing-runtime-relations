"""Fixed eight-arm LSDB relation ablation, using the shared RCA engine.

This runner does not select hyperparameters. It scores each case once, keeps
all candidates and initial metric assignments fixed, and varies only relation
selectors. Labels are consulted only after the engine returns.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ls_baro.contracts import RCAConfig
from ls_baro.engine import RCAEngine
from ls_baro.lsdb_adapter import LSDBAdapter
from ls_baro.pipeline import _legacy_result_view, root_cause_rank, root_cause_rank_details


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
HISTORICAL = {
    "Mapping-only": "V3_M0_mapping_only", "Containment": "V3_C_containment",
    "Dependency": "V3_D_dependency", "Full": "V3_CD_full",
}


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def case_row(name, adapted, result, config):
    evaluation = adapted.evaluation
    if len(evaluation.root_causes) != 1:
        raise ValueError("This evaluation protocol requires one root cause per case")
    truth = evaluation.root_causes[0]
    view = _legacy_result_view(result, evaluation, config)
    level = root_cause_rank_details(view, "level")
    global_detail = root_cause_rank_details(view, "global")
    rank = root_cause_rank(view)
    if level is None or global_detail is None or rank is None:
        raise ValueError(f"case {evaluation.case_id}: root cause absent from candidates")
    global_rank = next(i for i, r in enumerate(result.rankings["all_entities"], 1)
                       if r["kind"] == truth["type"] and r["key"] == truth["key"])
    root_id = next(e.id for e in adapted.graph.entities
                   if e.kind == truth["type"] and e.name == truth["key"])
    incoming = defaultdict(float)
    for contribution in result.contributions:
        if contribution["to"] == root_id:
            incoming[contribution["relation_kind"]] += contribution["delta"]
    hits = {k: float(rank <= k) for k in range(1, 6)}
    row = {
        "experiment": name, "case_id": evaluation.case_id,
        "truth_level": truth["type"], "truth_key": truth["key"],
        "baro_rank": rank, **{f"baro_top{k}": v for k, v in hits.items()},
        "baro_avg5": sum(hits.values()) / 5,
        **level, **{f"global_{k}": v for k, v in global_detail.items()},
        "global_deterministic_rank": global_rank,
        "global_deterministic_top1": float(global_rank <= 1),
        "global_deterministic_top3": float(global_rank <= 3),
        "global_deterministic_top5": float(global_rank <= 5),
        "level_candidates": len(result.rankings[truth["type"]]),
        "global_candidates": len(result.rankings["all_entities"]),
        "root_base_score": result.base_scores[root_id],
        "root_final_score": result.final_scores[root_id],
        "root_deployment_delta": incoming["deployment"],
        "root_ownership_delta": incoming["ownership"],
        "root_dependency_delta": incoming["dependency"],
        "propagation_edges": len(result.propagation_edges),
    }
    for kind in ("deployment", "ownership", "dependency"):
        row[f"{kind}_edges"] = sum(e["relation_kind"] == kind for e in result.propagation_edges)
    return row


def summarize(rows):
    summaries = []
    for name in ARMS:
        for level in ("all", "host", "pod", "service"):
            selected = [r for r in rows if r["experiment"] == name
                        and (level == "all" or r["truth_level"] == level)]
            if not selected:
                continue
            n = len(selected)
            summary = {"experiment": name, "truth_level": level, "cases": n}
            for field in ("baro_top1", "baro_top3", "baro_top5", "baro_avg5",
                          "top1", "top3", "top5", "global_top1", "global_top3", "global_top5",
                          "global_deterministic_top1", "global_deterministic_top3", "global_deterministic_top5"):
                summary[field] = sum(r[field] for r in selected) / n
            summary["mrr"] = sum(1 / r["rank"] for r in selected) / n
            summary["global_mrr"] = sum(1 / r["global_rank"] for r in selected) / n
            summaries.append(summary)
    return summaries


def paired(rows):
    indexed = {(r["experiment"], r["case_id"]): r for r in rows}
    comparisons = [("Mapping-only", name, "vs_mapping") for name in ARMS if name != "Mapping-only"]
    for before, kinds in ARMS.items():
        for relation in ("deployment", "ownership", "dependency"):
            if relation in kinds:
                continue
            after = next(name for name, selected in ARMS.items()
                         if set(selected) == set(kinds) | {relation})
            if (before, after) not in [(a, b) for a, b, _ in comparisons]:
                comparisons.append((before, after, "add_" + relation))
    output, case_deltas = [], []
    for before, after, context in comparisons:
        pairs = [(r, indexed[(after, r["case_id"])]) for r in rows if r["experiment"] == before]
        for level in ("all", "host", "pod", "service"):
            subset = [(a, b) for a, b in pairs if level == "all" or a["truth_level"] == level]
            if not subset:
                continue
            for scope, rank_field, prefix in (("known_layer", "baro_rank", "baro_"),
                                              ("global_tie_aware", "global_rank", "global_")):
                row = dict(before=before, after=after, context=context, truth_level=level,
                           scope=scope, cases=len(subset))
                row.update(improved=sum(b[rank_field] < a[rank_field] for a, b in subset),
                           same=sum(b[rank_field] == a[rank_field] for a, b in subset),
                           worsened=sum(b[rank_field] > a[rank_field] for a, b in subset))
                for k in (1, 3, 5):
                    field = f"{prefix}top{k}"
                    row[f"top{k}_gain_cases"] = sum(b[field] > a[field] for a, b in subset)
                    row[f"top{k}_loss_cases"] = sum(b[field] < a[field] for a, b in subset)
                    row[f"top{k}_delta"] = sum(b[field] - a[field] for a, b in subset) / len(subset)
                output.append(row)
        for a, b in pairs:
            case_deltas.append(dict(before=before, after=after, case_id=a["case_id"],
                                    truth_level=a["truth_level"], truth_key=a["truth_key"],
                                    layer_rank_before=a["baro_rank"], layer_rank_after=b["baro_rank"],
                                    global_rank_before=a["global_rank"], global_rank_after=b["global_rank"]))
    return output, case_deltas


def verify_history(rows, path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        old = {(r["experiment"], r["case_id"]): r for r in csv.DictReader(stream)}
    fields = ("rank", "rank_min", "rank_max", "tie_size", "baro_rank", "baro_top1",
              "baro_top2", "baro_top3", "baro_top4", "baro_top5", "baro_avg5",
              "top1", "top3", "top5", "global_rank", "global_rank_min", "global_rank_max",
              "global_tie_size", "global_top1", "global_top3", "global_top5",
              "level_candidates", "global_candidates")
    comparisons, max_error = 0, 0.0
    for row in rows:
        if row["experiment"] not in HISTORICAL:
            continue
        reference = old[(HISTORICAL[row["experiment"]], row["case_id"])]
        if row["truth_level"] != reference["truth_level"] or row["truth_key"] != reference["truth_key"]:
            raise AssertionError("Historical root cause mismatch")
        for field in fields:
            error = abs(float(row[field]) - float(reference[field]))
            max_error = max(max_error, error)
            if error > 1e-12:
                raise AssertionError(f"Historical mismatch: {row['case_id']} {row['experiment']} {field}")
        comparisons += 1
    return {"reference": str(path.resolve()), "case_arm_comparisons": comparisons,
            "compared_fields": list(fields), "max_error": max_error, "passed": True}


def report(output, summary, paired_rows, protocol):
    lines = ["# LSDB deployment / ownership relation ablation", "",
             f"Cases: {protocol['cases']}; fixed lambda=0.1, bidirectional, source-row normalization.",
             "Scoring: BARO DataFrame equivalent; max aggregation; most_specific ownership; exclude_last_seen.",
             "All eight arms retain the same graph, candidate entities, metric scores and base scores.", "",
             "## Known-root-layer evaluation (deterministic BARO-compatible positions)", "",
             "| Configuration | Top-1 | Top-3 | Top-5 | Avg@5 |",
             "|---|---:|---:|---:|---:|"]
    for r in summary:
        if r["truth_level"] == "all":
            lines.append(f"| {r['experiment']} | {r['baro_top1']:.4f} | {r['baro_top3']:.4f} | {r['baro_top5']:.4f} | {r['baro_avg5']:.4f} |")
    for level in ("host", "pod", "service"):
        lines.extend(["", f"### {level.title()} roots", "",
                      "| Configuration | Cases | Top-1 | Top-3 | Top-5 | Avg@5 |",
                      "|---|---:|---:|---:|---:|---:|"])
        for r in summary:
            if r["truth_level"] == level:
                lines.append(f"| {r['experiment']} | {r['cases']} | {r['baro_top1']:.4f} | {r['baro_top3']:.4f} | {r['baro_top5']:.4f} | {r['baro_avg5']:.4f} |")
    lines.extend(["", "## Global evaluation (fractional tie credit, historical convention)", "",
                  "| Configuration | Top-1 | Top-3 | Top-5 | MRR |", "|---|---:|---:|---:|---:|"])
    for r in summary:
        if r["truth_level"] == "all":
            lines.append(f"| {r['experiment']} | {r['global_top1']:.4f} | {r['global_top3']:.4f} | {r['global_top5']:.4f} | {r['global_mrr']:.4f} |")
    lines.extend(["", "## Paired layer-rank changes", "",
                  "| Before | After | Improved | Same | Worsened |", "|---|---|---:|---:|---:|"])
    for r in paired_rows:
        if r["truth_level"] == "all" and r["scope"] == "known_layer":
            lines.append(f"| {r['before']} | {r['after']} | {r['improved']} | {r['same']} | {r['worsened']} |")
    lines.extend(["", "## Interpretation limits", "",
                  "- Layer-restricted results use the known ground-truth type to restrict evaluation candidates; global results do not.",
                  "- Deployment and ownership selectors are distinct relation kinds. Containment remains their union.",
                  "- Outgoing normalization is recomputed in each arm. Added relations also redistribute existing weights.",
                  "- This is a fixed-policy component ablation, not parameter selection or evidence of causal directions.",
                  "- A positive contribution to the root score need not improve its rank; other candidate scores also change.",
                  "- No GNN, training or additional propagation rounds were introduced.", "",
                  "## Validation", "", json.dumps(protocol["historical_validation"], indent=2)])
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(dataset_root, output, case_ids, historical_path):
    output.mkdir(parents=True, exist_ok=True)
    rows, audits = [], []
    source_hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [Path(__file__), *[Path(__file__).parent / "src" / "ls_baro" / n
                                                 for n in ("engine.py", "canonical_core.py", "contracts.py",
                                                           "lsdb_adapter.py", "scoring.py", "pipeline.py", "topology.py")]]}
    for index, case_id in enumerate(case_ids, 1):
        adapted = LSDBAdapter().load(dataset_root, case_id)
        engine, reference, details = RCAEngine(), None, {}
        scores = None
        for name, kinds in ARMS.items():
            config = RCAConfig(relation_groups=("mapping", *kinds), propagation_lambda=0.0 if not kinds else 0.1,
                               direction="bidirectional", score_normalization="raw", entity_aggregation="max",
                               edge_normalization="row", mapping_policy="most_specific",
                               score_mode="baro_dataframe_equivalent", metric_policy="exclude_last_seen")
            result = engine.analyze(adapted.graph, adapted.observations, adapted.window, config)
            current = (result.base_scores, result.entity_mappings,
                       {(kind, r["id"]) for kind, rs in result.rankings.items() if kind != "metric" for r in rs},
                       result.rankings["metric"])
            if reference is None:
                reference = current
                scores = [(r["key"], r["score"]) for r in result.rankings["metric"]]
                engine = RCAEngine(scorer=lambda *_: scores)
            elif current != reference:
                raise AssertionError(f"case {case_id}: scoring, candidates or metric assignments changed")
            if {e["relation_kind"] for e in result.propagation_edges} - set(kinds):
                raise AssertionError("Unexpected selected relation type")
            row = case_row(name, adapted, result, config)
            rows.append(row)
            details[name] = {"config": asdict(config), "evaluation": row,
                             "final_scores": result.final_scores,
                             "rankings": {k: v for k, v in result.rankings.items() if k != "metric"},
                             "contributions": result.contributions}
        audit = {"case_id": case_id, "graph_version": adapted.graph.graph_version,
                 "entities": len(adapted.graph.entities), "relations": dict(Counter(r.kind for r in adapted.graph.relations)),
                 "metric_scores_sha256": fingerprint(scores), "base_scores_sha256": fingerprint(reference[0]),
                 "candidates_and_metric_assignments_equal": True}
        audits.append(audit)
        write_json(output / "details" / f"{case_id}.json",
                   {"audit": audit, "base_scores": reference[0],
                    "window": asdict(adapted.window), "window_source": adapted.evaluation.window_source,
                    "entities": [asdict(e) for e in adapted.graph.entities],
                    "relations": [asdict(r) for r in adapted.graph.relations], "experiments": details})
        print(f"[{index}/{len(case_ids)}] case {case_id}: eight arms, fixed scores/candidates verified", flush=True)
    summaries = summarize(rows)
    paired_rows, case_deltas = paired(rows)
    validation = verify_history(rows, historical_path) if historical_path else {"passed": None, "reason": "no historical reference supplied"}
    protocol = {"dataset": "LatentScope Dataset B", "dataset_root": str(dataset_root.resolve()),
                "cases": len(case_ids), "case_ids": case_ids, "experiments": ARMS,
                "configuration": asdict(RCAConfig(propagation_lambda=0.1, direction="bidirectional", edge_normalization="row")),
                "label_usage": "root identity/type used only after scoring for evaluation; public injection time defines window",
                "metric_definitions": {"baro_topN": "deterministic rank within known root layer",
                                       "topN": "fractional tie credit within known root layer",
                                       "global_topN": "fractional tie credit over all entity types",
                                       "baro_avg5": "mean deterministic Top-1 through Top-5 hit rates"},
                "all_candidate_and_base_score_checks_passed": True,
                "historical_validation": validation, "source_sha256": source_hashes,
                "case_audits": audits}
    write_csv(output / "relation_cases.csv", rows)
    write_csv(output / "relation_summary.csv", summaries)
    write_csv(output / "paired_changes.csv", paired_rows)
    write_csv(output / "paired_cases.csv", case_deltas)
    write_json(output / "protocol.json", protocol)
    write_json(output / "relation_summary.json", {"protocol": protocol, "summaries": summaries, "paired_changes": paired_rows})
    report(output, summaries, paired_rows, protocol)
    for r in summaries:
        if r["truth_level"] == "all":
            print(f"{r['experiment']}: Top1={r['baro_top1']:.4f} Top3={r['baro_top3']:.4f} Top5={r['baro_top5']:.4f} Avg5={r['baro_avg5']:.4f}")
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=Path(__file__).resolve().parent.parent / "LatentScope/data/dataset_b")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "output/lsdb_relation_split_l0p1")
    parser.add_argument("--case", action="append")
    parser.add_argument("--historical-cases", type=Path, default=Path(__file__).resolve().parent / "output/scheme_c_l0p1_bidirectional/ablation_cases.csv")
    args = parser.parse_args()
    labels = json.loads((args.dataset_root / "labels/label.json").read_text(encoding="utf-8"))
    run(args.dataset_root, args.output, args.case or sorted(labels, key=int), args.historical_cases)
