# MicroCERCL reproducible run

Data root: D:/AIOps/data/abnormal_20240615/abnormal

Run with D:/anaconda3/python.exe tools/audit_microcercl.py --run --output output/microcercl_full.
Without --run, only audit is performed. --limit N and --application sock_shop_chaos restrict a smoke run.

Policy frozen before the full run: BARO dataframe-equivalent, raw/max, most_specific, lambda=0.1, bidirectional, row-normalized; four arms share cached native metric scores. Root labels never contribute to graph or scoring. Public injection times minus 8 hours determine five-minute baseline and detection windows; this is an evaluation convention, not deployable anomaly detection.

Scope is ALL monitored namespaces, including other applications under the same injection. Do not count namespace metric folders as independent failures. Node metrics have direct host owners; instance metrics have pod owners (Kubernetes instance names); native service metrics have service owners. No cross-file metric deduplication or unit conversion is performed. Namespace aggregate resource.csv, edge-specific call.csv and instances_num.csv are not scored in this version; their semantics require separate policy. Therefore this is a declared supported-metric experiment, not all-modality replication of MicroCERCL.

Topology is the latest per-namespace graph snapshot at or before injection. Deployment rows are accepted only when both endpoints have known native identities. Ownership is DERIVED from an exact known service/deployment name after removing Kubernetes ReplicaSet/pod suffix, not directly observed ownership. Unknown endpoints and unmatched ownership are retained in audit/metadata and excluded from propagation; check their coverage before paper claims.

CSV rows are sorted by timestamp; duplicate timestamps fail rather than being silently merged. Non-numeric/non-finite observations are omitted and counted. BARO constant-window metrics are audited. Uniquely matched service labels alone enter quantitative evaluation; ambiguous labels are excluded from summaries with root_match_count reported. No Host or Pod accuracy is claimed from service-directory labels.

Artifacts: audit.jsonl (all accepted/excluded cases), results.jsonl (four-arm rankings/scores/contributions/run configuration), summary.json (per-application aggregate metrics). Results are exploratory until time alignment, label aliases and dependency-edge coverage have been reviewed. Existing AIOps negative results remain part of the study.

## Metric-source audit and shared-source sensitivity

The public MicroCERCL documentation defines `instance.csv` as per-instance CPU,
memory and network measurements, while `svc_metric.csv` contains service
resource measurements formed by averaging the service's instances.  Therefore
the latter is not an independent source from Pod/instance resource metrics.
The remaining scored Service files are `latency.csv`, `success_rate.csv`, and
`svc_qps.csv`; Host measurements are read from `node.csv`.  `call.csv`,
`resource.csv`, and `instances_num.csv` remain outside the frozen scoring scope.

Run the predeclared exclusion sensitivity with:

```
D:/anaconda3/python.exe tools/evaluate_microcercl_shared_source_sensitivity.py
```

The script replays the 615 directory-exact Pod cases, removes every scored
mapping whose provenance is `svc_metric.csv`, recomputes the signed-maximum
entity bases from the retained metric scores, and reruns Ownership against
Mapping, source-score permutation, uniform-source, incident-degree, and typed
degree-preserving rewiring controls.  Entities, candidate sets, relations,
labels, retained metric scores, one-step propagation, lambda, and the
tie-expected evaluation are frozen.  Outputs are written to
`output/microcercl_shared_source_sensitivity_20261103/`.

This sensitivity removes the documented instance-average resource source.  It
does not establish statistical independence between retained Service series
and Pod behavior, nor does alignment-dependent recovery identify causal fault
propagation.
