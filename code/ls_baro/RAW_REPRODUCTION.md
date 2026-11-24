# Raw-data execution

Run commands from the repository root with Python 3.11 or later. Install
`requirements.txt` and, for the pinned statistical environment,
`code/ls_baro/requirements-holm.txt`. Third-party raw data is obtained separately
from the projects in [THIRD_PARTY.md](../../THIRD_PARTY.md).

## Data layout

The input roots are explicit and can be anywhere on disk:

| Option | Required contents |
|---|---|
| `--lsdb-root` | LatentScope Dataset B with `labels/label.json`, `labels/service_deps.json`, and `data/<case>/metrics.json` / `rccs.json` |
| `--aiops2025-root` | Extracted AIOps2025 with `groundtruth.jsonl`, the release README, and dated metric/trace directories |
| `--microcercl-root` | Extracted abnormal release containing `hipster_chaos`, `sock_shop_chaos`, and `bookinfo_chaos` |
| `--output` | A separate writable directory for all intermediate and generated results |

The following example uses repository-relative data locations. Replace the three
data arguments with the actual extracted dataset roots. Paths containing spaces
must be quoted by the shell.

```bash
python code/ls_baro/tools/reproduce_raw.py --lsdb-root data/lsdb --aiops2025-root data/AgenticOpsEval/AIOps2025 --microcercl-root data/abnormal_20240615/abnormal --output reproduced/raw --dry-run
```

Inspect the printed ordered plan, then remove `--dry-run` to execute it. A
`run_plan.json` records the exact commands. Each stage checks its preceding
inputs before execution. Failed stages stop the run. Dataset releases must be
complete: the full adapters retain their frozen cohort checks, including the
882-case MicroCERCL audit and 615 directory-exact Pod cases.

## Ordered stages

1. `lsdb-native` computes scores once per case for all eight relation arms;
   `lsdb-coverage` checks raw-to-adapted entity/edge fidelity.
2. `aiops2025-native` generates the four-arm score/ranking and trace-relation
   records; `aiops2025-summary` selects the 272 native single-entity cases;
   `aiops2025-audit` checks current-arm invariants;
   `aiops2025-observation` reconstructs full entity bases and runs masking/rewires.
3. `microcercl-native` computes the original four arms for all applications;
   `microcercl-hipster` supplies the historical eight-arm cross-check;
   `microcercl-layers` derives directory-encoded references and native-layer
   rankings; `microcercl-summary` reports 615 eligible Pod cases;
   `microcercl-mechanisms` runs the original-source controls;
   `microcercl-filtered` excludes instance-aggregated Service resource scores.
4. `core-observation` computes the joint LSDB/AIOps2025 follow-up;
   `core-mechanisms` generates their eligible relation-task controls;
   `lambda-sensitivity` checks the fixed coefficient grid;
   `final-audit` derives the ten-task eligible-cohort and cluster diagnostics.

Select one dataset's native stages with `--datasets lsdb`, `--datasets aiops2025`,
or `--datasets microcercl`. Joint core stages require both LSDB and AIOps2025;
the final combined audit and lambda stage require all three datasets.

To rerun particular stages against previously generated intermediates:

```bash
python code/ls_baro/tools/reproduce_raw.py --lsdb-root data/lsdb --aiops2025-root data/AgenticOpsEval/AIOps2025 --microcercl-root data/abnormal_20240615/abnormal --output reproduced/raw --stages core-mechanisms final-audit
```

The individual stages share the `LS_BARO_OUTPUT_ROOT`, `LS_BARO_LSDB_ROOT`,
`LS_BARO_AIOPS2025_ROOT`, and `LS_BARO_MICROCERCL_ROOT` environment variables.
The orchestrator sets them for its child processes. With no overrides, data is
under the repository's `data/` and intermediates under `reproduced/raw/`.
The dataset runners also retain their own `--root`, `--dataset-root`, and
`--output`/`--out` arguments. All locations are independent of the shell's
working directory when launched through the orchestrator.

## Historical checks and caches

The LSDB native run no longer requires an unpublished older four-arm CSV.
Supply `run_ls_relation_split.py --historical-cases PATH` only when independently
checking such a prior record. Its protocol records when no historical check
was supplied. Likewise, the AIOps2025 current-arm audit accepts an optional
`--historical-deployment-dir`; omission does not claim a historical match.

Keep each experiment in its own output directory. AIOps2025 mapped-base pickle
caches are local generated intermediates, not required public inputs. Use a
fresh output directory after changing the upstream scoring or adaptation
protocol; the artifact does not redistribute pickle caches or raw telemetry.

For a fast LSDB check, run the individual entry point:

```bash
python code/ls_baro/run_ls_relation_split.py --dataset-root data/lsdb --case 1 --output reproduced/lsdb_smoke
```

For a limited MicroCERCL check:

```bash
python code/ls_baro/tools/audit_microcercl.py --root data/abnormal_20240615/abnormal --run --limit 1 --output reproduced/microcercl_smoke
```

These smoke outputs are not complete-cohort results and must not be substituted
for the frozen evidence.

## AIOps2025 download helper

The PowerShell helper operates on an existing checkout of the upstream release
with Git LFS installed. It accepts its repository and log locations explicitly,
uses `git` from PATH by default, pulls the AIOps2025 LFS files, and verifies all
18 archives against the release MD5 list. Extract the archives according to the
upstream release layout before running the adapters.

```powershell
powershell -File code/ls_baro/tools/download_aiops2025.ps1 -Repository data/AgenticOpsEval -LogDirectory reproduced/download_logs
```

## Processed-evidence replay

Raw regeneration and the bundled processed-evidence audit are separate entry
points. Without downloading raw data:

```bash
python code/ls_baro/tools/replay_mechanism_controls.py
python code/ls_baro/tools/evaluate_mechanism_holm.py
python code/ls_baro/tools/audit_final_requirements.py
python code/ls_baro/tools/audit_extended_dataset_roles.py
```

They write to separate subdirectories of `reproduced/`. Reference outputs are
under `results/core_evidence/` and `results/extended_roles/`. Bootstrap mechanism
labels remain exploratory; the global 50-test Holm sensitivity is reported
separately.
