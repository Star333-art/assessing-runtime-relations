import pandas as pd

from multisource_scoring import normalize_scores, robust_frame_scores
from telemetry_timeseries import build_log_ts, build_trace_ts


def test_logts_counts_events_in_unix_aligned_15_second_bins(tmp_path):
    path = tmp_path / "logs.parquet"
    pd.DataFrame({
        "timestamp": [1, 14, 15, 29],
        "container_name": ["cart", "cart", "cart", "frontend"],
    }).to_parquet(path, index=False)

    result = build_log_ts(path)

    assert result["time"].tolist() == [0, 15]
    assert result["log::cart"].tolist() == [2, 1]
    assert result["log::frontend"].tolist() == [0, 1]


def test_trace_series_use_operation_error_count_and_mean_latency(tmp_path):
    path = tmp_path / "traces.parquet"
    pd.DataFrame({
        "serviceName": ["frontendservice", "frontendservice"],
        "methodName": ["GetCart", "GetCart"],
        "operationName": ["CartService/GetCart", "CartService/GetCart"],
        "startTimeMillis": [1000, 2000],
        "duration": [10, 30],
        "statusCode": [0, 500],
    }).to_parquet(path, index=False)

    errors, latency, owners = build_trace_ts(path, ["frontend", "cartservice"])

    assert errors["trace_err::frontend/GetCart"].tolist() == [1.0]
    assert latency["trace_lat::frontend/GetCart"].tolist() == [20.0]
    assert owners["frontend/GetCart"] == "frontend"


def test_robust_scores_split_on_inject_time_and_normalize():
    frame = pd.DataFrame({
        "time": [0, 15, 30, 45, 60, 75],
        "a": [1, 2, 1, 2, 20, 21],
        "constant": [1, 1, 1, 1, 1, 1],
    })

    scores = robust_frame_scores(frame, inject_time=60)

    assert scores["a"] > 0
    assert "constant" not in scores
    assert normalize_scores({"a": 2, "b": 4}) == {"a": 0.0, "b": 1.0}
