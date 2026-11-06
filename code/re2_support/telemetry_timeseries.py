"""Convert raw RCAEval RE2 telemetry to the official 15-second time series."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd


INTERVAL_SECONDS = 15


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def resolve_service(value: str, candidates: Iterable[str]) -> Optional[str]:
    """Resolve telemetry aliases against metric-derived service entities."""
    exact = {_compact(candidate): str(candidate) for candidate in candidates}
    key = _compact(value)
    if key in exact:
        return exact[key]
    if key.endswith("service") and key[:-7] in exact:
        return exact[key[:-7]]
    return None


def _complete_bins(frame: pd.DataFrame, fill_value: float = 0.0) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["time"])
    start, end = int(frame.index.min()), int(frame.index.max())
    index = pd.Index(range(start, end + INTERVAL_SECONDS, INTERVAL_SECONDS), name="time")
    return frame.reindex(index).fillna(fill_value).reset_index()


def build_metric_ts(metrics_path: Path | str) -> pd.DataFrame:
    """Downsample one-second metrics exactly as RCAEval multi-source BARO."""
    frame = pd.read_parquet(metrics_path)
    return frame.iloc[::INTERVAL_SECONDS, :].reset_index(drop=True)


def build_log_ts(logs_path: Path | str) -> pd.DataFrame:
    """Count log events per logical container in fixed 15-second bins."""
    path = Path(logs_path)
    if not path.exists():
        return pd.DataFrame(columns=["time"])
    logs = pd.read_parquet(path, columns=["timestamp", "container_name"])
    logs["timestamp"] = pd.to_numeric(logs["timestamp"], errors="coerce")
    logs = logs.dropna(subset=["timestamp", "container_name"])
    logs["time"] = (logs["timestamp"].astype("int64") // INTERVAL_SECONDS) * INTERVAL_SECONDS
    table = logs.groupby(["time", "container_name"]).size().unstack(fill_value=0)
    table.columns = [f"log::{name}" for name in table.columns]
    return _complete_bins(table)


def _is_trace_error(value: object) -> bool:
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    if text in {"", "0", "0.0", "ok", "success", "unset"}:
        return False
    try:
        number = float(text)
        return number != 0 and (number >= 400 or number < 100)
    except ValueError:
        return True


def build_trace_ts(
    traces_path: Path | str,
    services: Iterable[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, str]]:
    """Build operation error-count and mean-latency series at 15 seconds."""
    path = Path(traces_path)
    if not path.exists():
        empty = pd.DataFrame(columns=["time"])
        return empty, empty.copy(), {}
    traces = pd.read_parquet(path)
    required = {"serviceName", "operationName", "startTimeMillis", "duration", "statusCode"}
    missing = required - set(traces.columns)
    if missing:
        raise ValueError(f"Trace file is missing columns: {sorted(missing)}")
    if "methodName" not in traces:
        traces["methodName"] = None

    candidates = list(services)
    traces["service"] = traces["serviceName"].map(
        lambda value: resolve_service(value, candidates)
    )
    traces["task"] = traces["methodName"].where(
        traces["methodName"].notna(), traces["operationName"]
    ).astype(str).str.strip()
    traces = traces.dropna(subset=["service"])
    traces = traces[traces["task"].ne("") & traces["task"].ne("nan")]
    traces["operation"] = traces["service"] + "/" + traces["task"]
    millis = pd.to_numeric(traces["startTimeMillis"], errors="coerce")
    traces["time"] = ((millis / 1000).astype("Int64") // INTERVAL_SECONDS) * INTERVAL_SECONDS
    traces["duration"] = pd.to_numeric(traces["duration"], errors="coerce")
    traces["error"] = traces["statusCode"].map(_is_trace_error).astype(float)
    traces = traces.dropna(subset=["time"])
    traces["time"] = traces["time"].astype("int64")

    err = traces.groupby(["time", "operation"])["error"].sum().unstack(fill_value=0)
    err.columns = [f"trace_err::{name}" for name in err.columns]
    lat = traces.groupby(["time", "operation"])["duration"].mean().unstack()
    lat.columns = [f"trace_lat::{name}" for name in lat.columns]
    owners = {
        operation: operation.split("/", 1)[0]
        for operation in traces["operation"].unique()
    }
    return _complete_bins(err), _complete_bins(lat), owners
