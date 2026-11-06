"""Reference degrees isolate edge inclusion from redistribution of old weights."""
from dataclasses import replace

import pytest

from ls_baro.contracts import AnalysisWindow, MetricBinding, MetricSeries, RCAConfig, Relation, RuntimeEntity, RuntimeGraph
from ls_baro.engine import RCAEngine


@pytest.mark.parametrize("direction", ["forward", "backward", "bidirectional", "relation-aware"])
def test_reference_weights_are_fixed_across_relation_masks(direction):
    graph = RuntimeGraph(
        tuple(RuntimeEntity(k, t, k) for k, t in [("h", "host"), ("p", "pod"), ("s", "service"), ("c", "service")]),
        (Relation("d", "h", "p", "deployment", "observed"),
         Relation("o", "s", "p", "ownership", "observed"),
         Relation("c", "s", "c", "dependency", "observed"),
         Relation("ignored", "p", "c", "display_only", "display")),
        (MetricBinding("m", "p", "native"),), "synthetic", "1")
    obs, window = (MetricSeries("m", (0, 1), (1., 2.)),), AnalysisWindow(0, 0, 1, 1)
    engine = RCAEngine(scorer=lambda *_: [("m", 10.)])
    cfg = RCAConfig(relation_groups=("mapping", "deployment", "ownership", "dependency"),
                    propagation_lambda=.1, direction=direction, edge_normalization="reference_row")
    full = engine.analyze(graph, obs, window, cfg)
    row_full = engine.analyze(graph, obs, window, replace(cfg, edge_normalization="row"))
    assert full.final_scores == row_full.final_scores
    assert full.contributions == row_full.contributions
    key = lambda e: (e["source"], e["target"], e["relation_id"])
    weights = {key(e): e["weight"] for e in full.propagation_edges}
    for kinds in [("ownership",), ("deployment", "ownership"), ("dependency",)]:
        result = engine.analyze(graph, obs, window, replace(cfg, relation_groups=("mapping", *kinds)))
        assert result.base_scores == full.base_scores
        assert all(e["weight"] == weights[key(e)] for e in result.propagation_edges)
        assert result.final_scores["p"] == 10.  # No relay of updated parent evidence.
    if direction == "bidirectional":
        own = engine.analyze(graph, obs, window, replace(cfg, relation_groups=("mapping", "ownership")))
        contain = engine.analyze(graph, obs, window, replace(cfg, relation_groups=("mapping", "containment")))
        assert own.final_scores["s"] == contain.final_scores["s"] == .5
        assert own.final_scores["h"] == 0.
        assert contain.final_scores["h"] == .5
