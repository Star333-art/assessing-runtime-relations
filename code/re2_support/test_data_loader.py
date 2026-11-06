import json

import pandas as pd

from data_loader import DataLoader


def _write_inject_time(case_dir, value=3):
    (case_dir / "inject_time.txt").write_text(str(value), encoding="utf-8")


def test_loads_metrics_json(tmp_path):
    case_dir = tmp_path / "re1ob_cartservice_cpu_1"
    case_dir.mkdir()
    _write_inject_time(case_dir)
    expected = {"cartservice_cpu": [[1, 10.0], [2, 11.0], [3, 30.0]]}
    (case_dir / "metrics.json").write_text(json.dumps(expected), encoding="utf-8")

    loader = DataLoader(str(tmp_path))
    item = loader.get_dataset(case_dir.name)

    assert loader.datasets == [case_dir.name]
    assert item["metrics"] == expected
    assert item["inject_time"] == 3
    assert item["root_cause_service"] == "cartservice"


def test_loads_metrics_parquet_and_drops_missing_points(tmp_path):
    case_dir = tmp_path / "re1ob_checkoutservice_mem_2"
    case_dir.mkdir()
    _write_inject_time(case_dir, value=12)
    pd.DataFrame(
        {
            "time": [10, 11, 12],
            "checkoutservice_mem": [1.0, None, 5.0],
            "empty_metric": [None, None, None],
        }
    ).to_parquet(case_dir / "metrics.parquet", index=False)

    item = DataLoader(str(tmp_path)).get_dataset(case_dir.name)

    assert item["metrics"] == {
        "checkoutservice_mem": [[10, 1.0], [12, 5.0]],
    }
    assert item["inject_time"] == 12


def test_prefers_parquet_when_both_formats_exist(tmp_path):
    case_dir = tmp_path / "re1ob_adservice_delay_3"
    case_dir.mkdir()
    _write_inject_time(case_dir)
    (case_dir / "metrics.json").write_text(
        json.dumps({"json_metric": [[1, 99.0]]}), encoding="utf-8"
    )
    pd.DataFrame({"time": [1], "parquet_metric": [7.0]}).to_parquet(
        case_dir / "metrics.parquet", index=False
    )

    metrics = DataLoader(str(tmp_path)).get_dataset(case_dir.name)["metrics"]

    assert metrics == {"parquet_metric": [[1, 7.0]]}
