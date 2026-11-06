"""AIOps-native four-arm evaluation with fault isolation and causal containment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_aiops2022_ablation import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run AIOps 2022 with isolated fault windows and relation-aware "
            "Pod-to-Node/Service containment propagation."
        )
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--subset", required=True)
    parser.add_argument(
        "--output", type=Path,
        default=Path("output/aiops2022-native"),
    )
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.direction = "relation-aware"
    args.isolation_guard_seconds = 600
    args.protocol_name = "aiops2022-native-isolated-relation-aware"
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
