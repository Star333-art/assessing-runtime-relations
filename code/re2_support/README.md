# BARO and RE2 replay support

This directory contains the retained BARO implementation and the frozen-result
replay entry point. Upstream attribution and permissions are in [LICENSE](LICENSE)
and [THIRD_PARTY.md](../../THIRD_PARTY.md). The local package version is a static
support-package version, independent of upstream releases and Git metadata.

From the repository root, using the configured Python environment:

```bash
python code/re2_support/run_heterogeneous_graph_component_ablation.py --input results/re2 --output reproduced/re2 --lambda 0.05 --direction forward --replay-existing
```

The input consists of 90 cases for each of five components. Outputs are written
to a separate directory. Overlapping input/output locations and outputs under
the frozen `results/` tree are rejected, including when `--force` is supplied.
This replay does not load raw RCAEval telemetry. The unavailable historical raw
graph builder is not part of the supported execution path.

Saved `avg5` fields use reciprocal rank truncated at rank 5. The supporting
analysis recomputes the declared Avg@5 from rank as the mean of Top-1 through
Top-5; it does not use that saved field as Avg@5:

```bash
python code/ls_baro/tools/audit_extended_dataset_roles.py
```

Tests run from this directory with `python -m pytest -q`.
