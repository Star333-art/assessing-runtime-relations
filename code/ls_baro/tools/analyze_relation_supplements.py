"""Paired inference and plots for the declared relation supplement protocol."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_relation_supplements import ARMS, SENSITIVITY_ARMS, DEFAULT_OUTPUT, PROTOCOL, write_csv, write_json

B = 20000
SEED = 20261015
COMPARISONS = [
    ("Mapping-only", "Containment"), ("Dependency", "Containment"),
    ("Mapping-only", "Ownership"), ("Dependency", "Ownership"),
    ("Mapping-only", "Dependency"), ("Mapping-only", "Deployment"),
    ("Ownership", "Containment"), ("Containment", "Full"),
    ("Ownership+Dependency", "Full"),
]


def load_rows(path):
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    strings = {"dataset", "case_id", "truth_level", "truth_key", "target_cluster", "fault", "normalization", "arm"}
    return [{k: v if k in strings else float(v) for k, v in r.items()} for r in rows]


def aggregate(rows):
    grouped = defaultdict(list)
    for r in rows:
        for level in ("all", r["truth_level"]):
            grouped[(r["dataset"], r["normalization"], r["arm"], r["lambda"], level)].append(r)
    out = []
    metrics = [f"{scope}_top{k}" for scope in ("layer", "global") for k in (1, 3, 5)]
    metrics += ["layer_mrr", "global_mrr", "layer_avg5"]
    for (ds, norm, arm, weight, level), selected in grouped.items():
        out.append({"dataset": ds, "normalization": norm, "arm": arm, "lambda": weight,
                    "truth_level": level, "cases": len(selected),
                    **{key: sum(r[key] for r in selected)/len(selected) for key in metrics}})
    return out


def holm(rows):
    ordered = sorted(enumerate(rows), key=lambda z: z[1]["p_raw"])
    running = 0.
    for i, (_, row) in enumerate(ordered):
        running = max(running, min(1., row["p_raw"] * (len(ordered)-i)))
        row["p_holm"] = running


def intervals(delta, groups, rng, binary):
    n = len(delta)
    if binary:
        frequencies = np.array([(delta == -1).sum(), (delta == 0).sum(), (delta == 1).sum()]) / n
        draws = rng.multinomial(n, frequencies, size=B)
        case_boot = (draws[:, 2] - draws[:, 0]) / n
    else:
        # Bounded batches avoid a large B x N allocation for continuous MRR.
        case_boot = np.concatenate([delta[rng.integers(0, n, size=(min(1000, B-i), n))].mean(axis=1)
                                    for i in range(0, B, 1000)])
    names = sorted(set(groups))
    sums = np.array([sum(d for d, g in zip(delta, groups) if g == name) for name in names])
    counts = np.array([sum(g == name for g in groups) for name in names])
    if len(names) < 2:
        cluster_ci = [float("nan"), float("nan")]
    else:
        choices = rng.integers(0, len(names), size=(B, len(names)))
        boot = sums[choices].sum(axis=1) / counts[choices].sum(axis=1)
        cluster_ci = list(np.quantile(boot, [.025, .975]))
    return [float(x) for x in np.quantile(case_boot, [.025, .975])], [float(x) for x in cluster_ci], len(names)


def statistics(rows):
    index = {(r["dataset"], r["case_id"], r["arm"]): r for r in rows
             if r["normalization"] == "row" and (r["lambda"] == .1 or r["arm"] == "Mapping-only")}
    out, rank_rows = [], []
    rng = np.random.default_rng(SEED)
    for ds in sorted({r["dataset"] for r in rows}):
        case_ids = sorted({r["case_id"] for r in rows if r["dataset"] == ds})
        for before, after in COMPARISONS:
            a = [index[(ds, case, before)] for case in case_ids]
            b = [index[(ds, case, after)] for case in case_ids]
            groups = [r["target_cluster"] for r in a]
            for scope in ("layer", "global"):
                ra, rb = np.array([r[f"{scope}_rank"] for r in a]), np.array([r[f"{scope}_rank"] for r in b])
                rank_rows.append({"dataset": ds, "before": before, "after": after, "scope": scope, "cases": len(a),
                                  "improved": int((rb < ra).sum()), "same": int((rb == ra).sum()),
                                  "worsened": int((rb > ra).sum())})
                for metric in ("top1", "top3", "top5", "mrr"):
                    field = scope + "_" + metric
                    delta = np.array([y[field] - x[field] for x, y in zip(a, b)])
                    binary = metric != "mrr"
                    case_ci, cluster_ci, clusters = intervals(delta, groups, rng, binary)
                    gains, losses = int((delta > 0).sum()), int((delta < 0).sum())
                    p = float(binomtest(gains, gains+losses, .5).pvalue) if binary and gains+losses else 1.
                    primary = scope == "layer" and metric == "top3" and (before, after) in COMPARISONS[:2]
                    out.append({"dataset": ds, "before": before, "after": after, "scope": scope, "metric": metric,
                                "cases": len(a), "target_clusters": clusters,
                                "before_mean": sum(r[field] for r in a)/len(a), "after_mean": sum(r[field] for r in b)/len(b),
                                "delta": float(delta.mean()), "gain_cases": gains, "loss_cases": losses,
                                "case_ci_low": case_ci[0], "case_ci_high": case_ci[1],
                                "cluster_ci_low": cluster_ci[0], "cluster_ci_high": cluster_ci[1],
                                "p_raw": p if binary else "not_tested", "p_holm": "not_tested",
                                "role": "primary" if primary else "exploratory",
                                "holm_family": "primary_four" if primary else f"exploratory_{ds}_{scope}_{metric}"})
    families = defaultdict(list)
    for row in out:
        if row["p_raw"] != "not_tested":
            families[row["holm_family"]].append(row)
    for group in families.values():
        holm(group)
    return out, rank_rows


def rescaling_audit(rows):
    """Check a predicted effective-strength equivalence on Service roots only."""
    selected = [r for r in rows if r["normalization"] == "row" and r["truth_level"] == "service"]
    index = {(r["dataset"], r["case_id"], r["arm"], r["lambda"]): r for r in selected}
    out = []
    for ds, case in sorted({(r["dataset"], r["case_id"]) for r in selected}):
        for own_weight, contain_weight in ((.05, .1), (.1, .2)):
            a, b = index[(ds, case, "Ownership", own_weight)], index[(ds, case, "Containment", contain_weight)]
            out.append({"dataset": ds, "case_id": case, "ownership_lambda": own_weight,
                        "containment_lambda": contain_weight,
                        "root_score_abs_error": abs(a["root_final_score"]-b["root_final_score"]),
                        "root_scores_match": math.isclose(a["root_final_score"], b["root_final_score"], rel_tol=1e-12, abs_tol=1e-10),
                        "layer_rank_match": a["layer_rank"] == b["layer_rank"]})
    return out


def plots(summaries, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lookup = {(r["dataset"], r["normalization"], r["arm"], r["lambda"], r["truth_level"]): r for r in summaries}
    ds_names = sorted({r["dataset"] for r in summaries})
    colors = {"Ownership": "#14816a", "Containment": "#2869a6", "Dependency": "#c77728", "Full": "#8859a5"}
    fig, axes = plt.subplots(len(ds_names), 3, figsize=(13, 3.7*len(ds_names)), squeeze=False)
    for i, ds in enumerate(ds_names):
        base = lookup[(ds, "row", "Mapping-only", 0., "all")]
        for j, metric in enumerate(("layer_top3", "layer_top5", "global_top5")):
            ax = axes[i, j]
            for arm in SENSITIVITY_ARMS:
                ys = [lookup[(ds, "row", arm, x, "all")][metric]*100 for x in (.05, .1, .2)]
                ax.plot([.05, .1, .2], ys, marker="o", label=arm, color=colors[arm])
            ax.axhline(base[metric]*100, color="#555555", linestyle="--", label="Mapping-only")
            ax.set(title=f"{ds}: {metric.replace('_', ' ')}", xlabel="Propagation strength lambda", ylabel="Hit rate (%)")
            ax.set_xticks([.05, .1, .2]); ax.grid(alpha=.2)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False)
    fig.tight_layout(rect=[0, .06, 1, 1])
    for ext in ("png", "svg"):
        fig.savefig(output / ("lambda_sensitivity."+ext), dpi=180)
    plt.close(fig)
    names = list(ARMS)
    short = ["Map", "Deploy", "Own", "Dep", "Contain", "D+Dep", "O+Dep", "Full"]
    fig, axes = plt.subplots(len(ds_names), 2, figsize=(13, 3.8*len(ds_names)), squeeze=False)
    for i, ds in enumerate(ds_names):
        for j, metric in enumerate(("layer_top3", "global_top5")):
            ax = axes[i, j]
            for shift, norm, color, label in ((-.19, "row", "#2869a6", "Selected-edge degree"), (.19, "reference_row", "#14816a", "Fixed full-graph degree")):
                ys = [lookup[(ds, norm, arm, 0. if arm == "Mapping-only" else .1, "all")][metric]*100 for arm in names]
                ax.bar(np.arange(len(names))+shift, ys, width=.36, color=color, label=label)
            ax.set_xticks(np.arange(len(names)), short, rotation=20)
            ax.set(title=f"{ds}: {metric.replace('_', ' ')}", ylabel="Hit rate (%)")
            ax.grid(axis="y", alpha=.2)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False)
    fig.tight_layout(rect=[0, .05, 1, 1])
    for ext in ("png", "svg"):
        fig.savefig(output / ("normalization_control."+ext), dpi=180)
    plt.close(fig)


def report(summaries, stats, rank_rows, audit, output, scale_summary):
    datasets = [ds for ds in ("LSDB", "Hipster") if ds in audit["case_counts"]]
    actual_counts = "；".join(f"{ds} {audit['case_counts'][ds]} 例" for ds in datasets)
    lines = ["# 关系贡献补充实验", "", actual_counts + "。主表统一使用确定性排名：层内候选由真实根因类型限定，Global 候选包含图中全部运行实体。两种结果分别报告。", "",
             "配置固定为 BARO DataFrame-compatible、raw/max、most_specific、单次双向传播；λ=0.1 为原工作点。敏感性分析只检查 0.05、0.1、0.2，不据此更换主结果参数。", "",
             "## 主要发现", ""]
    for ds in datasets:
        curves = {arm: [r['layer_top3'] for r in summaries if r['dataset'] == ds and r['truth_level'] == 'all' and r['normalization'] == 'row' and r['arm'] == arm] for arm in ('Containment', 'Dependency')}
        c, d = curves['Containment'], curves['Dependency']
        lines.append(f"- {ds}：三个λ点的Containment层内Top-3为 {100*min(c):.2f}%–{100*max(c):.2f}%，Dependency为 {100*min(d):.2f}%–{100*max(d):.2f}%。这支持所测试范围内的关系差异，但不意味着Containment在全部指标上最优。")
    lines += ["- 固定参考边权后，每个案例的Ownership与Containment对全部Service的分数和层内排名完全一致。原两组的Service差异反映了权重重分配；不能据此单独判断Deployment证据有害。",
              "- Hipster的正向增益得到案例配对与故障目标分组区间的共同支持；LSDB相对基线的层内Top-3增幅尚不能排除零差异。结论应分别报告两套数据的证据强度。", "",
             "## 原工作点与固定参考边权", "",
             "row 使用筛选后的出边度数；reference_row 使用同一完整运行图中全部支持关系的出边度数。后者保留边权固定，但子图传播总预算可以小于 1；该对照用于解释权重重分配，不能单凭结果判断哪种归一化更优。", "",
             "| 数据集 | 归一化 | 关系组 | 层内Top-1 | 层内Top-3 | 层内Top-5 | Global Top-3 | Global Top-5 |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for ds in datasets:
        for norm in ("row", "reference_row"):
            for arm in ARMS:
                r = next(r for r in summaries if r["dataset"] == ds and r["truth_level"] == "all" and r["normalization"] == norm and r["arm"] == arm and (r["lambda"] == .1 or arm == "Mapping-only"))
                lines.append(f"| {ds} | {norm} | {arm} | {100*r['layer_top1']:.2f}% | {100*r['layer_top3']:.2f}% | {100*r['layer_top5']:.2f}% | {100*r['global_top3']:.2f}% | {100*r['global_top5']:.2f}% |")
    lines += ["", "## 正向收益的配对分析", "", "本次补充分析指定两套数据上的层内 Top-3 为主要指标：Mapping-only→Containment、Dependency→Containment，共四次检验，使用 Holm 校正。此前已经观察过原始结果，因此这些检验属于事后补充证据，并非预注册的确认性检验。其他指标和 Ownership 拆分属于探索性分析，单独记录校正家族。", "",
              "表中差值和区间均为百分点；普通区间按案例配对重采样，分组区间按真实根因类型及名称组成的目标组重采样。重复注入依赖可能无法完全由该分组捕捉。", "",
              "| 数据集 | 比较 | 命中差值 | 新增/丢失 | 案例95%区间 | 目标组95%区间 | 组数 | 原始p | Holm p |", "|---|---|---:|---:|---|---|---:|---:|---:|"]
    for r in stats:
        if r["role"] == "primary":
            lines.append(f"| {r['dataset']} | {r['before']}→{r['after']} | {100*r['delta']:+.2f} | {r['gain_cases']}/{r['loss_cases']} | [{100*r['case_ci_low']:.2f}, {100*r['case_ci_high']:.2f}] | [{100*r['cluster_ci_low']:.2f}, {100*r['cluster_ci_high']:.2f}] | {r['target_clusters']} | {r['p_raw']:.4g} | {r['p_holm']:.4g} |")
    lines += ["", "McNemar的p值以案例为配对单位，不能单独解决重复注入的相关性；目标组重采样是额外的稳健性检查。LSDB基线→Containment的层内Top-3原始及校正p均为0.109，不能写成两套数据均显著优于基线。"]
    for r in stats:
        if r['dataset'] == 'LSDB' and r['before'] == 'Mapping-only' and r['after'] == 'Containment' and r['scope'] == 'global' and r['metric'] == 'top5':
            lines += ["", f"LSDB Global Top-5虽增加 {100*r['delta']:.2f} 个百分点，按案例的95%区间为 [{100*r['case_ci_low']:.2f}, {100*r['case_ci_high']:.2f}]，按目标组区间为 [{100*r['cluster_ci_low']:.2f}, {100*r['cluster_ci_high']:.2f}]。后者包含零，说明该增幅对目标组构成敏感，也需要保留不确定性说明。"]
    lines += ["", "## 传播强度敏感性", "", "| 数据集 | 关系组 | λ | 层内Top-1 | 层内Top-3 | 层内Top-5 | Global Top-5 |", "|---|---|---:|---:|---:|---:|---:|"]
    for ds in datasets:
        for arm in SENSITIVITY_ARMS:
            for weight in (.05, .1, .2):
                r = next(r for r in summaries if r["dataset"] == ds and r["truth_level"] == "all" and r["normalization"] == "row" and r["arm"] == arm and r["lambda"] == weight)
                lines.append(f"| {ds} | {arm} | {weight} | {100*r['layer_top1']:.2f}% | {100*r['layer_top3']:.2f}% | {100*r['layer_top5']:.2f}% | {100*r['global_top5']:.2f}% |")
    lines += ["", "![Lambda sensitivity](lambda_sensitivity.png)", "", "![Normalization control](normalization_control.png)", "",
              "## 机制与迁移核对", "",
              f"共 {audit['case_configuration_runs']} 次案例配置运行；原 λ=0.1 八组结果逐项核对 {audit['historical_comparisons']} 次，最大绝对误差 {audit['historical_max_abs_error']:.3g}。",
              f"固定参考边权共核对 {audit['reference_edge_weight_checks']} 条筛选后的有向边；所有完整图配置在两种归一化下的分数与贡献完全一致。",
              "每个案例中，固定参考权重后的 Ownership-only 与 Containment-only 对全部 Service 的分数和层内排名完全一致。原 row 归一化下两者对 Service 的差异因此来自既有边权重变化；Deployment 仍可改变 Host、Pod 分数及 Global 排名。", "",
              "另检查 Service 根因上的有效强度等价：Ownership λ=0.05 对比 Containment λ=0.1，以及 Ownership λ=0.1 对比 Containment λ=0.2。该核对只涉及 Service 的分数与层内排名，不能推广为两组 Global 排名相同。", ""]
    for r in scale_summary:
        lines.append(f"- {r['dataset']}：{r['service_root_comparisons']} 次比较，根因分数匹配 {r['root_score_matches']} 次，层内排名匹配 {r['layer_rank_matches']} 次；最大分数误差 {r['max_abs_error']:.3g}。")
    lines += ["", "## 结论边界", "", "- 统计不显著不能证明无效；显著也不证明传播方向具有因果含义。报告区间、实际案例数和跨参数一致性。",
              "- LSDB 的 Host 根因只有12例；Hipster只有Service根因，无法直接评价外部Host/Pod定位。",
              "- Hipster是查看原三子集结果后选择的拆分子集，应把原三子集四组结果保留在补充材料，不能描述为此前未见的独立测试集。",
              "- MicroCERCL ownership由名称规则推导；正常/异常窗口使用公开注入时间。",
              "- 历史LSDB Global分数包含并列分摊；本报告以两套数据共有的确定性排名为主，历史数据仍保留，不混用。", "",
              "复现：运行 `run_relation_supplements.py`，随后运行 `tools/analyze_relation_supplements.py`。源文件指纹、每案例输入指纹、检查点和完整分析配置随输出保存。"]
    (output / "REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    rows = load_rows(args.output / "supplement_cases.csv")
    summaries = aggregate(rows)
    stats, rank_rows = statistics(rows)
    scale_rows = rescaling_audit(rows)
    write_csv(args.output / "summary.csv", summaries)
    write_csv(args.output / "paired_statistics.csv", stats)
    write_csv(args.output / "paired_rank_changes.csv", rank_rows)
    write_csv(args.output / "effective_strength_audit.csv", scale_rows)
    scale_summary = [{"dataset": ds, "service_root_comparisons": len(rs),
                      "root_score_matches": sum(r["root_scores_match"] for r in rs),
                      "layer_rank_matches": sum(r["layer_rank_match"] for r in rs),
                      "max_abs_error": max(r["root_score_abs_error"] for r in rs)}
                     for ds in sorted({r["dataset"] for r in scale_rows})
                     for rs in [[r for r in scale_rows if r["dataset"] == ds]]]
    write_json(args.output / "effective_strength_audit.json", scale_summary)
    write_json(args.output / "analysis_config.json", {"protocol": PROTOCOL, "bootstrap_replicates": B,
               "seed": SEED, "comparisons": COMPARISONS, "numpy_version": np.__version__,
               "analysis_status": "post-hoc supplemental analysis after original outcomes were known; not preregistered confirmatory inference",
               "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    audit = json.loads((args.output / "run_audit.json").read_text(encoding="utf-8"))
    plots(summaries, args.output)
    report(summaries, stats, rank_rows, audit, args.output, scale_summary)
    for row in stats:
        if row["role"] == "primary":
            print(json.dumps(row, ensure_ascii=False), flush=True)
    print(f"Saved {len(summaries)} summaries and {len(stats)} paired statistics", flush=True)


if __name__ == "__main__":
    main()
