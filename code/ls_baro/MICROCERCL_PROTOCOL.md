# MicroCERCL reproducible protocol

The fixed scorer is BARO DataFrame-equivalent signed maximum RobustScaler with
constant-window filtering, `exclude_last_seen`, most-specific metric binding,
raw/max entity aggregation, lambda 0.1, one-step bidirectional propagation and
source-row normalization. Every arm shares the scored values and candidates.

The public injection timestamp is converted by the adapter's fixed eight-hour
offset. Inclusive baseline timestamps are `[trigger-300, trigger-1]`; inclusive
detection timestamps are `[trigger, trigger+299]`. The trigger belongs only to
the detection window. These are known-onset experiments. The target layer is
specified by the experimental design; root entity identity is evaluation-only.

All monitored namespaces are retained. `node.csv` supplies Host measurements,
`instance.csv` supplies Pod resource measurements, and native Service files
supply Service measurements. No cross-file deduplication or unit conversion is
introduced. `resource.csv`, `call.csv` and `instances_num.csv` remain outside the
frozen scoring scope. Nonfinite samples are omitted and audited; duplicate
timestamps fail rather than being silently merged.

Topology is the latest per-namespace snapshot at or before injection. Deployment
requires known endpoints. Ownership is derived by matching known service names
after removing Kubernetes instance suffixes; it is not a native observed
Ownership relation. Unknown endpoints and unmatched relations are reported and
excluded from propagation.

## Cases and source sensitivity

The full adapter audit contains 882 cases across three applications. The main
native-layer scope is 615 directory-exact Pod cases: 269 Hipster cases from 18
Pod identities and 346 SockShop cases from 12 Pod identities. No separate Host
root is inferred from a Pod placement. Auxiliary Service attribution and
Bookinfo derived references are reported separately.

The release defines `svc_metric.csv` resource measurements as instance averages.
The filtered Ownership analysis removes every scored mapping from that file,
retaining Service `latency.csv`, `success_rate.csv` and `svc_qps.csv` measurements.
Retained metric scores, entities, candidates, relations and windows stay fixed.
Deployment uses the original source set. Both directions have 100 within-case
source permutations and 20 typed degree-preserving rewires per eligible case.

## Execution

From the repository root, first inspect the ordered plan:

```bash
python code/ls_baro/tools/reproduce_raw.py --datasets microcercl --microcercl-root data/abnormal_20240615/abnormal --output reproduced/raw --dry-run
```

Remove `--dry-run` for the complete run. The stages generate the native four-arm
records, Hipster cross-check, layer references, 615-case summaries, original
mechanism controls and shared-source-excluded Ownership controls in that order.
Use [RAW_REPRODUCTION.md](RAW_REPRODUCTION.md) for stage selection, input layout,
environment overrides and limited smoke runs. Processed-evidence replay is:

```bash
python code/ls_baro/tools/replay_mechanism_controls.py
python code/ls_baro/tools/audit_final_requirements.py
```

Pod/recorded-placement clusters do not establish independent deployments. Small
numbers of Pod identities, shared measurements, adapter-derived Ownership and
finite endpoint swaps limit interpretation. Nominal bootstrap labels are
exploratory and do not identify physical fault propagation.
