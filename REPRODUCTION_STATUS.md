# Reproduction status: 2026-11-24

Current artifact release: `v1.0.3`. Earlier release `v1.0.1` is retained unchanged
and does not contain the comparison-level Holm replay. The current release
includes the 50-test generator, complete-mask label replay, eligible-cohort
rewiring audit, explicit raw-data execution plan, and byte-level integrity tool.

## Processed evidence

The bundled case snapshots support 50 complete-mask contrasts across ten tasks.
All point differences and displayed bootstrap endpoints reproduce the current
contrast matrix. All ten mechanism categories are preserved. Alignment-dependent
recovery requires Mapping and all four controls to have positive interval lower
bounds; uncertain recovery and no recovery are separate labels.

The pinned Wilcoxon/Holm calculation uses 50 unrounded one-sided comparisons:
29 unadjusted and 16 globally Holm-adjusted p-values are below 0.05. LSDB
Ownership-to-Service and filtered MicroCERCL Ownership-to-Pod each retain all
five comparisons. LSDB Ownership-to-Pod retains none after global adjustment;
its bootstrap classification is exploratory. Pinned versions and input hashes
are recorded in `results/core_evidence/mechanism_holm/`.

The audit under `results/core_evidence/final_requirements/` uses the same
task-specific eligible IDs as the mechanism contrasts and exactly 20 rewires
per eligible case. Genuine zero-swap replicates remain included; no-edge
placeholder rows do not count as rewires. The AIOps2025 eligible totals are
D→Host 81/1620, D→Pod 68/1360, O→Pod 62/1240, O→Service 100/2000
(cases/replicates). MicroCERCL Deployment and Ownership each have 615/12300.
Full-cohort diagnostics are separately named `full_cohort_*.csv`.

Pod/placement bootstrap seed is 20261106 with 5000 case-weighted cluster
resamples. Those sensitivity intervals are unchanged by cohort-audit cleanup.
Neither grouping establishes deployment independence.

## Time boundaries and controls

- MicroCERCL baseline `[trigger-300, trigger-1]`, detection `[trigger, trigger+299]`.
- AIOps2025 baseline `[onset-600, onset)`, detection `[onset, onset+600)`.
- LSDB baseline from released series start through `trigger-1`, detection from
  `trigger` through released series end, with inclusive stored endpoints.
- The trigger belongs only to detection. Masking changes direct entity bases
  after scoring; known target layer and candidate restrictions are explicit.
- Controls use 100 source permutations and 20 rewires per eligible case;
  requested masking fractions use ten deterministic repeats. Seeds and ordering
  remain defined by the original generators.

## Validation and limits

The core regression suite has 48 passing tests; RE2 support has 12. RE2 frozen
replay reconstructs 450 case-component records with numerical difference zero.
AIOps2022 and RE2 paired summaries reproduce the committed reference numbers.
RE2 still supports frozen-rank replay, not historical raw graph regeneration.

The revised raw entry point provides explicit data/output locations and ordered
intermediate dependencies; historical CSV validation is optional for a fresh
LSDB run. Smoke checks and processed-evidence replay do not constitute a new
full raw-telemetry rerun of every dataset. Follow
`code/ls_baro/RAW_REPRODUCTION.md` for the complete commands and input requirements.
Original third-party telemetry remains separately distributed.

Text distribution uses LF endings. `MANIFEST_SHA256.csv` records actual release
bytes, and `artifact_manifest.py` verifies checkouts, archives, or committed Git
blobs. The artifact excludes obsolete screening plans, bibliography/layout
checks, duplicate replay outputs and machine-specific paths. Required evidence
and protocols use public file identities, as described in `provenance/README.md`.
RE2 replay writes only to a separate output directory; the frozen inputs and
their manifest hashes remain unchanged after replay.
