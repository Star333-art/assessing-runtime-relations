# Open Science Artifact

Public repository: https://github.com/Star333-art/assessing-runtime-relations
Manuscript release: `v1.0.1`

This artifact accompanies *Assessing Runtime Relations under Incomplete Entity
Observations*. It contains the active analysis code, tests, processed evidence,
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
- `provenance/`: source paths, hashes, and manuscript-quality audit records.

Third-party raw telemetry is not redistributed. The original projects and data
must be obtained from the sources listed in `THIRD_PARTY.md`.

## Environment

Python 3.10 or later is recommended.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

On Linux or macOS, replace `.venv/Scripts/python` with `.venv/bin/python`.

## Verify the active implementation

```bash
cd code/ls_baro
python -m pytest -q

cd ../re2_support
python -m pytest -q
```

Expected results in the prepared workspace are 26 passing core tests and 9
passing BARO/RE2-support tests.

## Replay the supporting RE2 result

The command below rebuilds the component case table and historical summary from
the 450 frozen case-component JSON records without loading raw RCAEval data.

```bash
cd code/re2_support
python run_heterogeneous_graph_component_ablation.py \
  --output ../../results/re2 \
  --lambda 0.05 \
  --direction forward \
  --replay-existing \
  --force
```

The replay has been checked against the original 450-row table with maximum
absolute numeric difference 0.

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
field was reciprocal rank and is not used as the paper's Avg@5 definition.

## Full raw-data experiments

The LSDB, AIOps2025, MicroCERCL, and AIOps2022 runners are included under
`code/ls_baro/`. Their dataset-specific commands and expected directory layout
are documented in `code/ls_baro/PROJECT_GUIDE.md`. Raw datasets are excluded
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

