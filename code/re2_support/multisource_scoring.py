"""BARO-compatible robust scoring and evidence fusion for RE2 telemetry."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler

from entity_mapping import metric_ranks_to_service_ranks, normalize_service_name
from telemetry_timeseries import build_log_ts, build_trace_ts, resolve_service


def robust_frame_scores(frame: pd.DataFrame, inject_time: int) -> Dict[str, float]:
    """Apply the same normal/anomalous split and RobustScaler as mmBARO."""
    if frame.empty or "time" not in frame:
        return {}
    normal = frame[frame["time"] < inject_time]
    anomalous = frame[frame["time"] >= inject_time]
    scores = {}
    for column in frame.columns:
        if column == "time":
            continue
        a = pd.to_numeric(normal[column], errors="coerce").dropna().to_numpy()
        b = pd.to_numeric(anomalous[column], errors="coerce").dropna().to_numpy()
        if len(a) < 4 or len(b) == 0 or np.all(a == a[0]):
            continue
        scaler = RobustScaler().fit(a.reshape(-1, 1))
        value = float(np.max(scaler.transform(b.reshape(-1, 1))[:, 0]))
        if np.isfinite(value):
            scores[str(column)] = value
    return scores


def normalize_scores(scores: Mapping[str, float]) -> Dict[str, float]:
    """Min-max normalize one modality without using labels."""
    finite = {key: float(value) for key, value in scores.items() if np.isfinite(value)}
    if not finite:
        return {}
    low, high = min(finite.values()), max(finite.values())
    if high == low:
        return {key: 1.0 for key in finite}
    return {key: (value - low) / (high - low) for key, value in finite.items()}


def build_multisource_evidence(
    metric_results: Iterable[Tuple[str, float]],
    logs_path: Path | str,
    traces_path: Path | str,
    inject_time: int,
) -> dict:
    """Score each modality, map it to typed entities, and fuse at Service."""
    metric_results = list(metric_results)
    raw_metric_services = dict(metric_ranks_to_service_ranks(metric_results))
    services = list(raw_metric_services)
    metric_service = normalize_scores(raw_metric_services)

    log_raw = robust_frame_scores(build_log_ts(logs_path), inject_time)
    log_normalized = normalize_scores(log_raw)
    container_scores = {
        name.split("::", 1)[1]: score for name, score in log_normalized.items()
    }

    trace_err, trace_lat, operation_owners = build_trace_ts(traces_path, services)
    err_scores = normalize_scores(robust_frame_scores(trace_err, inject_time))
    lat_scores = normalize_scores(robust_frame_scores(trace_lat, inject_time))
    operation_components: Dict[str, List[float]] = defaultdict(list)
    for prefix_scores in (err_scores, lat_scores):
        for name, score in prefix_scores.items():
            operation_components[name.split("::", 1)[1]].append(score)
    operation_scores = {
        operation: sum(values) / len(values)
        for operation, values in operation_components.items()
    }

    service_components: Dict[str, Dict[str, float]] = defaultdict(dict)
    for service, score in metric_service.items():
        service_components[service]["metric"] = score
    for container, score in container_scores.items():
        service = resolve_service(container, services)
        if service:
            service_components[service]["log"] = max(
                score, service_components[service].get("log", 0.0)
            )
    for operation, score in operation_scores.items():
        service = operation_owners.get(operation)
        if service:
            service_components[service]["trace"] = max(
                score, service_components[service].get("trace", 0.0)
            )
    service_scores = {
        service: sum(parts.values()) / len(parts)
        for service, parts in service_components.items()
        if parts
    }
    return {
        "metric_scores": metric_results,
        "logical_container_scores": sorted(
            container_scores.items(), key=lambda item: item[1], reverse=True
        ),
        "operation_scores": sorted(
            operation_scores.items(), key=lambda item: item[1], reverse=True
        ),
        "service_base_scores": sorted(
            service_scores.items(), key=lambda item: item[1], reverse=True
        ),
        "service_components": dict(service_components),
        "operation_owners": operation_owners,
        "normalization": "per-modality min-max",
        "interval_seconds": 15,
    }
