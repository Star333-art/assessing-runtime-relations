# Reproduction status: 2026-11-06

Local review revision: requirements-audit-20261106. Public repository: https://github.com/Star333-art/assessing-runtime-relations. The manuscript is pinned to release `v1.0.1`; the release tag must remain attached to the artifact commit when the repository is updated.

## Windows and randomness

- MicroCERCL: baseline [trigger-300, trigger-1], detection [trigger, trigger+299], inclusive timestamps, as implemented in microcercl_adapter.py.
- AIOps2025: 600 seconds before and after injection; consult the frozen runner configuration for timestamp boundary conventions.
- LSDB: released case-series start to trigger-1 for baseline, trigger to released series end for detection; no invented common duration.
- Complete controls: 100 source permutations and 20 rewires per case; masking uses ten deterministic repeats. Existing generators derive seeds through SHA-256 keys; preserve the generator source and protocol together.
- New Pod/placement cluster sensitivity: seed 20261106, 5,000 resamples, case-weighted cluster sums divided by resampled cluster sizes. Groups are application-specific.

## Added evidence

evidence/final_requirements contains matched case IDs, Pod references, recorded host-placement groups, all five paired cluster contrasts, leave-one-group-out ranges, and actual swap-count summaries. Host placement is not proof of independent deployment.

RE2 supports replay of frozen ranks only, not raw-telemetry regeneration or reconstructed tie expectations. Raw datasets remain separately distributed by their original providers.

The original reference_audit.md describes an earlier bibliography. The current bibliography has 27 entries. Added entries must not be represented as covered by the older Crossref verification record without checking their verification provenance.
