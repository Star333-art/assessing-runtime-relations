# Public dataset screening for LSDB transfer

Screening date: 2026-09-30

## Decision on candidate 2

Candidate: **LEMMA-RCA Product Review (preprocessed)**

Decision: **reject for the LSDB relationship-transfer experiment**. Do not add
an adapter and do not report transfer ablations for this dataset.

The published files do provide metric time series, but the public contract does
not provide all three inputs required by this project in a machine-readable,
case-aligned form:

| Required input | Finding | Decision |
| --- | --- | --- |
| Metric time series | Present as four date-based ZIP archives under `Metrics Data`; the archives contain pod-oriented preprocessed pickle payloads. | Pass |
| Host/Node -> Pod -> Service ownership | The publisher describes node-level and pod-level data, but does not publish a per-case ownership table or deployment snapshot that joins every pod to both its node and service. Service names can be inferred for some pods, but inference is not an observed ownership relation. | Fail |
| Root-cause label and time window | The publisher's own usage guide says the root cause is stored in a `readme.pptx` inside each archive (for example, `20211203` is `MongoDB-v1`), rather than a stable CSV/JSON label contract. | Fail |

This dataset can support pod-level RCA, but fabricating the missing hierarchy
would make the Containment and Full arms incomparable with LSDB. The Adapter
gate therefore stops before implementation.

Publisher references:

- <https://huggingface.co/datasets/Lemma-RCA-NEC/Product_Review_Preprocessed/tree/main>
- <https://github.com/KnowledgeDiscovery/rca_baselines>

## Replacement audit result

Candidate: **AIOps 2022 (HipsterShop), `training_data_with_faults`**

Decision: **pass the Adapter gate for the second cross-layer public-data
validation**. This decision is based on the downloaded files, not only the
dataset description.

The archive checksum is MD5 `A468E018A256EBC5793E4C0D84F0C010`. A read-only
audit covered all seven dated cloudbed subsets and all 300 fault labels. Every
subset contains 6 nodes, 40 pods, 10 services, metric time series at all three
levels, and trace data. All labels resolve to an entity at the declared level
and fall inside that level's metric time range.

The container identifier has the form `node.pod`, giving an unambiguous
Pod-to-Node edge. Pod-to-Service is recovered deterministically from the
published workload and protocol suffix conventions; the audit found zero
malformed identifiers, ambiguous mappings, or pods assigned to multiple nodes.
No pickle payload is required. A 500,000-row trace probe recovered 14 observed
non-self Service-to-Service edges, so the dependency arm also has real graph
evidence.

Machine-readable evidence is stored in
`output/dataset_audit/aiops2022_structural_audit.json`; the reproducible checker
is archived at `wasted/tools/audit_aiops2022_candidate.py`. This structural
screening utility is retained for provenance but is not part of the current
paper reproduction path.

Important caveats before integration:

1. The currently accessible Zenodo copy is a community re-upload because the
   original challenge download was removed. Licensing/provenance should be
   clarified before redistributing the files.
2. This pass establishes structural feasibility only. RCA accuracy and transfer
   performance remain to be measured after implementing the Adapter.
3. Keep the LSDB parameters frozen for the first run; do not tune against these
   300 labels before reporting the four-arm result.

References:

- <https://zenodo.org/records/19176851>
- <https://github.com/LLM4AIOps/RCLAgent-V2>

If provenance or mapping coverage fails, use **OpenRCA Market** as the fallback.
Its official documentation explicitly defines node-qualified pod identifiers
such as `node-5.adservice-2`, service grouping, metric telemetry, and structured
root-cause records. It is less convenient for a first transfer run because the
complete download is much larger (the project recommends at least 80 GB of
storage).

- <https://github.com/microsoft/OpenRCA>

## Frozen LSDB anchor for the first valid transfer run

Reuse the already executed LSDB anchor in
`output/scheme_c_l0p1_bidirectional/policy.json` without tuning on the target
dataset:

- BARO scoring: `baro_dataframe_equivalent`
- metric policy: `exclude_last_seen`
- mapping policy: `most_specific`
- score normalization: `raw`
- entity aggregation: `max`
- edge normalization: `row`
- propagation lambda: `0.1`
- direction: `bidirectional`
- four arms: Mapping-only, Containment-only, Dependency-only, Full

The Adapter gate has now passed. The next step is to implement the AIOps 2022
Adapter and run the same four arms with this policy unchanged.
