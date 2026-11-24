"""Portable locations shared by raw-data experiment stages.

The command-line orchestrator sets these environment variables before starting
each stage. Individual stages can use the same variables without orchestration.
"""
from pathlib import Path
import os

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = PACKAGE_ROOT.parents[1]


def output_path(relative=""):
    root = Path(os.environ.get("LS_BARO_OUTPUT_ROOT", REPOSITORY / "reproduced/raw")).expanduser().resolve()
    return root / relative


def dataset_path(dataset):
    defaults = {"lsdb": "lsdb", "aiops2025": "AgenticOpsEval/AIOps2025",
                "microcercl": "abnormal_20240615/abnormal"}
    return Path(os.environ.get(f"LS_BARO_{dataset.upper()}_ROOT",
        REPOSITORY / "data" / defaults[dataset])).expanduser().resolve()
