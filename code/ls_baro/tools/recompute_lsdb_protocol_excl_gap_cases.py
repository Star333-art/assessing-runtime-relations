"""Re-summarize the AIOps2025 LSDB-protocol single-entity evaluation after
excluding the three cases whose host->pod (deployment) coverage is incomplete.

This does NOT re-score anything: it re-aggregates the already frozen per-case,
per-arm ranks (case_results.csv) over the reduced case set, using the exact
same metrics and arm order as summarize_aiops2025_lsdb_protocol.py:

  arms    : mapping, deployment, ownership, containment
  metric  : Top-1..Top-5 hit rates + Avg@5 (mean of Top-1..Top-5)
  rank    : deterministic best rank among annotated targets at the native layer

Excluded cases (host->pod coverage gaps; ambiguous_pods == 0, so these are pure
observation gaps, not ambiguity omissions):

  29f88351-232  host    33 pods,  0 deployment edges  (33 missing)
  0718e0f9-92   service 35 pods, 33 deployment edges  ( 2 missing)
  d32bcd36-104  service 35 pods, 34 deployment edges  ( 1 missing)

The original 272-case result is preserved; this is a robustness/sensitivity
check, not a replacement.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/aiops2025_lsdb_protocol_20261023"
ARMS = ["mapping", "deployment", "ownership", "containment"]
EXCLUDED = {"29f88351-232", "0718e0f9-92", "d32bcd36-104"}


def summarize(df: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Identical metric definitions to summarize_aiops2025_lsdb_protocol.py."""
    rows = []
    for arm in ARMS:
        g = df[df.arm == arm]
        assert len(g) == g.case_id.nunique()
        row = {"scope": scope, "arm": arm, "cases": len(g)}
        for k in range(1, 6):
            row[f"top{k}_hits"] = int((g["rank"] <= k).sum())
            row[f"top{k}"] = float((g["rank"] <= k).mean())
        row["avg5"] = sum(row[f"top{k}"] for k in range(1, 6)) / 5
        rows.append(row)
    return pd.DataFrame(rows)


def pct(x: float) -> str:
    return f"{x:.2%}"


def main() -> None:
    results = pd.read_csv(OUT / "case_results.csv")
    assert len(results) == 272 * 4
    full = results.copy()
    kept = results[~results.case_id.isin(EXCLUDED)].copy()
    dropped = results[results.case_id.isin(EXCLUDED)]
    print(f"原始 272 例 -> 排除 {len(EXCLUDED)} 例 -> 剩余 {kept.case_id.nunique()} 例")
    print("排除案例层级:", sorted(dropped.drop_duplicates('case_id').level.tolist()))

    summary = summarize(kept, "all")
    by_level = pd.concat([summarize(g, level) for level, g in kept.groupby("level")])

    # Mapping -> Containment transition per level (the LSDB-protocol comparison)
    transition_rows = []
    for level in ["host", "pod", "service"]:
        lvl = kept[kept.level == level]
        mapping = lvl[lvl.arm == "mapping"]
        containment = lvl[lvl.arm == "containment"]
        row = {"level": level, "cases": lvl.case_id.nunique()}
        for k in range(1, 6):
            row[f"mapping_top{k}"] = float((mapping["rank"] <= k).mean())
            row[f"containment_top{k}"] = float((containment["rank"] <= k).mean())
        row["mapping_avg5"] = sum(row[f"mapping_top{k}"] for k in range(1, 6)) / 5
        row["containment_avg5"] = sum(row[f"containment_top{k}"] for k in range(1, 6)) / 5
        transition_rows.append(row)
    transition = pd.DataFrame(transition_rows)

    out_dir = OUT / "excl_3_deployment_gap_cases"
    try:
        out_dir.mkdir(exist_ok=True)
        summary.to_csv(out_dir / "summary.csv", index=False)
        by_level.to_csv(out_dir / "by_level.csv", index=False)
        transition.to_csv(out_dir / "mapping_to_containment.csv", index=False)
        (out_dir / "excluded_cases.json").write_text(
            json.dumps({
                "excluded_cases": sorted(EXCLUDED),
                "exclusion_reason": "incomplete host->pod coverage in the frozen pilot",
                "kept_cases": int(kept.case_id.nunique()),
                "original_cases": 272,
            }, indent=2), encoding="utf-8")
        print("已写出:", out_dir)
    except PermissionError as e:
        print("跳过落盘(权限):", e)

    print("\n=== 269 例总体（严格 LSDB 口径）===")
    print(summary[["arm", "cases", "top1", "top3", "top5", "avg5"]].to_string(
        index=False, formatters={"top1": pct, "top3": pct, "top5": pct}))
    print("\n=== 269 例分层 ===")
    print(by_level[["scope", "arm", "cases", "top1", "top3", "top5", "avg5"]].to_string(
        index=False, formatters={"top1": pct, "top3": pct, "top5": pct}))
    print("\n=== Mapping -> Containment（分层，269 例）===")
    print(transition.to_string(index=False))

    # delta vs 272 for reference
    full_summary = summarize(full, "all")
    print("\n=== 272 vs 269 总体 Top-1 变化 ===")
    cmp = full_summary.merge(summary, on="arm", suffixes=("_272", "_269"))
    print(cmp[["arm", "top1_272", "top1_269", "top3_272", "top3_269", "top5_272", "top5_269"]].to_string(
        index=False, formatters={c: pct for c in cmp.columns if c.startswith("top")}))


if __name__ == "__main__":
    main()
