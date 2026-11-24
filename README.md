# Open Science Artifact

Public repository: https://github.com/Star333-art/assessing-runtime-relations
Artifact release: `v1.0.1`

This artifact accompanies *When Do Runtime Relations Help Localize Root Causes?
A Controlled Study of Missing Entity Scores and Structural Priors*. It contains the active analysis code, tests, processed evidence,
protocol metadata, and supporting-result replay material used by the paper.

## Scope

The package is organized around the paper's controlled question: whether a
ranking gain from a typed runtime relation depends on aligned anomaly scores or
can be reproduced by the specified score and structural controls.

- `code/ls_baro/`: the core runtime-graph implementation, dataset adapters,
  masking and control experiments, sensitivity analyses, and tests.
- `code/re2_support/`: BARO source provenance and the verified replay entry
  point for the frozen RCAEval RE2 component results.
- `results/core_evidence/`: processed evidence snapshots used by the paper.
- `results/aiops2022/`: the retained AIOps2022 boundary subset.
- `results/re2/`: 90-case, five-component frozen RE2 results and configuration.
- `results/extended_roles/`: paired summaries assigning conservative evidence
  roles to AIOps2022 and RE2.
- `provenance/`: public evidence identities, input transformations, and hashes.

Third-party raw telemetry is not redistributed. The original projects and data
must be obtained from the sources listed in `THIRD_PARTY.md`.

## Environment

Python 3.11 or later is required for the complete replay workflow.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r code/ls_baro/requirements-holm.txt
```

On Linux or macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r code/ls_baro/requirements-holm.txt
```

Run all remaining commands in that activated environment. After opening a new
shell, activate it again before using `python`.

## Replay multiple-comparison sensitivity

From the repository root, use Python 3.11 or later and install the pinned
analysis dependencies:

```bash
python -m pip install -r code/ls_baro/requirements-holm.txt
python code/ls_baro/tools/evaluate_mechanism_holm.py
```

The input snapshots are bundled under `results/core_evidence/`. The command
writes all 50 raw and globally Holm-adjusted p-values, per-task support counts,
and a protocol with dependency versions and SHA-256 hashes to
`reproduced/mechanism_holm/`. Frozen reference outputs and input provenance
are in `results/core_evidence/mechanism_holm/`.

The replay yields 29 unadjusted and 16 Holm-adjusted comparisons below 0.05.
LSDB Ownership-to-Service and MicroCERCL Ownership-to-Pod each retain all five
comparisons; LSDB Ownership-to-Pod retains zero. MicroCERCL Ownership excludes
instance-aggregated Service resource scores, while Deployment uses the original
source set. The analysis uses one case-level score per condition after averaging
random controls within each case. Bootstrap classifications remain exploratory.
The protocol records the explicit asymptotic signed-rank settings used for this
replay. The historical aggregate count of 12 has no corresponding 50-test
generator in the original release and is superseded by these reproducible
comparison-level results.

To validate the replay:

```bash
python -m pytest -q code/ls_baro/tests/test_mechanism_holm.py
```

## Verify the active implementation

```bash
cd code/ls_baro
python -m pytest -q

cd ../re2_support
python -m pytest -q
```

Expected results in the prepared workspace are 48 passing core tests and 12
passing BARO/RE2-support tests.

## Replay the supporting RE2 result

The command below rebuilds the component case table and historical summary from
the 450 frozen case-component JSON records without loading raw RCAEval data.

```bash
python code/re2_support/run_heterogeneous_graph_component_ablation.py --input results/re2 --output reproduced/re2 --lambda 0.05 --direction forward --replay-existing
```

Run from the repository root. The replay has been checked against the original
450-row table with maximum absolute numeric difference 0. Frozen inputs are
read-only; regenerated tables and configuration go under `reproduced/re2/`.
The runner rejects overlapping input/output locations and outputs within
`results/`, including when `--force` is provided.

The historical raw-telemetry Route-B generator is not claimed as reproducible:
its graph-builder module was absent from the final workspace. RE2 is a
supporting boundary analysis, and this package therefore provides verified
case-result replay and metric recomputation rather than an unverifiable raw
pipeline.

## Recompute the AIOps2022 and RE2 paper summaries

From the package root:

```bash
python code/ls_baro/tools/audit_extended_dataset_roles.py \
  --aiops2022-dir results/aiops2022 \
  --re2-dir results/re2 \
  --output reproduced/extended_roles
```

This step recomputes Avg@5 from each saved RE2 rank. The historical `avg5`
field was reciprocal rank truncated at rank 5 (zero beyond rank 5) and is not
used as the paper's Avg@5 definition.

## Full raw-data experiments

The LSDB, AIOps2025, MicroCERCL, and AIOps2022 runners are included under
`code/ls_baro/`. Explicit data/output arguments, ordered stages, optional
historical checks and smoke commands are documented in
[`RAW_REPRODUCTION.md`](code/ls_baro/RAW_REPRODUCTION.md). Raw datasets are excluded
because they are third-party releases and substantially larger than the code
artifact. The paper's processed evidence and protocol/hash records remain in
this package so that reported tables can be audited without redistributing the
raw releases.

## Statistical interpretation

All reported intervals are exploratory. An interval crossing zero means that
the additional advantage is not established; it does not establish equivalence.
The experiments assume a known failure time and root layer and rank candidates
within that layer. Masking is applied to post-scoring direct entity scores, not
to arbitrary raw telemetry or topology collection.

## Replay the complete mechanism and audit tables

```bash
python code/ls_baro/tools/replay_mechanism_controls.py
python code/ls_baro/tools/audit_final_requirements.py
```

The first command computes all ten task summaries, paired intervals, and labels
using the original generators and seeds. Alignment-dependent recovery requires
positive interval lower bounds against Mapping and all four controls; uncertain
and no recovery are reported separately. MicroCERCL Deployment-to-Pod retains
Real–Mapping +3.27 [2.18, 4.39] percentage points and is not separable from controls.

The second command validates exactly 20 rewires for each eligible case, including
genuine zero-swap replicates but excluding no-edge placeholders. Its ten-task
audit uses the same case IDs as the mechanism comparisons. Full-cohort relation
and observation-gradient diagnostics are saved separately as `full_cohort_*.csv`.
Reference outputs are in `results/core_evidence/mechanism_controls/` and
`results/core_evidence/final_requirements/`.

## Verify artifact integrity

Text is distributed with LF line endings on every platform. The manifest lists
repository-relative paths, byte lengths and SHA-256 hashes of every distributed
file except the manifest itself. It works in both Git checkouts and unpacked
archives:

```bash
python code/ls_baro/tools/artifact_manifest.py
```

To verify committed blob bytes directly, add `--revision HEAD`. Maintainers can
stage new files and run `--write`, then stage the regenerated manifest. Current
evidence is indexed by `provenance/evidence_index.csv`. The package contains
reproduction inputs, protocols and checksums; document-layout audits,
bibliography audits, superseded screening plans and duplicate replay outputs
are excluded.

