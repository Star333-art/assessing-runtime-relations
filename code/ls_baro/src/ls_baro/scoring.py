from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from sklearn.preprocessing import RobustScaler


SCORING_SCHEMA_VERSION = 2


def filter_metrics(
    metrics: Mapping[str, Sequence[tuple[int, float]]],
    metric_policy: str = "all",
) -> dict[str, Sequence[tuple[int, float]]]:
    """Apply a label-independent metric policy before BARO scoring."""
    if metric_policy not in {"all", "exclude_last_seen"}:
        raise ValueError("metric_policy must be all or exclude_last_seen")
    if metric_policy == "all":
        return dict(metrics)

    selected = {}
    for metric, samples in metrics.items():
        try:
            import json

            kpi = str(json.loads(metric).get("kpi", "")).lower()
        except (TypeError, ValueError):
            kpi = ""
        if kpi == "container_last_seen":
            continue
        selected[metric] = samples
    return selected


def score_with_baro_dataframe_equivalent(
    metrics: Mapping[str, Sequence[tuple[int, float]]],
    inject_time: int,
) -> list[tuple[str, float]]:
    """Reproduce BARO's DataFrame RobustScaler scoring per metric.

    BARO preprocesses the normal and anomalous windows independently with
    ``drop_constant``.  A metric must therefore vary in both windows.  The
    score is the maximum post-fault value transformed by a RobustScaler fitted
    on the pre-fault window; negative deviations are not converted to absolute
    values.
    """
    rows: list[tuple[str, float]] = []
    for metric, samples in metrics.items():
        pre = np.asarray(
            [float(value) for timestamp, value in samples if timestamp < inject_time],
            dtype=float,
        )
        post = np.asarray(
            [float(value) for timestamp, value in samples if timestamp >= inject_time],
            dtype=float,
        )
        pre = pre[np.isfinite(pre)]
        post = post[np.isfinite(post)]
        if pre.size == 0 or post.size == 0:
            continue
        if np.all(pre == pre[0]) or np.all(post == post[0]):
            continue
        scaler = RobustScaler().fit(pre.reshape(-1, 1))
        score = float(np.max(scaler.transform(post.reshape(-1, 1))[:, 0]))
        rows.append((str(metric), score))
    return sorted(rows, key=lambda item: (-item[1], item[0]))


def score_with_official_baro(
    metrics: Mapping[str, Sequence[tuple[int, float]]],
    inject_time: int,
    baro_root: Path | str | None = None,
    score_mode: str = "baro_dataframe_equivalent",
    metric_policy: str = "all",
) -> list[tuple[str, float]]:
    """Score LatentScope metrics with an explicit BARO compatibility mode."""
    selected = filter_metrics(metrics, metric_policy)
    if score_mode == "baro_dataframe_equivalent":
        return score_with_baro_dataframe_equivalent(selected, inject_time)
    if score_mode != "legacy_dict":
        raise ValueError(
            "score_mode must be baro_dataframe_equivalent or legacy_dict"
        )
    if baro_root is not None:
        root = str(Path(baro_root).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        module = importlib.import_module("baro.root_cause_analysis")
    except ImportError as exc:
        raise RuntimeError(
            "Official BARO is unavailable. Install fse-baro or pass --baro-root "
            "pointing at the BARO source checkout."
        ) from exc
    result = module.robust_scorer(dict(selected), inject_time=inject_time)
    scores = result.get("scores")
    if scores is None:
        raise RuntimeError("This BARO checkout does not expose scored dict input")
    return [(str(metric), float(score)) for metric, score in scores]
