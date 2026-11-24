# LS-baro

The shared engine maps BARO-compatible metric scores to native entities and
applies one immutable-base-score topology pass. It does not train a model.
Dataset adapters provide typed entities, observed or explicitly adapter-derived
relations, metric bindings, and separate evaluation labels.

```text
Metric scores -> metric-to-entity aggregation -> one-step relation propagation
              -> Host / Pod / Service candidate rankings and edge contributions
```

The fixed core configuration uses signed maximum RobustScaler scores,
`exclude_last_seen`, most-specific metric assignment, raw/max aggregation,
lambda 0.1, bidirectional propagation, and source-row normalization. Neither
updated scores nor evaluation root identities are fed back into propagation.
The experimental design supplies the target layer for masking and candidate
restriction; the root entity identity is used only for evaluation.

## Run and reproduce

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m pip install -e code/ls_baro
python code/ls_baro/tools/reproduce_raw.py --dry-run
```

Use [RAW_REPRODUCTION.md](RAW_REPRODUCTION.md) for explicit data/output arguments,
ordered stages, limited runs, intermediate dependencies and optional historical
checks. The data is obtained separately from the projects in
[THIRD_PARTY.md](../../THIRD_PARTY.md). Processed-evidence replay needs no raw data:

```bash
python code/ls_baro/tools/replay_mechanism_controls.py
python code/ls_baro/tools/evaluate_mechanism_holm.py
python code/ls_baro/tools/audit_final_requirements.py
```

The complete core test suite runs from this directory with `python -m pytest -q`.

## Implementation contract

`RCAEngine.analyze(graph, observations, window, config)` separates dataset parsing
from scoring, aggregation and propagation. `deployment` connects Host and Pod;
`ownership` connects Service and Pod; `dependency` connects caller and callee
Services. `containment` is an alias for Deployment plus Ownership. Missing entity
types, identities or relation evidence are not invented.

The score update is:

```text
final[i] = mapped[i] + lambda * sum_j(weight[j,i] * mapped[j])
```

Normalization uses selected outgoing degrees. The `reference_row` sensitivity
keeps denominators fixed to all supported relations. Graph disabling retains
the original candidates and mapped bases. Final result records include global
and per-kind rankings, base/final scores, contributions, impact paths, graph
fingerprints and run configuration.

## Dataset roles

- LSDB: 88 native Host/Pod/Service cases and eight relation arms; mechanism
  comparisons use task-specific structural eligibility. All-zero source-score
  tasks are boundary controls, not evidence of usable cross-layer scores.
- AIOps2025: the 272-case single-entity scope contains 82 Host, 68 Pod and 122
  Service cases. Each mechanism task further requires an observed relation edge.
- MicroCERCL: 615 directory-exact Pod cases (269 Hipster, 346 SockShop). Ownership
  is derived from documented naming rules. The filtered Ownership condition
  excludes instance-aggregated Service resource metrics; Deployment uses its
  original source set. See [MICROCERCL_PROTOCOL.md](MICROCERCL_PROTOCOL.md).
- AIOps2022: the bundled supporting run covers 51 isolated cases from one
  cloudbed subset; it does not instantiate the full mechanism protocol. The
  runners can process other released subsets when requested explicitly.
- RE2: the public entry point replays frozen case-component outputs; its
  historical raw graph builder is unavailable. See
  [ARTIFACT_LIMITATIONS.md](../../ARTIFACT_LIMITATIONS.md).

The complete-mask matrix uses tie-expected Top-k and Avg@5. Exploratory mechanism
labels require positive Real-minus-Mapping and all four Real-minus-Control
interval lower bounds for alignment-dependent recovery. Nominal paired bootstrap
labels and the global 50-test Holm sensitivity remain distinct analyses.

See [PROJECT_GUIDE.md](PROJECT_GUIDE.md) for the active script inventory and
[REPRODUCTION_STATUS.md](../../REPRODUCTION_STATUS.md) for the release status.
