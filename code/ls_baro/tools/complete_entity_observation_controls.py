"""Degree-only controls and frozen-protocol LSDB observation gradients.

Matches evaluate_aiops2025_observability_and_rewire.py masking and metrics.
No changes to the scorer, graph, mapping, propagation rule, or lambda.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from ls_baro.contracts import RuntimeEntity, RuntimeGraph, Relation, RCAConfig
from ls_baro.canonical_core import propagation
from ls_baro.topology import propagate_once
from evaluate_aiops2025_observability_and_rewire import _stats, _metric_seed

OUT = ROOT / "output/entity_observation_followup_20261026"
AI_SOURCE = ROOT / "output/aiops2025_observability_rewire_20261026"
LS_SOURCE = ROOT / "output/lsdb_relation_split_l0p1"
AI_ARMS = {"mapping": (), "deployment": ("deployment",),
           "ownership": ("ownership",), "containment": ("deployment", "ownership")}
ARMS = {**AI_ARMS, "dependency": ("dependency",),
        "deployment_dependency": ("deployment", "dependency"),
        "ownership_dependency": ("ownership", "dependency"),
        "full": ("deployment", "ownership", "dependency")}
LS_NAMES = {"mapping": "Mapping-only", "deployment": "Deployment", "ownership": "Ownership",
            "containment": "Containment", "dependency": "Dependency",
            "deployment_dependency": "Deployment+Dependency",
            "ownership_dependency": "Ownership+Dependency", "full": "Full"}
RATES = (0.0, .25, .50, .75, 1.0)
SEEDS = tuple(range(10))
METRICS = [f"top{k}" for k in range(1, 6)] + [f"tie_top{k}" for k in range(1, 6)] + ["avg5", "tie_avg5"]
REPORTED = ["top1", "top3", "top5", "avg5", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def config(groups):
    return RCAConfig(relation_groups=("mapping", *groups), propagation_lambda=.1,
                     direction="bidirectional", edge_normalization="row", metric_policy="all")


def case_graph(c):
    return RuntimeGraph(tuple(RuntimeEntity(**e) for e in c["entities"]),
                        tuple(Relation(**r) for r in c["relations"]), (), c["dataset"], "frozen")


def prepare(c):
    graph = case_graph(c)
    c["ids"] = [e.id for e in graph.entities]
    c["names"] = [e.name for e in graph.entities]
    c["kinds"] = [e.kind for e in graph.entities]
    c["base"] = np.asarray([c["base_scores"][eid] for eid in c["ids"]], dtype=float)
    c["eval_ix"] = np.asarray([i for i, k in enumerate(c["kinds"]) if k == c["level"]], dtype=int)
    c["eval_names"] = [c["names"][i] for i in c["eval_ix"]]
    c["root_ix"] = next(i for i in c["eval_ix"] if c["names"][i] == c["target"])
    c["edges"] = {arm: propagation(graph, config(groups))[0]
                  for arm, groups in (ARMS if c["dataset"] == "lsdb" else AI_ARMS).items()}
    return c


def apply(c, base, arm):
    final, _ = propagate_once(dict(zip(c["ids"], map(float, base))), c["edges"][arm], .1)
    return np.asarray([final[eid] for eid in c["ids"]])


def measure(c, values):
    return _stats(c["eval_names"], values[c["eval_ix"]], c["target"])


def paired_interval(values, *key):
    v = np.asarray(values, dtype=float)
    rng = np.random.default_rng(_metric_seed("followup-case-bootstrap", *key))
    means = v[rng.integers(0, len(v), size=(5000, len(v)))].mean(axis=1)
    return dict(difference_pp=100 * float(v.mean()),
                ci95_low_pp=100 * float(np.quantile(means, .025)),
                ci95_high_pp=100 * float(np.quantile(means, .975)), paired_cases=len(v))


def load_cases():
    cases, sources, checks = [], [], []
    paths = sorted((LS_SOURCE / "details").glob("*.json"), key=lambda p: int(p.stem))
    assert len(paths) == 88
    for path in paths:
        d = json.loads(path.read_text(encoding="utf-8"))
        ev = d["experiments"]["Mapping-only"]["evaluation"]
        c = prepare(dict(dataset="lsdb", case_id=path.stem, level=ev["truth_level"],
                         target=ev["truth_key"], entities=d["entities"], relations=d["relations"],
                         base_scores=d["base_scores"]))
        for arm, name in LS_NAMES.items():
            values = apply(c, c["base"], arm)
            stats = measure(c, values)
            old = d["experiments"][name]
            assert stats["rank"] == int(old["evaluation"]["baro_rank"]), (path.stem, arm)
            reference = np.asarray([old["final_scores"][eid] for eid in c["ids"]])
            max_error = float(np.abs(values - reference).max())
            assert np.allclose(values, reference, rtol=1e-12, atol=1e-8), (path.stem, arm, max_error)
            checks.append(dict(dataset="lsdb", case_id=path.stem, arm=arm,
                               rank_equal=True, max_score_error=max_error))
        cases.append(c); sources.append(path)
    saved = pd.read_csv(ROOT / "output/aiops2025_ownership_pilot/rankings.csv")
    for path in sorted(AI_SOURCE.glob("mapped_case_bases_*.pkl")):
        with path.open("rb") as stream:
            batch = pickle.load(stream)
        sources.append(path)
        for cid, d in batch.items():
            entities = [dict(id=eid, kind=k, name=n) for eid, k, n in zip(d["ids"], d["kinds"], d["entity_names"])]
            relations = [dict(id=f"{kind}:{i}", source=s, target=t, kind=kind,
                              source_ref=f"frozen:{path.name}")
                         for kind, pairs in d["relations"].items() for i, (s, t) in enumerate(pairs)]
            c = prepare(dict(dataset="aiops2025", case_id=cid, level=d["level"], target=d["target"],
                entities=entities, relations=relations, base_scores=dict(zip(d["ids"], map(float, d["base"])))))
            for arm in AI_ARMS:
                values = apply(c, c["base"], arm)
                old = saved[(saved.case_id == cid) & (saved.arm == arm)].set_index("entity")
                got = {c["names"][i]: values[i] for i in c["eval_ix"]}
                assert set(got) == set(old.index)
                max_error = max(abs(got[n] - float(old.loc[n, "score"])) for n in got)
                assert all(np.isclose(got[n], old.loc[n, "score"], rtol=1e-12, atol=1e-8) for n in got)
                stats = measure(c, values)
                assert stats["rank"] == int(old.loc[c["target"], "rank"]), (cid, arm)
                checks.append(dict(dataset="aiops2025", case_id=cid, arm=arm,
                                   rank_equal=True, max_score_error=max_error))
            cases.append(c)
    assert Counter(c["dataset"] for c in cases) == {"lsdb": 88, "aiops2025": 272}
    return cases, sources, checks


def main():
    OUT.mkdir(exist_ok=True)
    protocol = dict(version="nonzero-entity-mask-v1-followup", datasets={"lsdb": 88, "aiops2025": 272},
        structural_controls=["incident_edge_count", "uniform_source_row_propagation"],
        degree_definition="Incident selected original relations; count multiplicity, exclude self-loops; no metrics or labels",
        uniform_source_definition="Unchanged source-row single-step propagation with every entity base=1; no telemetry",
        gradient_dataset="lsdb", relation_arms=ARMS, rates=RATES, seeds=SEEDS,
        mask="Same as completed AIOps2025 experiment: each case/layer/rate/seed independently samples round(rate*n) originally nonzero entity bases uniformly without replacement; zero selected bases; keep graph/candidates fixed",
        seed="SHA256('mask|case_id|layer|fraction|seed'), first 8 bytes big endian, numpy default_rng",
        primary_metrics="Exact-score tie expectation; deterministic name-order results retained for compatibility",
        bootstrap="5000 paired case resamples after averaging 10 mask seeds within case; nominal exploratory intervals without multiplicity correction",
        lambda_value=.1, propagation="unchanged one step, bidirectional, source-row normalization",
        scope="Direct entity evidence masking, not raw-channel missingness, medical-domain validation, or training-data scarcity")
    dump(OUT / "protocol.json", protocol)
    cases, source_paths, validation = load_cases()
    print("Historical score/rank checks passed:", len(validation), flush=True)
    pd.DataFrame(validation).to_csv(OUT / "historical_replay_checks.csv", index=False)

    # Structural priors: no observed anomaly score is read to construct rankings.
    structural = []
    for c in cases:
        arms = ARMS if c["dataset"] == "lsdb" else AI_ARMS
        for arm, groups in arms.items():
            degree = Counter()
            for r in c["relations"]:
                if r["kind"] in groups and r["source"] != r["target"]:
                    degree[r["source"]] += 1; degree[r["target"]] += 1
            raw = np.asarray([degree[eid] for eid in c["ids"]], dtype=float)
            uniform = apply(c, np.ones(len(c["ids"])), arm)
            for control, values in (("incident_edge_count", raw), ("uniform_source_row_propagation", uniform)):
                structural.append(dict(dataset=c["dataset"], case_id=c["case_id"], level=c["level"],
                    arm=arm, control=control, **measure(c, values)))
    structural = pd.DataFrame(structural)
    structural.to_csv(OUT / "structural_prior_cases.csv", index=False)
    summaries = []
    for dataset, data in structural.groupby("dataset"):
        for scope, g in [("all", data), *data.groupby("level")]:
            for (arm, control), sub in g.groupby(["arm", "control"]):
                summaries.append(dict(dataset=dataset, scope=scope, arm=arm, control=control,
                                      cases=len(sub), **{m: float(sub[m].mean()) for m in REPORTED}))
    structural_summary = pd.DataFrame(summaries)
    structural_summary.to_csv(OUT / "structural_prior_summary.csv", index=False)

    # LSDB: 3 masking layers x 5 rates x 10 seeds x 8 arms, all 88 cases.
    gradient = []
    for c in (c for c in cases if c["dataset"] == "lsdb"):
        for layer in ("host", "pod", "service"):
            eligible = np.asarray([i for i, (kind, score) in enumerate(zip(c["kinds"], c["base"]))
                                   if kind == layer and score != 0.0], dtype=int)
            for fraction in RATES:
                for seed in SEEDS:
                    selected = np.array([], dtype=int)
                    if fraction > 0 and len(eligible):
                        nmask = len(eligible) if fraction == 1 else int(round(fraction * len(eligible)))
                        rng = np.random.default_rng(_metric_seed("mask", c["case_id"], layer, fraction, seed))
                        selected = np.sort(rng.choice(eligible, size=nmask, replace=False))
                    base = c["base"].copy(); base[selected] = 0.0
                    for arm in ARMS:
                        values = apply(c, base, arm)
                        gradient.append(dict(case_id=c["case_id"], level=c["level"], layer_masked=layer,
                            fraction=fraction, seed=seed, arm=arm, eligible_count=len(eligible),
                            masked_count=len(selected), root_direct_masked=bool(c["root_ix"] in selected),
                            **measure(c, values)))
    gradient = pd.DataFrame(gradient)
    assert len(gradient) == 88 * 3 * 5 * 10 * 8
    assert not gradient.duplicated(["case_id", "layer_masked", "fraction", "seed", "arm"]).any()
    gradient.to_csv(OUT / "lsdb_observation_gradient_cases.csv", index=False)
    gcase = gradient.groupby(["case_id", "level", "layer_masked", "fraction", "arm"], as_index=False)[METRICS].mean()
    gs = gcase.groupby(["level", "layer_masked", "fraction", "arm"], as_index=False)[REPORTED].mean()
    gs["cases"] = gs.level.map({"host": 12, "pod": 26, "service": 50})
    gs.to_csv(OUT / "lsdb_observation_gradient_summary.csv", index=False)
    gradient_pairs = []
    for (level, layer, fraction), g in gcase[gcase.level == gcase.layer_masked].groupby(["level", "layer_masked", "fraction"]):
        for arm in ARMS:
            if arm == "mapping": continue
            for metric in REPORTED:
                p = g.pivot(index="case_id", columns="arm", values=metric)
                gradient_pairs.append(dict(level=level, layer_masked=layer, fraction=fraction,
                    comparison=f"{arm}-mapping", metric=metric,
                    **paired_interval(p[arm] - p.mapping, "lsdb-gradient", level, fraction, arm, metric)))
    # Incremental Dependency effect while the containment scaffold is fixed.
    for (level, layer, fraction), g in gcase[gcase.level == gcase.layer_masked].groupby(["level", "layer_masked", "fraction"]):
        for left, right in (("full", "containment"), ("ownership_dependency", "ownership")):
            for metric in REPORTED:
                p = g.pivot(index="case_id", columns="arm", values=metric)
                gradient_pairs.append(dict(level=level, layer_masked=layer, fraction=fraction,
                    comparison=f"{left}-{right}", metric=metric,
                    **paired_interval(p[left] - p[right], "lsdb-dependency-increment", level, fraction, left, right, metric)))
    pd.DataFrame(gradient_pairs).to_csv(OUT / "lsdb_observation_gradient_paired_ci.csv", index=False)

    # Compare propagation against structure-only priors under exactly the same
    # candidate/label contract. Prior rankings do not change with score masks.
    ai = pd.read_csv(AI_SOURCE / "observation_gradient_cases.csv")
    ai = ai.groupby(["case_id", "level", "layer_masked", "fraction", "arm"], as_index=False)[METRICS].mean()
    all_gradients = pd.concat([gcase.assign(dataset="lsdb"), ai.assign(dataset="aiops2025")], ignore_index=True)
    comparisons = []
    for dataset, data in all_gradients[all_gradients.level == all_gradients.layer_masked].groupby("dataset"):
        for (level, fraction, arm), g in data.groupby(["level", "fraction", "arm"]):
            for control in ("incident_edge_count", "uniform_source_row_propagation"):
                prior = structural[(structural.dataset == dataset) & (structural.level == level) &
                                   (structural.arm == arm) & (structural.control == control)]
                paired = g.merge(prior, on=["case_id", "level", "arm"], suffixes=("_prop", "_prior"), validate="one_to_one")
                assert len(paired) == len(g)
                for metric in REPORTED:
                    delta = paired[metric + "_prop"] - paired[metric + "_prior"]
                    comparisons.append(dict(dataset=dataset, level=level, fraction=fraction,
                        arm=arm, control=control, metric=metric,
                        propagation_mean=float(paired[metric + "_prop"].mean()),
                        prior_mean=float(paired[metric + "_prior"].mean()),
                        **paired_interval(delta, "structure-comparison", dataset, level, fraction, arm, control, metric)))
    comparisons = pd.DataFrame(comparisons)
    comparisons.to_csv(OUT / "propagation_vs_structural_prior.csv", index=False)

    code_paths = [Path(__file__), ROOT / "tools/evaluate_aiops2025_observability_and_rewire.py",
                  *[ROOT / "src/ls_baro" / n for n in ("canonical_core.py", "topology.py", "scoring.py")]]
    sources = {str(p): sha(p) for p in [*source_paths, *code_paths, AI_SOURCE / "observation_gradient_cases.csv"]}
    verification = dict(complete=True, cases={"lsdb": 88, "aiops2025": 272},
        historical_checks=len(validation), all_historical_ranks_equal=True,
        max_historical_score_error=max(r["max_score_error"] for r in validation),
        lsdb_gradient_rows=len(gradient), structural_prior_rows=len(structural),
        source_and_code_sha256=sources,
        root_labels_used_for="evaluation only; not masks, structural scores, or relation construction",
        note="LSDB Host bases already zero; masking Host adds no further evidence loss")
    dump(OUT / "verification.json", verification)

    lines = ["# Structural priors and LSDB observation gradients", "",
        "Frozen source scores, graph candidates, one-step bidirectional propagation, row normalization, lambda=0.1.",
        "Masking matches the completed AIOps2025 nonzero-entity protocol, with 10 seeds and 5 rates. Host bases in LSDB already equal zero.",
        "Primary interpretation uses exact-tie expected metrics; deterministic scores remain in CSV. Bootstrap intervals are nominal exploratory case-paired intervals.", "",
        "## Structure-only priors", "",
        "Incident degree reads only selected graph relations. Uniform-source control runs unchanged propagation with all entity source scores equal to one. Neither control reads anomaly metrics or root labels.", "",
        "| Dataset | Scope | Arm | Control | N | Tie Top-1 | Tie Top-3 | Tie Top-5 | Tie Avg@5 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for r in structural_summary.itertuples():
        if r.scope == "all" or r.arm == "mapping": continue
        lines.append(f"| {r.dataset} | {r.scope} | {r.arm} | {r.control} | {r.cases} | {r.tie_top1:.2%} | {r.tie_top3:.2%} | {r.tie_top5:.2%} | {r.tie_avg5:.4f} |")
    lines += ["", "## LSDB: Service root, Service direct-evidence gradient", "",
        "This is the root layer directly connected by Dependency; all rates and all arms are reported.", "",
        "| Masked fraction | Arm | N | Tie Top-1 | Tie Top-3 | Tie Top-5 | Tie Avg@5 |",
        "|---:|---|---:|---:|---:|---:|---:|"]
    for fraction in RATES:
        for arm in ARMS:
            r = gs[(gs.level == "service") & (gs.layer_masked == "service") &
                   (gs.fraction == fraction) & (gs.arm == arm)].iloc[0]
            lines.append(f"| {fraction:.0%} | {arm} | {r.cases} | {r.tie_top1:.2%} | {r.tie_top3:.2%} | {r.tie_top5:.2%} | {r.tie_avg5:.4f} |")
    lines += ["", "## Propagation minus structural prior", "",
        "Full tables include every layer, fraction, arm, metric and both priors. Selected checks below do not replace the complete results.", "",
        "| Dataset | Root layer | Mask | Arm | Prior | Metric | Propagation | Prior | Difference pp | Case 95% interval pp |",
        "|---|---|---:|---|---|---|---:|---:|---:|---|"]
    selected = comparisons[(comparisons.control == "incident_edge_count") &
        (((comparisons.dataset == "aiops2025") & (comparisons.level == "host") &
          (comparisons.arm == "deployment") & comparisons.fraction.isin([0, .5, 1])) |
         ((comparisons.dataset == "lsdb") & (comparisons.level == "host") &
          (comparisons.arm == "deployment") & (comparisons.fraction == 0)) |
         ((comparisons.dataset == "lsdb") & comparisons.level.isin(["pod", "service"]) &
          (comparisons.arm == "ownership") & (comparisons.fraction == 1))) &
        comparisons.metric.isin(["tie_top1", "tie_top3", "tie_top5", "tie_avg5"])]
    for r in selected.itertuples():
        lines.append(f"| {r.dataset} | {r.level} | {r.fraction:.0%} | {r.arm} | {r.control} | {r.metric} | {r.propagation_mean:.4f} | {r.prior_mean:.4f} | {r.difference_pp:.2f} | [{r.ci95_low_pp:.2f}, {r.ci95_high_pp:.2f}] |")
    lines += ["", "## Interpretation limits", "",
        "These controls test direct mapped-entity evidence loss and graph structural priors. They do not demonstrate medical-domain accuracy, raw telemetry loss realism, causal fault propagation, or universal superiority of any relation.",
        "Known native fault layer restricts evaluation candidates. Roots and their parent/child attributions are not counted as multiple true causes.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("COMPLETED", OUT, flush=True)
    print(selected[["dataset", "fraction", "metric", "propagation_mean", "prior_mean", "difference_pp", "ci95_low_pp", "ci95_high_pp"]].to_string(index=False))
    print("LSDB Service roots, Service mask: Mapping versus Dependency")
    print(gs[(gs.level == "service") & (gs.layer_masked == "service") &
             gs.arm.isin(["mapping", "dependency"])][["fraction", "arm", "tie_top1", "tie_top3", "tie_top5", "tie_avg5"]].to_string(index=False))


if __name__ == "__main__":
    main()
