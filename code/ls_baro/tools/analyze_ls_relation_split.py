"""Summarize the split ablation and verify its normalization mechanism."""
import argparse
import csv
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "output/lsdb_relation_split_l0p1")
    args = parser.parse_args()
    output = args.output
    with (output / "relation_summary.csv").open(encoding="utf-8-sig", newline="") as stream:
        summary = {(r["experiment"], r["truth_level"]): r for r in csv.DictReader(stream)}
    ratios, compared, max_error = set(), 0, 0.0
    examples = []
    for path in sorted((output / "details").glob("*.json"), key=lambda p: int(p.stem)):
        data = json.loads(path.read_text(encoding="utf-8"))
        own, contain = (data["experiments"][arm] for arm in ("Ownership", "Containment"))
        edges = {(r["from"], r["to"], r["relation_id"]): r for r in own["contributions"]}
        for row in contain["contributions"]:
            if row["relation_kind"] == "ownership" and row["to"].startswith("lsdb:service:"):
                reference = edges[(row["from"], row["to"], row["relation_id"])]
                ratios.add(row["weight"] / reference["weight"])
                compared += 1
        for entity in data["entities"]:
            if entity["kind"] != "service":
                continue
            key = entity["id"]
            predicted = data["base_scores"][key] + 0.5 * (own["final_scores"][key] - data["base_scores"][key])
            actual = contain["final_scores"][key]
            max_error = max(max_error, abs(predicted - actual))
            if not math.isclose(predicted, actual, rel_tol=1e-12, abs_tol=1e-10):
                raise AssertionError(f"case {path.stem}: Service score is not half-strength ownership")
        a, b = own["evaluation"], contain["evaluation"]
        if a["truth_level"] == "service" and a["baro_rank"] != b["baro_rank"]:
            examples.append({"case_id": path.stem, "root": a["truth_key"],
                             "ownership_rank": a["baro_rank"], "containment_rank": b["baro_rank"],
                             "ownership_delta": a["root_ownership_delta"],
                             "containment_delta": b["root_ownership_delta"]})
    if ratios != {0.5}:
        raise AssertionError(f"Unexpected normalization ratios: {ratios}")
    mechanism = {"pod_to_service_edges_compared": compared, "ownership_weight_ratios": sorted(ratios),
                 "service_scores_equal_base_plus_half_ownership_increment": True,
                 "max_absolute_reconstruction_error": max_error, "rank_change_examples": examples,
                 "interpretation": "For these LSDB graphs, adding deployment halves Pod-to-Service ownership weights; single-hop deployment cannot pass Host evidence on to Service."}
    (output / "normalization_audit.json").write_text(json.dumps(mechanism, indent=2), encoding="utf-8")
    percent = lambda arm, layer, field: 100 * float(summary[(arm, layer)][field])
    note = ["# Containment 内部贡献分析", "",
            "LSDB 全部 88 个案例；固定 λ=0.1、双向、按源节点归一化、一次传播。以下层内 Top-N 使用确定性排序，与论文总体表格一致。", "",
            f"1. Deployment 的层内 Top-1 收益集中在 Host：{percent('Mapping-only','host','baro_top1'):.2f}% → {percent('Deployment','host','baro_top1'):.2f}%。Pod 与 Service 的层内排名未变。Host 为 12 例，不能据此声称所有系统均有同等收益。",
            f"2. Ownership 改善 Pod 与 Service 的候选覆盖。Pod Top-3：{percent('Mapping-only','pod','baro_top3'):.2f}% → {percent('Ownership','pod','baro_top3'):.2f}%；Top-5：{percent('Mapping-only','pod','baro_top5'):.2f}% → {percent('Ownership','pod','baro_top5'):.2f}%。Service Top-3：{percent('Mapping-only','service','baro_top3'):.2f}% → {percent('Ownership','service','baro_top3'):.2f}%；Top-5：{percent('Mapping-only','service','baro_top5'):.2f}% → {percent('Ownership','service','baro_top5'):.2f}%。两层 Top-1 均未净增加。",
            f"3. 组合不是逐项最优。Deployment 总体 Top-1 为 {percent('Deployment','all','baro_top1'):.2f}%，高于 Containment 的 {percent('Containment','all','baro_top1'):.2f}%；Ownership 总体 Top-5 为 {percent('Ownership','all','baro_top5'):.2f}%，高于 Containment 的 {percent('Containment','all','baro_top5'):.2f}%。Containment 的总体 Top-3 与 Avg@5 在八组中并列最高。", "",
            "## 归一化机制的直接核查", "",
            f"核对 {compared} 条 Pod→Service ownership 传播边，加入 deployment 后，原 ownership 权重均为单独使用 ownership 时的 1/2。",
            f"全部 Service 的 Containment 分数均可用『基础分 + 1/2 × Ownership 增量』复原；最大绝对误差 {max_error:.3g}，属于浮点计算误差。",
            "因此，部分 Service 排名差异可直接归因于已有 ownership 证据权重的重分配；不应描述成 Host 异常接力传播到 Service，也不能仅归因于新增症状噪声。", "",
            "| 案例 | Service 根因 | Ownership 排名 | Containment 排名 | Ownership 增量 | Containment 增量 |",
            "|---|---|---:|---:|---:|---:|"]
    for row in examples:
        note.append(f"| {row['case_id']} | {row['root']} | {row['ownership_rank']} | {row['containment_rank']} | {row['ownership_delta']:.4f} | {row['containment_delta']:.4f} |")
    note.extend(["", "## 论文可用表述", "",
                 "进一步拆分跨层包含关系后，deployment 与 ownership 表现出不同的分层贡献：前者主要改善 Host 根因的首位定位，后者主要提高 Pod 与 Service 根因的 Top-3/Top-5 覆盖率。两者组合获得更高的总体 Top-3 与 Avg@5，但并未在所有评价指标上优于单独关系。逐边核查表明，组合时按源节点归一化会重新分配既有传播权重，因而关系贡献既受到实体层级影响，也受到传播配置的制约。上述发现支持分别评价不同语义关系，并限制在所考察数据和单次传播机制下解释其收益。", "",
                 "原有分层表部分字段使用并列分摊口径；本文件采用确定性 Top-N。两种口径均保留在 relation_summary.csv，不能混用。Global Top-1 保持 10.23%；37.50% → 55.68% 对应 Global Top-5。"])
    (output / "INTERPRETATION.md").write_text("\n".join(note) + "\n", encoding="utf-8")
    plot(output, summary)
    print(json.dumps(mechanism, ensure_ascii=False, indent=2))


def plot(output, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    arms = ["Mapping-only", "Deployment", "Ownership", "Containment"]
    colors = ["#8a939e", "#3274a1", "#dd8452", "#55a868"]
    panels = [("Host", "host", "baro_top1", "Top-1"),
              ("Pod", "pod", "baro_top5", "Top-5"),
              ("Service", "service", "baro_top5", "Top-5")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.1), sharey=True)
    for axis, (title, layer, field, metric) in zip(axes, panels):
        values = [100 * float(summary[(arm, layer)][field]) for arm in arms]
        axis.bar(np.arange(4), values, color=colors, width=0.65)
        for i, value in enumerate(values):
            axis.text(i, value + 1.3, f"{value:.1f}", ha="center", fontsize=9)
        axis.set_title(f"{title} roots (n={summary[(arms[0], layer)]['cases']}): {metric}", fontsize=11)
        axis.set_xticks(range(4), ["Mapping", "Deploy.", "Owner.", "Both"], fontsize=9)
        axis.set_ylim(0, 109)
        axis.set_yticks(range(0, 101, 20))
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Known-root-layer hit rate (%)")
    fig.suptitle("LSDB: deployment and ownership contribute at different entity layers", fontsize=12)
    fig.text(0.5, 0.02, "Fixed one-hop policy: lambda=0.1, bidirectional, source normalization; deterministic Top-N.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.055, 1, 0.94))
    fig.savefig(output / "relation_split_by_layer.png", dpi=200)
    fig.savefig(output / "relation_split_by_layer.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
