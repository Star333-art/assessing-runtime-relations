"""Paired AIOps2025 observation-coverage and degree-preserving topology controls.

Uses the frozen LSDB-protocol entity bases and case graph evidence. The BARO
scorer and one-step propagation core are not changed. Observation stress masks
already-mapped entity bases; shuffled controls swap endpoints within each
typed bipartite relation while preserving every source and target degree.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from ls_baro.topology import Edge, propagate_once
from ls_baro.contracts import RuntimeEntity, RuntimeGraph, Relation, MetricBinding, RCAConfig
from ls_baro.canonical_core import aggregate
from ls_baro.scoring import score_with_baro_dataframe_equivalent
from evaluate_aiops2025_ownership import load_day, epoch

SOURCE = ROOT / "output/aiops2025_ownership_pilot"
MAIN = ROOT / "output/aiops2025_lsdb_protocol_20261023"
OUT = ROOT / "output/aiops2025_observability_rewire_20261026"
DATA = ROOT.parent / "data/AgenticOpsEval/AIOps2025"
ARMS = {"mapping": (), "deployment": ("deployment",),
        "ownership": ("ownership",), "containment": ("deployment", "ownership")}
FRACTIONS = (0.0, 0.25, 0.50, 0.75, 1.0)
MASK_SEEDS = tuple(range(10))
SHUFFLE_REPLICATES = 20
LAMBDA = 0.1
EXCLUDED = {"29f88351-232", "0718e0f9-92", "d32bcd36-104"}


def _stats(names, scores, target):
    order = sorted(range(len(names)), key=lambda i: (-float(scores[i]), names[i]))
    root_i = names.index(target)
    rank = order.index(root_i) + 1
    root_score = float(scores[root_i])
    higher = sum(float(x) > root_score for x in scores)
    tied = sum(float(x) == root_score for x in scores)
    row = {"rank": rank, "root_score": root_score, "tie_size": tied}
    for k in range(1, 6):
        row[f"top{k}"] = int(rank <= k)
        row[f"tie_top{k}"] = float(np.clip((k - higher) / tied, 0, 1))
    row["avg5"] = sum(row[f"top{k}"] for k in range(1, 6)) / 5
    row["tie_avg5"] = sum(row[f"tie_top{k}"] for k in range(1, 6)) / 5
    return row


def _metric_seed(*parts):
    raw = "|".join(map(str, parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _expanded_edges(relations, groups):
    expanded = []
    for kind in ("deployment", "ownership"):
        if kind not in groups:
            continue
        for source, target in relations[kind]:
            expanded.extend(((source, target), (target, source)))
    degree = Counter(source for source, _ in expanded)
    return [Edge(source, target, 1.0 / degree[source], "typed", "randomization_control")
            for source, target in expanded]


def _rewire(edges, seed):
    """Directed bipartite 2-switches; preserve exact in/out degree sequence."""
    original = list(edges)
    work = list(edges)
    if len(work) < 2:
        return work, 0, 0.0
    rng = random.Random(seed)
    edge_set = set(work)
    accepted = 0
    for _ in range(max(200, 100 * len(work))):
        i, j = rng.sample(range(len(work)), 2)
        s1, t1 = work[i]
        s2, t2 = work[j]
        if s1 == s2 or t1 == t2:
            continue
        a, b = (s1, t2), (s2, t1)
        if a in edge_set or b in edge_set:
            continue
        edge_set.remove(work[i]); edge_set.remove(work[j])
        work[i], work[j] = a, b
        edge_set.add(a); edge_set.add(b)
        accepted += 1
    changed = sum(a != b for a, b in zip(original, work)) / len(work)
    assert Counter(s for s, _ in original) == Counter(s for s, _ in work)
    assert Counter(t for _, t in original) == Counter(t for _, t in work)
    assert len(set(work)) == len(work)
    return work, accepted, changed


def _apply(base, names, relations, groups):
    edges = _expanded_edges(relations, groups)
    final, _ = propagate_once(dict(zip(names, map(float, base))), edges, LAMBDA)
    return np.asarray([final[n] for n in names], dtype=float)


def _reconstruct_full_cases(audit, frozen, deployment, ownership, labels, data_root):
    """Recreate every mapped entity base, including non-evaluated source layers."""
    by_id = {x["uuid"]: x for x in labels}
    grouped = defaultdict(list)
    for row in audit.itertuples():
        grouped[pd.Timestamp(by_id[row.case_id]["start_time"]).tz_convert("Asia/Shanghai").date().isoformat()].append(row)
    result = {}
    for day, day_rows in sorted(grouped.items()):
        cache_path = OUT / f"mapped_case_bases_{day}.pkl"
        if cache_path.exists():
            with cache_path.open("rb") as stream:
                cached = pickle.load(stream)
            result.update(cached)
            print(f"Using verified mapped-case cache: {day} ({len(cached)} cases)", flush=True)
            continue
        print(f"Rebuilding full mapped graph: {day} ({len(day_rows)} evaluated cases)", flush=True)
        data, _, _, _ = load_day(data_root, day)
        series = {}
        for key, frame in data.sort_values("t").groupby("metric", sort=False):
            series[key] = (frame.t.to_numpy(), frame.value.to_numpy(),
                           str(frame.kind.iloc[0]), str(frame.entity.iloc[0]))
        del data
        for row in day_rows:
            label = by_id[row.case_id]
            start = epoch(label["start_time"])
            candidates, metrics, owners = set(), {}, {}
            for key, (ts, values, kind, name) in series.items():
                inside = (ts >= start - 600) & (ts < start + 600)
                if not inside.any():
                    continue
                owner = (kind, name)
                candidates.add(owner)
                metrics[key] = list(zip(ts[inside].tolist(), values[inside].tolist()))
                owners[key] = owner
            ids = {entity: json.dumps(entity) for entity in candidates}
            relations = {"deployment": [], "ownership": []}
            for edge in deployment[deployment.case_id == row.case_id].itertuples():
                source, target = ("host", str(edge.node)), ("pod", str(edge.pod))
                if source in ids and target in ids:
                    relations["deployment"].append((ids[source], ids[target]))
            for edge in ownership[ownership.case_id == row.case_id].itertuples():
                source, target = ("service", str(edge.service)), ("pod", str(edge.pod))
                if source in ids and target in ids:
                    relations["ownership"].append((ids[source], ids[target]))
            entities = tuple(RuntimeEntity(ids[e], *e) for e in sorted(ids))
            graph = RuntimeGraph(entities, tuple(
                [Relation(f"d:{s}->{t}", s, t, "deployment", "saved_observation") for s, t in relations["deployment"]] +
                [Relation(f"o:{s}->{t}", s, t, "ownership", "saved_observation") for s, t in relations["ownership"]]),
                tuple(MetricBinding(k, ids[owners[k]], "observed_metric") for k in metrics),
                "aiops2025", day)
            metric_scores = dict(score_with_baro_dataframe_equivalent(metrics, start))
            config = RCAConfig(relation_groups=("mapping",), propagation_lambda=LAMBDA,
                direction="bidirectional", edge_normalization="row", metric_policy="all")
            base_map, _ = aggregate(graph, metric_scores, {}, dict(zip(metrics, metrics)), config)
            entity_ids = [e.id for e in entities]
            entity_kinds = [e.kind for e in entities]
            entity_names = [e.name for e in entities]
            base = np.asarray([base_map[eid] for eid in entity_ids], dtype=float)
            layer = str(row.level)
            eval_ix = np.asarray([i for i, kind in enumerate(entity_kinds) if kind == layer], dtype=int)
            eval_names = [entity_names[i] for i in eval_ix]
            target = json.loads(row.targets)[0]
            if target not in eval_names:
                raise ValueError(f"Root target not reconstructed among candidates: {row.case_id} {target}")
            # Guard against accidental scorer/adapter drift on the evaluated layer.
            saved = frozen[(frozen.case_id == row.case_id) & (frozen.arm == "mapping")].set_index("entity")
            reconstructed = {entity_names[i]: float(base[i]) for i in eval_ix}
            if set(reconstructed) != set(saved.index.astype(str)):
                raise ValueError(f"Candidate set changed while rebuilding {row.case_id}")
            err = max(abs(reconstructed[name] - float(saved.loc[name, "base"])) for name in reconstructed)
            if err > 1e-8:
                raise ValueError(f"Frozen mapping score mismatch for {row.case_id}: {err}")
            result[row.case_id] = dict(level=layer, target=target, ids=entity_ids,
                entity_names=entity_names, kinds=entity_kinds, eval_indices=eval_ix,
                eval_names=eval_names, base=base, relations=relations,
                reconstructed_mapping_max_error=err)
        with cache_path.open("wb") as stream:
            pickle.dump({row.case_id: result[row.case_id] for row in day_rows}, stream,
                        protocol=pickle.HIGHEST_PROTOCOL)
    if len(result) != len(audit):
        raise ValueError(f"Expected {len(audit)} reconstructed cases, got {len(result)}")
    return result


def _bootstrap_diff(case_table, left, right, metric, seed, repeats=5000):
    pivot = case_table.pivot(index="case_id", columns="arm", values=metric).dropna()
    delta = (pivot[left] - pivot[right]).to_numpy(float)
    rng = np.random.default_rng(seed)
    draws = np.mean(delta[rng.integers(0, len(delta), size=(repeats, len(delta)))], axis=1)
    return {"difference_pp": 100 * float(delta.mean()),
            "ci95_low_pp": 100 * float(np.quantile(draws, .025)),
            "ci95_high_pp": 100 * float(np.quantile(draws, .975)),
            "paired_cases": int(len(delta))}


def main():
    OUT.mkdir(exist_ok=True)
    input_paths = [SOURCE / "rankings.csv", SOURCE / "deployment_evidence.csv",
                   SOURCE / "ownership_evidence.csv", MAIN / "label_audit.csv",
                   DATA / "groundtruth.jsonl", ROOT / "tools/evaluate_aiops2025_ownership.py",
                   ROOT / "src/ls_baro/scoring.py", ROOT / "src/ls_baro/canonical_core.py",
                   ROOT / "src/ls_baro/topology.py"]
    audit = pd.read_csv(MAIN / "label_audit.csv")
    audit = audit[audit.status == "single_entity_main"].copy()
    assert len(audit) == 272
    frozen = pd.read_csv(SOURCE / "rankings.csv")
    deployment = pd.read_csv(SOURCE / "deployment_evidence.csv")
    ownership = pd.read_csv(SOURCE / "ownership_evidence.csv")
    raw_labels = [json.loads(x) for x in (DATA / "groundtruth.jsonl").read_text(encoding="utf-8").splitlines()]
    labels = {x["uuid"]: x for x in raw_labels}

    cases = _reconstruct_full_cases(audit, frozen, deployment, ownership, raw_labels, DATA)

    # 1. Graded observation availability. One shared mask per case/layer/
    # fraction/seed is applied to all relation arms (paired comparison).
    gradient_rows, mask_rows = [], []
    for cid, c in cases.items():
        for layer in ("host", "pod", "service"):
            eligible = np.array([i for i, (kind, score) in enumerate(zip(c["kinds"], c["base"]))
                                 if kind == layer and score != 0.0], dtype=int)
            for fraction in FRACTIONS:
                for seed in MASK_SEEDS:
                    masked_idx = np.array([], dtype=int)
                    if fraction > 0 and len(eligible):
                        nmask = len(eligible) if fraction == 1 else int(round(fraction * len(eligible)))
                        rng = np.random.default_rng(_metric_seed("mask", cid, layer, fraction, seed))
                        masked_idx = np.sort(rng.choice(eligible, size=nmask, replace=False))
                    mask = np.zeros(len(c["ids"]), dtype=bool); mask[masked_idx] = True
                    b = c["base"].copy(); b[mask] = 0.0
                    root_ix = c["ids"].index(json.dumps((c["level"], c["target"])))
                    mask_rows.append(dict(case_id=cid, layer_masked=layer, fraction=fraction,
                        seed=seed, originally_observed=int(len(eligible)), masked_count=int(mask.sum()),
                        root_was_masked=bool(mask[root_ix])))
                    for arm, groups in ARMS.items():
                        score = b.copy() if not groups else _apply(b, c["ids"], c["relations"], groups)
                        metric = _stats(c["eval_names"], score[c["eval_indices"]], c["target"])
                        gradient_rows.append(dict(case_id=cid, level=c["level"], arm=arm,
                            layer_masked=layer, fraction=fraction, seed=seed,
                            originally_observed=int(len(eligible)), masked_count=int(mask.sum()),
                            root_was_masked=bool(mask[root_ix]), **metric))
    gradient = pd.DataFrame(gradient_rows)
    gradient.to_csv(OUT / "observation_gradient_cases.csv", index=False)
    pd.DataFrame(mask_rows).to_csv(OUT / "observation_masks.csv", index=False)

    # Summarize by first averaging randomized masks within each case, then
    # bootstrap paired case differences. The case remains the sampling unit.
    metric_fields = [f"top{k}" for k in range(1, 6)] + [f"tie_top{k}" for k in range(1, 6)] + ["avg5", "tie_avg5"]
    grad_case = (gradient.groupby(["case_id", "level", "arm", "layer_masked", "fraction"], as_index=False)
                 [metric_fields].mean())
    grad_summary, grad_pairs = [], []
    for (level, layer, fraction, arm), g in grad_case.groupby(["level", "layer_masked", "fraction", "arm"]):
        rec = dict(level=level, layer_masked=layer, fraction=fraction, arm=arm, cases=len(g))
        rec.update({m: float(g[m].mean()) for m in ["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"]})
        rec.update({f"{m}_sd_across_cases": float(g[m].std(ddof=1)) for m in ["top1", "top3", "top5", "avg5"]})
        grad_summary.append(rec)
    for (level, layer, fraction), g in grad_case.groupby(["level", "layer_masked", "fraction"]):
        for arm in ("deployment", "ownership", "containment"):
            for metric in ("top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"):
                delta = _bootstrap_diff(g, arm, "mapping", metric,
                    _metric_seed("bootstrap-gradient", level, layer, fraction, arm, metric))
                grad_pairs.append(dict(level=level, layer_masked=layer, fraction=fraction,
                    comparison=f"{arm}-mapping", metric=metric, **delta))
    pd.DataFrame(grad_summary).to_csv(OUT / "observation_gradient_summary.csv", index=False)
    pd.DataFrame(grad_pairs).to_csv(OUT / "observation_gradient_paired_ci.csv", index=False)

    # 2. Degree-preserving typed rewiring. Compare true topology with 20
    # independently rewired graphs under native evidence and Host-zero evidence.
    shuffle_rows, shuffle_audit = [], []
    for cid, c in cases.items():
        names, base, relations = c["ids"], c["base"], c["relations"]
        for rep in range(SHUFFLE_REPLICATES):
            rewired, audit_rel = {}, {}
            for kind in ("deployment", "ownership"):
                seed = _metric_seed("rewire", cid, rep, kind)
                rewired[kind], swaps, changed = _rewire(relations[kind], seed)
                audit_rel[kind] = (swaps, changed)
            shuffle_audit.append(dict(case_id=cid, replicate=rep,
                deployment_edges=len(relations["deployment"]),
                deployment_accepted_swaps=audit_rel["deployment"][0],
                deployment_fraction_edges_changed=audit_rel["deployment"][1],
                ownership_edges=len(relations["ownership"]),
                ownership_accepted_swaps=audit_rel["ownership"][0],
                ownership_fraction_edges_changed=audit_rel["ownership"][1]))
            for obs in ("native", "host_zero"):
                b = base.copy()
                if obs == "host_zero":
                    b[np.array(c["kinds"]) == "host"] = 0.0
                for arm, groups in ARMS.items():
                    true_scores = b.copy() if not groups else _apply(b, names, relations, groups)
                    true_metric = _stats(c["eval_names"], true_scores[c["eval_indices"]], c["target"])
                    if arm == "mapping":
                        shuffled_scores = b.copy()
                    else:
                        shuffled_rel = {k: (rewired[k] if k in groups else relations[k])
                                        for k in ("deployment", "ownership")}
                        shuffled_scores = _apply(b, names, shuffled_rel, groups)
                    shuffled_metric = _stats(c["eval_names"], shuffled_scores[c["eval_indices"]], c["target"])
                    for topology, metric in (("true", true_metric), ("degree_preserving_shuffle", shuffled_metric)):
                        shuffle_rows.append(dict(case_id=cid, level=c["level"], observation=obs,
                            arm=arm, topology=topology, replicate=rep, **metric))
    shuffle = pd.DataFrame(shuffle_rows)
    shuffle.to_csv(OUT / "rewired_topology_cases.csv", index=False)
    pd.DataFrame(shuffle_audit).to_csv(OUT / "rewiring_integrity.csv", index=False)

    # For each case, average shuffle replicates before paired case bootstrap.
    sh_case = (shuffle.groupby(["case_id", "level", "observation", "arm", "topology"], as_index=False)
               [metric_fields].mean())
    sh_summary, sh_pairs = [], []
    for (level, obs, arm, topology), g in sh_case.groupby(["level", "observation", "arm", "topology"]):
        rec = dict(level=level, observation=obs, arm=arm, topology=topology, cases=len(g))
        rec.update({m: float(g[m].mean()) for m in ["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"]})
        sh_summary.append(rec)
    for (level, obs, arm), g in sh_case.groupby(["level", "observation", "arm"]):
        both = g.pivot(index="case_id", columns="topology", values=["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"])
        paired = pd.DataFrame({m: both[(m, "true")] - both[(m, "degree_preserving_shuffle")]
                               for m in ["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"]}).reset_index()
        for metric in ("top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"):
            values = paired[metric].to_numpy(float)
            rng = np.random.default_rng(_metric_seed("bootstrap-shuffle", level, obs, arm, metric))
            draws = np.mean(values[rng.integers(0, len(values), size=(5000, len(values)))], axis=1)
            delta = {"difference_pp": 100 * float(values.mean()),
                     "ci95_low_pp": 100 * float(np.quantile(draws, .025)),
                     "ci95_high_pp": 100 * float(np.quantile(draws, .975)),
                     "paired_cases": int(len(values))}
            sh_pairs.append(dict(level=level, observation=obs, arm=arm, metric=metric, **delta))
    pd.DataFrame(sh_summary).to_csv(OUT / "rewired_topology_summary.csv", index=False)
    pd.DataFrame(sh_pairs).to_csv(OUT / "rewired_topology_paired_ci.csv", index=False)

    # Hash inputs and script after all outputs; report explicit design details.
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    verification = dict(experiment="AIOps2025 observation gradient and degree-preserving typed rewiring",
        cases=272, lambda_value=LAMBDA, arms=ARMS, observation_layers=["host", "pod", "service"],
        observed_entity_mask_fractions=FRACTIONS, mask_seeds=MASK_SEEDS,
        shuffle_replicates=SHUFFLE_REPLICATES,
        shuffle_method="directed bipartite 2-switch within relation type and case; exact source/target degrees preserved",
        graph_semantics={"deployment": "Host->Pod", "ownership": "Service->Pod"},
        propagation="unchanged one-step bidirectional source-row normalization; fixed lambda=0.1",
        case_is_bootstrap_unit=True, mask_selection_uses_root_labels=False,
        actual_label_distribution=audit.level.value_counts().to_dict(),
        max_reconstructed_mapping_score_error=max(c["reconstructed_mapping_max_error"] for c in cases.values()),
        source_hashes=hashes, script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (OUT / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")

    # Compact, reproducible human-readable report; preserve uncertainty and
    # note how many relations could not be shuffled in each replicate.
    lines = ["# AIOps2025 observation gradient and degree-preserving rewiring", "",
        "272 native single-entity cases. Frozen BARO-compatible base scores, candidate sets, one-step bidirectional propagation, source-row normalization, and lambda=0.1 are fixed.",
        "Observation masks set uniformly sampled, originally nonzero entity bases to zero. Every relation arm uses the same case/layer/fraction/seed mask; root labels do not guide mask selection.",
        "Typed directed bipartite 2-switches preserve exact source and target degrees within each case and relation type. Paired intervals resample cases after averaging mask seeds or rewiring replicates within case.",
        "Both deterministic name ordering and tie-expected Top-k are reported. Tie-expected values should be preferred when masking creates tied scores.", "",
        "## Own-layer observation gradient", "",
        "| Root layer | Masked layer | Fraction | Arm | N | Top-1 | Top-3 | Top-5 | Avg@5 | Tie-expected Top-1 | Top-3 | Top-5 | Avg@5 |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    gs = pd.DataFrame(grad_summary)
    for level in ("host", "pod", "service"):
        for layer in (level,):
            for frac in FRACTIONS:
                sub = gs[(gs.level == level) & (gs.layer_masked == layer) & (gs.fraction == frac)]
                if sub.empty: continue
                for arm in ARMS:
                    r = sub[sub.arm == arm].iloc[0]
                    lines.append(f"| {level} | {layer} | {frac:.0%} | {arm} | {int(r.cases)} | {r.top1:.2%} | {r.top3:.2%} | {r.top5:.2%} | {r.avg5:.4f} | {r.tie_top1:.2%} | {r.tie_top3:.2%} | {r.tie_top5:.2%} | {r.tie_avg5:.4f} |")
    lines += ["", "## True versus degree-preserving shuffled topology", "",
        "Each shuffled cell averages 20 rewired graphs within case before averaging cases. Paired 95% case-bootstrap intervals are in rewired_topology_paired_ci.csv.", "",
        "| Root layer | Observation | Arm | Topology | N | Top-1 | Top-3 | Top-5 | Avg@5 | Tie-expected Top-1 | Top-3 | Top-5 | Avg@5 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    ss = pd.DataFrame(sh_summary)
    for level in ("host", "pod", "service"):
        for obs in ("native", "host_zero"):
            for arm in ARMS:
                for topo in ("true", "degree_preserving_shuffle"):
                    sub = ss[(ss.level == level) & (ss.observation == obs) & (ss.arm == arm) & (ss.topology == topo)]
                    if sub.empty: continue
                    r = sub.iloc[0]
                    lines.append(f"| {level} | {obs} | {arm} | {topo} | {int(r.cases)} | {r.top1:.2%} | {r.top3:.2%} | {r.top5:.2%} | {r.avg5:.4f} | {r.tie_top1:.2%} | {r.tie_top3:.2%} | {r.tie_top5:.2%} | {r.tie_avg5:.4f} |")
    ra = pd.DataFrame(shuffle_audit)
    lines += ["", "## Rewiring integrity", "",
        f"Per graph, Deployment averages {ra.deployment_edges.mean():.1f} edges and {ra.deployment_accepted_swaps.mean():.1f} accepted swaps; Ownership averages {ra.ownership_edges.mean():.1f} edges and {ra.ownership_accepted_swaps.mean():.1f} accepted swaps.",
        f"Replicates with at least one swap: Deployment {(ra.deployment_accepted_swaps>0).mean():.2%}; Ownership {(ra.ownership_accepted_swaps>0).mean():.2%}. Exact degree and duplicate-edge checks are recorded in rewiring_integrity.csv. Replicates={SHUFFLE_REPLICATES}.", "",
        "## Scope", "",
        "This is a controlled test for the current AIOps2025 adapter, mapped scores, and one-step propagation. It does not establish medical-domain effectiveness or imply that an unobserved edge is physically absent.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}; gradient rows={len(gradient):,}, shuffle rows={len(shuffle):,}")
    print("Observation summary (Host roots, Host masked):")
    print(gs[(gs.level == "host") & (gs.layer_masked == "host")][["fraction", "arm", "top1", "top3", "top5", "avg5"]].to_string(index=False))
    print("Shuffle integrity:")
    print(ra[["deployment_accepted_swaps", "ownership_accepted_swaps",
             "deployment_fraction_edges_changed", "ownership_fraction_edges_changed"]].describe().to_string())


if __name__ == "__main__":
    main()
