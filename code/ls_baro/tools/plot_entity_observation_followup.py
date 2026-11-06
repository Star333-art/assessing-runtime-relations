"""Publication-oriented figures for the observation-control follow-up."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/entity_observation_followup_20261026"
AI = ROOT / "output/aiops2025_observability_rewire_20261026"


def main():
    ai = pd.read_csv(AI / "observation_gradient_summary.csv")
    ls = pd.read_csv(OUT / "lsdb_observation_gradient_summary.csv")
    prior = pd.read_csv(OUT / "structural_prior_summary.csv")
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "svg.fonttype": "none"})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1), constrained_layout=True)
    for arm, label, color in (("mapping", "Mapping", "#555555"),
                               ("deployment", "Deployment", "#0072B2")):
        g = ai[(ai.level == "host") & (ai.layer_masked == "host") & (ai.arm == arm)].sort_values("fraction")
        axes[0].plot(g.fraction * 100, g.tie_avg5, marker="o", label=label, color=color)
    p = prior[(prior.dataset == "aiops2025") & (prior.scope == "host") &
              (prior.arm == "deployment") & (prior.control == "incident_edge_count")].iloc[0]
    axes[0].axhline(p.tie_avg5, linestyle="--", color="#D55E00", label="Degree only")
    axes[0].set_title("AIOps2025: Host roots (n=82)")
    axes[0].set_ylabel("Tie-expected Avg@5")
    for arm, label, color, style in (("mapping", "Mapping", "#555555", "-"),
        ("dependency", "Dependency", "#009E73", "-"),
        ("containment", "Containment", "#0072B2", "-"),
        ("full", "Full", "#CC79A7", "--")):
        g = ls[(ls.level == "service") & (ls.layer_masked == "service") & (ls.arm == arm)].sort_values("fraction")
        axes[1].plot(g.fraction * 100, g.tie_avg5, marker="o", color=color, linestyle=style, label=label)
    axes[1].set_title("LSDB: Service roots (n=50)")
    p = prior[(prior.dataset == "lsdb") & (prior.scope == "service") &
              (prior.arm == "ownership") & (prior.control == "incident_edge_count")].iloc[0]
    axes[1].axhline(p.tie_avg5, linestyle="--", color="#D55E00", label="Ownership degree only")
    for ax in axes:
        ax.set_xlabel("Requested fraction of nonzero entity bases masked (%)")
        ax.set_xticks([0, 25, 50, 75, 100]); ax.set_ylim(0, 1)
        ax.grid(axis="y", alpha=.2); ax.legend(frameon=False, fontsize=9)
    fig.savefig(OUT / "observation_controls.png", dpi=220)
    fig.savefig(OUT / "observation_controls.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
