from entity_mapping import (
    map_metric_to_service,
    metric_ranks_to_service_ranks,
)


def test_maps_metric_to_service_using_final_separator():
    mapping = map_metric_to_service("frontend-external_latency")

    assert mapping.service == "frontend-external"
    assert mapping.metric_type == "latency"
    assert mapping.mapped is True


def test_keeps_unmapped_candidate_visible():
    mapping = map_metric_to_service("unknownmetric")

    assert mapping.service == "unknownmetric"
    assert mapping.metric_type == "unknown"
    assert mapping.mapped is False


def test_service_ranking_deduplicates_metrics_and_uses_max_score():
    metric_results = [
        ("adservice_cpu", 9.0),
        ("frontend_latency", 8.0),
        ("adservice_mem", 7.0),
    ]

    assert metric_ranks_to_service_ranks(metric_results) == [
        ("adservice", 9.0),
        ("frontend", 8.0),
    ]

