from __future__ import annotations

import json

import pytest

from ls_baro.dataset import LatentScopeCase
from ls_baro.engine import RCAEngine
from ls_baro.lsdb_adapter import LSDBAdapter
from ls_baro.model import Edge, entity_id
from ls_baro.pipeline import root_cause_rank, root_cause_rank_details, run_case
from ls_baro.scoring import score_with_baro_dataframe_equivalent
from ls_baro.topology import (
    aggregate_metric_scores,
    build_propagation_edges,
    hierarchical_rankings,
    normalize_metric_scores,
    propagate_once,
)


@pytest.fixture()
def dataset(tmp_path):
    root = tmp_path / "dataset_b"
    (root / "labels").mkdir(parents=True)
    (root / "data" / "1").mkdir(parents=True)
    metric = json.dumps({"node": "node-1", "service": "api", "pod": "api-0", "kpi": "cpu"})
    (root / "labels" / "label.json").write_text(json.dumps({
        "1": {"time": 3, "rc": [{"type": "pod", "key": "api-0"}], "trigger": [json.dumps({"service": "web", "kpi": "mrt"})]}
    }), encoding="utf-8")
    (root / "labels" / "service_deps.json").write_text(
        json.dumps([["web", "api"]]), encoding="utf-8"
    )
    (root / "data" / "1" / "metrics.json").write_text(json.dumps([{
        "key": metric, "service": "api", "data": {"1": 0, "2": 0, "3": 8, "4": 9}
    }]), encoding="utf-8")
    (root / "data" / "1" / "rccs.json").write_text(json.dumps({
        "rccs": [
            {"key": "node-1", "kind": "host", "linked_metrics": [metric]},
            {"key": "api-0", "kind": "pod", "linked_metrics": [metric]},
            {"key": "api", "kind": "service", "linked_metrics": [metric]},
            {"key": "web", "kind": "service", "linked_metrics": []},
        ],
        "rcc_edges": [
            {"rcc1": "node-1", "rcc2": "api-0", "linked_metrics": [metric]},
            {"rcc1": "api", "rcc2": "api-0", "linked_metrics": [metric]},
        ],
    }), encoding="utf-8")
    return root, metric


def test_loads_real_fields_without_inventing_levels(dataset):
    root, metric = dataset
    case = LatentScopeCase.load(root, "1")
    assert case.metric_descriptor(metric) == {
        "node": "node-1", "service": "api", "pod": "api-0", "kpi": "cpu"
    }
    assert case.level_availability()["unavailable"] == ["physical_server", "process", "task"]


def test_propagates_formula_once_from_immutable_base():
    base = {"service::web": 4.0, "service::api": 2.0}
    final, rows = propagate_once(
        base,
        [Edge("service::web", "service::api", 0.5, "calls", "test")],
        0.2,
    )
    assert final["service::api"] == pytest.approx(2.4)
    assert rows[0]["delta"] == pytest.approx(0.4)


def test_uses_rcc_containment_and_service_dependencies(dataset):
    case = LatentScopeCase.load(dataset[0], "1")
    evidence, original = build_propagation_edges(case)
    triples = {(edge.source, edge.target, edge.relation) for edge in evidence}
    assert ("pod::api-0", "host::node-1", "contains:relation-aware-backward") in triples
    assert ("pod::api-0", "service::api", "contains:relation-aware-backward") in triples
    assert ("service::web", "service::api", "calls:relation-aware-forward") in triples
    assert any(edge.provenance == "rccs.json:rcc_edges" for edge in original)


def test_row_normalization_and_direction(dataset):
    case = LatentScopeCase.load(dataset[0], "1")
    edges, _ = build_propagation_edges(
        case, components="all", direction="bidirectional", edge_normalization="row"
    )
    totals = {}
    for edge in edges:
        totals[edge.source] = totals.get(edge.source, 0.0) + edge.weight
    assert all(total == pytest.approx(1.0) for total in totals.values())


def test_score_normalization_and_entity_aggregation(dataset):
    case = LatentScopeCase.load(dataset[0], "1")
    metric = dataset[1]
    assert normalize_metric_scores([("a", 8.0), ("b", 4.0)], "max") == [
        ("a", 1.0), ("b", 0.5)
    ]
    scores, _ = aggregate_metric_scores(case, {metric: 9.0}, aggregation="top5_mean")
    assert scores[entity_id("pod", "api-0")] == 9.0


def test_most_specific_mapping_assigns_shared_metric_once(dataset):
    case = LatentScopeCase.load(dataset[0], "1")
    metric = dataset[1]
    scores, edges = aggregate_metric_scores(
        case, {metric: 9.0}, aggregation="max", mapping_policy="most_specific"
    )
    assert scores[entity_id("pod", "api-0")] == 9.0
    assert scores[entity_id("host", "node-1")] == 0.0
    assert scores[entity_id("service", "api")] == 0.0
    assert [(edge.source, edge.target) for edge in edges] == [
        (entity_id("metric", metric), entity_id("pod", "api-0"))
    ]


def test_percentile_normalization_preserves_ties():
    rows = normalize_metric_scores(
        [("c", 1.0), ("a", 3.0), ("b", 3.0), ("d", 0.0)], "percentile"
    )
    scores = dict(rows)
    assert scores["a"] == scores["b"]
    assert scores["c"] < scores["a"]


def test_dataframe_equivalent_scoring_uses_robust_scaler_without_absolute_value():
    scores = dict(score_with_baro_dataframe_equivalent({
        "up": [(1, 0), (2, 1), (3, 2), (4, 3), (5, 4), (6, 5)],
        "down": [(1, 5), (2, 6), (3, 7), (4, 8), (5, 2), (6, 1)],
    }, inject_time=5))
    assert scores["up"] > 0
    assert scores["down"] < 0


def test_end_to_end_outputs_ranking_and_actual_impact_path(dataset):
    root, metric = dataset

    def scorer(metrics, inject_time, baro_root):
        assert list(metrics) == [metric]
        assert inject_time == 3
        return [(metric, 10.0)]

    result = run_case(root, "1", propagation_lambda=0.2, scorer=scorer)
    assert result["hierarchical_rankings"]["pod"][0]["key"] == "api-0"
    assert result["evaluation_protocol"]["labels_used_by_engine"] is False
    assert root_cause_rank(result) == 1
    assert root_cause_rank_details(result, "global") is not None
    assert any(
        [row["name"] for row in path["entities"]][:2] == ["node-1", "api-0"]
        for path in result["impact_paths"]
    )


def test_lsdb_adapter_builds_canonical_contract_and_separate_evaluation(dataset):
    root, metric = dataset
    adapted = LSDBAdapter().load(root, "1")
    assert {entity.id for entity in adapted.graph.entities} == {
        "lsdb:host:node-1", "lsdb:pod:api-0",
        "lsdb:service:api", "lsdb:service:web",
    }
    assert {relation.kind for relation in adapted.graph.relations} == {
        "deployment", "ownership", "dependency",
    }
    assert adapted.observations[0].metric_id.startswith("lsdb:metric:")
    assert adapted.graph.metric_bindings
    assert adapted.evaluation.root_causes[0]["key"] == "api-0"
    assert adapted.window.detection_start == 3


def test_engine_mapping_only_and_label_isolation(dataset):
    root, metric = dataset
    adapted = LSDBAdapter().load(root, "1")
    engine = RCAEngine(scorer=lambda *_: [(metric, 10.0)])
    from ls_baro.contracts import RCAConfig

    result = engine.analyze(
        adapted.graph, adapted.observations, adapted.window,
        RCAConfig(relation_groups=("mapping",), propagation_lambda=0.2),
    )
    assert result.propagation_edges == []
    assert result.contributions == []
    assert "ground_truth" not in result.run_info
    assert result.base_scores["lsdb:pod:api-0"] == 10.0


def test_engine_migration_matches_pre_migration_numeric_outputs(dataset):
    """Keep the old helper path as a numeric oracle for the canonical engine."""
    from ls_baro.contracts import RCAConfig

    root, metric = dataset
    case = LatentScopeCase.load(root, "1")
    adapted = LSDBAdapter().load(root, "1")
    metric_ranking = [(metric, 10.0)]
    old_metric_scores = dict(normalize_metric_scores(metric_ranking, "raw"))
    old_base, _ = aggregate_metric_scores(
        case, old_metric_scores, aggregation="max", mapping_policy="all_links"
    )
    old_edges, _ = build_propagation_edges(
        case, components="all", direction="relation-aware", edge_normalization="none"
    )
    old_final, old_contributions = propagate_once(old_base, old_edges, 0.2)
    result = RCAEngine(scorer=lambda *_: metric_ranking).analyze(
        adapted.graph,
        adapted.observations,
        adapted.window,
        RCAConfig(mapping_policy="all_links", propagation_lambda=0.2),
    )
    runtime_id = {entity.name: entity.id for entity in adapted.graph.entities}
    for entity in case.entities:
        assert result.base_scores[runtime_id[entity.key]] == pytest.approx(old_base[entity.id])
        assert result.final_scores[runtime_id[entity.key]] == pytest.approx(old_final[entity.id])
    for level in ("host", "pod", "service"):
        assert [row["key"] for row in result.rankings[level]] == [
            row["key"] for row in hierarchical_rankings(case, old_final, old_metric_scores)[level]
        ]
    assert sorted(row["delta"] for row in result.contributions) == pytest.approx(
        sorted(row["delta"] for row in old_contributions)
    )


@pytest.mark.parametrize("selectors,host,service,kinds", [
    (("deployment",), 1.0, 0.0, {"deployment"}),
    (("ownership",), 0.0, 1.0, {"ownership"}),
    (("deployment", "ownership"), 0.5, 0.5, {"deployment", "ownership"}),
    (("containment", "deployment", "ownership"), 0.5, 0.5, {"deployment", "ownership"}),
])
def test_independent_relations_preserve_candidates_and_one_hop_policy(dataset, selectors, host, service, kinds):
    from ls_baro.contracts import RCAConfig

    root, metric = dataset
    adapted = LSDBAdapter().load(root, "1")
    result = RCAEngine(scorer=lambda *_: [(metric, 10.0)]).analyze(
        adapted.graph, adapted.observations, adapted.window,
        RCAConfig(relation_groups=("mapping", *selectors), propagation_lambda=0.1,
                  direction="bidirectional", edge_normalization="row"),
    )
    assert len(result.rankings["all_entities"]) == 4
    assert result.base_scores == {"lsdb:host:node-1": 0.0, "lsdb:pod:api-0": 10.0,
                                  "lsdb:service:api": 0.0, "lsdb:service:web": 0.0}
    assert result.final_scores["lsdb:host:node-1"] == pytest.approx(host)
    assert result.final_scores["lsdb:service:api"] == pytest.approx(service)
    assert result.final_scores["lsdb:pod:api-0"] == 10.0  # No feedback from updated parents.
    assert {e["relation_kind"] for e in result.propagation_edges} == kinds
    assert result.run_info["missing_relation_groups"] == []
    assert len(result.propagation_edges) == 2 * len(kinds)  # Alias overlap must not duplicate evidence.


def test_missing_individual_relation_is_reported(dataset):
    from dataclasses import replace
    from ls_baro.contracts import RCAConfig

    adapted = LSDBAdapter().load(dataset[0], "1")
    graph = replace(adapted.graph, relations=tuple(r for r in adapted.graph.relations if r.kind != "ownership"))
    result = RCAEngine(scorer=lambda *_: []).analyze(
        graph, adapted.observations, adapted.window, RCAConfig(relation_groups=("mapping", "ownership")),
    )
    assert result.run_info["missing_relation_groups"] == ["ownership"]
    assert result.propagation_edges == []
