# LS-baro

The current paper workflow, active-script inventory, dataset roles, and archive
policy are documented in [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md). Scripts that
belong only to superseded or exploratory workflows have been moved to
[`wasted/`](wasted/) and are not imported by the active paper pipeline.

LatentScope Dataset B external validation for the document's lightweight method:

```text
BARO RobustScorer
  -> Metric-to-Entity Mapping
  -> one-hop Topology Propagation
  -> Host / Pod / Service / Metric Top-K
  -> auditable impact paths
```

The implementation does not train a new model. Metrics are the only anomaly
score source; LatentScope topology files only map and propagate those scores.
The calibrated default, `baro_dataframe_equivalent`, reproduces BARO's
DataFrame `RobustScaler` formula per metric. The earlier locally-added dict
adapter remains available as `legacy_dict` only for reproducing V1 outputs.

## Data used without fabrication

- `metrics.json`: original JSON metric identifiers and time series. Each key may
  contain the real `node`, `pod`, `service`, and `kpi` fields.
- `rccs.json:rccs.linked_metrics`: exact Metric-to-Host/Pod/Service ownership.
- `rccs.json:rcc_edges`: observed Host-to-Pod and Service-to-Pod containment.
- `service_deps.json`: caller-to-callee service dependency graph.
- `label.json`: public root-cause label, trigger time, and observed trigger.

Dataset B does not provide separate Physical Server, Process, or Task IDs. Those
levels are reported as unavailable instead of being inferred or invented. Its
`host` RCC is the dataset's `node` entity, and `pod` is the Container/Pod layer.

## Propagation

The implementation applies the required equation exactly once using immutable
mapped BARO scores:

```text
S_final[i] = S_BARO_mapped[i] + lambda * sum_j(w_ji * S_BARO_mapped[j])
```

Root-cause evidence uses three data-backed orientations:

1. Pod -> Host, by reversing the real Host -> Pod containment edge.
2. Pod -> Service, by reversing the real Service -> Pod containment edge.
3. Caller -> Callee, following `service_deps.json`, so a caller symptom supports
   the dependency that may have caused it. Caller fan-out is row-normalized.

No propagated value is reused during the same pass. Every edge contribution is
saved in the result JSON for audit and ablation.

Impact paths are built from graph relations and scored entities. Each path marks
whether a step is an actual propagation edge or an explanation-only edge; public
root-cause labels do not choose the path.

## Run

From `D:\AIOps\LS-baro`:

```powershell
$python = "C:\Users\Smr\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = "src"
& $python -m ls_baro.cli `
  --dataset-root ..\LatentScope\data\dataset_b `
  --baro-root ..\baro-main `
  --case 3 `
  --lambda 0.2 `
  --output output\case-3
```

Run every public Dataset B case with `--all` instead of `--case 3`.

Formal ablation controls are also available on the case runner:

```text
--topology-components mapping-only|containment|dependency|all
--direction forward|backward|bidirectional|relation-aware
--score-normalization raw|max|percentile
--entity-aggregation max|top3_mean|top5_mean|p95
--edge-normalization none|row
```

## Shared RCA contract

Dataset-specific parsing lives in `LSDBAdapter`; ranking and propagation enter
through the shared `RCAEngine.analyze(graph, observations, window, config)`
method. The runtime graph contains namespaced, stable entity IDs
(`lsdb:pod:...`), typed relations, and metric bindings. LSDB maps Host → Pod to
`deployment`, Service → Pod to `ownership`, and caller → callee to `dependency`.
`deployment` and `ownership` form the containment group. Other observed
relations can remain in the graph, but do not propagate unless their semantics
are explicitly supported.

```python
from ls_baro import LSDBAdapter, RCAConfig, RCAEngine

case = LSDBAdapter().load(dataset_root, case_id)
result = RCAEngine(baro_root=baro_root).analyze(
    graph=case.graph,
    observations=case.observations,
    window=case.window,
    config=RCAConfig(
        relation_groups=("mapping", "containment", "dependency"),
        mapping_policy="most_specific",
        propagation_lambda=0.05,
        direction="relation-aware",
        edge_normalization="row",
    ),
)
```

The four component comparisons use one graph and vary only `relation_groups`:
`("mapping",)` (no propagation), mapping plus containment, mapping plus
dependency, or all three. Labels are returned separately as
`case.evaluation`; the engine does not receive them. For this public dataset,
the injection timestamp in `label.json` defines the analysis window. The
evaluation protocol records that temporal-label use explicitly; root-cause and
trigger labels do not enter BARO scoring, entity aggregation, or propagation.

### Deployment and ownership split

The canonical engine also accepts `deployment` and `ownership` independently
in `RCAConfig.relation_groups`. `containment` remains an alias for their union;
using the alias together with explicit kinds does not duplicate evidence edges.
All entities remain in the candidate set even when their incident relations are
disabled. Source-node normalization is recomputed over selected edges, as in
the original Scheme C.

The fixed eight-arm runner evaluates Mapping-only, Deployment, Ownership,
Dependency, Containment, Deployment+Dependency, Ownership+Dependency, and Full.
It uses lambda 0.1, bidirectional propagation, row normalization, raw scores,
max aggregation, most_specific metric assignment, and exclude_last_seen.
Metric scores are computed once per case and reused across the eight arms.

```powershell
Set-Location D:\AIOps\LS-baro
& "D:\anaconda3\python.exe" .\run_ls_relation_split.py
```

Default output: `output/lsdb_relation_split_l0p1/`. `REPORT.md` gives the tables;
`relation_summary.csv` includes known-root-layer and global metrics separately;
`paired_changes.csv` and `paired_cases.csv` record improvements and regressions;
`details/<case>.json` stores final rankings, scores and typed edge contributions.
`protocol.json` records fixed configuration, source fingerprints and invariant
checks. The runner checks the original four arms against every case in
`output/scheme_c_l0p1_bidirectional/ablation_cases.csv`, including deterministic
BARO Top-N and the historical fractional tie-aware global convention.

Use `--case 1 --output output/relation_split_case1` for a single-case run.
This study does not tune parameters or train a GNN. Per-type results use the
known label type to restrict evaluation; global results rank all entity types.

After the complete 88-case run, generate the interpretation, source-weight
audit and per-layer plot using:

```powershell
& "D:\anaconda3\python.exe" .\tools\analyze_ls_relation_split.py
```

`INTERPRETATION.md` describes the observed layer-specific contributions and
tradeoffs. `normalization_audit.json` checks every Pod-to-Service ownership
weight and reconstructs Containment Service scores from half-strength
Ownership evidence. `relation_split_by_layer.png` and `.svg` visualize Host
Top-1 and Pod/Service Top-5; panels intentionally identify their different
metrics and sample sizes.

### Cross-dataset relation supplements

The supplement runner retains all 88 LSDB cases and the 299-case MicroCERCL
Hipster subset. It adds lambda 0.05/0.2 around the existing 0.1 anchor for
Ownership, Containment, Dependency and Full, and evaluates the eight relation
arms with a fixed reference-degree control. The canonical-only configuration
`RCAConfig(edge_normalization="reference_row")` computes outgoing degrees from
all supported runtime relations before masking. Shared edge weights therefore
remain fixed. Full equals the original row-normalized Full, while a masked
subgraph can have outgoing weight below one. This is a sensitivity control,
not a replacement selected to optimize target-data results.

```powershell
& "D:\anaconda3\python.exe" .\run_relation_supplements.py
& "D:\anaconda3\python.exe" .\tools\analyze_relation_supplements.py
```

Output is `output/relation_supplements_20261015/`. Per-case checkpoints include
source and input fingerprints. The runner checks all original eight-arm
positions and root scores, freezes metric scores/mappings/candidates, verifies
shared reference weights and Full equivalence, and keeps historical outputs.
Primary tables use deterministic positions for both datasets; historical LSDB
fractional-tie global scores remain separately documented.

The analysis reports paired exact McNemar results, 20,000 case bootstrap
resamples, and an injection-target cluster bootstrap sensitivity. Four main
layer-Top3 comparisons receive Holm correction; other comparisons are marked
exploratory. These are post-hoc analyses, not preregistered confirmatory tests.
Hipster was selected after the earlier three-subset outcomes were known;
keep those original results alongside the new decomposition. MicroCERCL has
Service-only root labels and name-derived ownership. Neither significance nor
edge contribution establishes causal propagation direction.

`RCAResult` includes global and per-kind rankings, base/final entity scores,
per-edge contributions, graph-based impact paths, and run metadata including
the data source, graph fingerprint, selected config, and missing relation
groups. Kinds absent from the source graph are reported unavailable rather
than synthesized.

The earlier V1/V2 staged ablation runner, its parameter scan, and the pre-v1
implementation snapshot are not part of the current paper evidence chain. They
are preserved under `wasted/root/` and `wasted/legacy/` for historical recovery.
The current LSDB evidence is reproduced with `run_ls_relation_split.py` and the
controlled mechanism scripts listed in `PROJECT_GUIDE.md`.

## Verify

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```

## AIOps 2022 containment transfer

The external transfer runner consumes the extracted
`training_data_with_faults` directory through `AIOps2022Adapter` and enters the
same `RCAEngine` interface as LSDB. It fixes the LSDB anchor without target-data
tuning: BARO dataframe-equivalent scoring, `exclude_last_seen`, most-specific
mapping, raw/max aggregation, row-normalized bidirectional propagation. The
four matched arms are Mapping-only (lambda 0.0), Containment-only (lambda 0.1),
Dependency-only (lambda 0.1), and Full (lambda 0.1).

Each public ground-truth row becomes one evaluation case. The protocol uses a
fixed 600-second window before and after every injection and evaluates all 300
labels. No target-dependent filtering or secondary sensitivity experiment is
performed.

Extract the inner fault archive first (the runner intentionally does not unpack
or start a long job automatically):

```powershell
$faultRoot = "D:\AIOps\data\aiops2022\faults"
New-Item -ItemType Directory -Force $faultRoot | Out-Null
tar.exe -xf "D:\AIOps\data\aiops2022\audit\training_data_with_faults.zip" `
  -C $faultRoot
```

Run the four arms over all 300 labels:

```powershell
python .\run_aiops2022_ablation.py `
  --dataset-root "D:\AIOps\data\aiops2022\faults" `
  --output "output\aiops2022-containment-transfer"
```

The runner checkpoints after every fault and resumes by default. `summary.json`
reports overall and per-cloudbed Top-1/3/5, MRR, paired rank outcomes, and the
two containment contrasts: Containment-only minus Mapping-only, and Full minus
Dependency-only. `cases.csv` contains the paired case-level measurements.

For an AIOps-native follow-up (separate from the frozen LSDB transfer), use
`run_aiops2022_native.py`. It keeps only labels with no neighboring injection
within 600 seconds and uses relation-aware containment, so Pod evidence flows
to its Node and owning Service without pushing parent-level noise back to Pods.
The same four arms and per-arm lambdas are retained.

```powershell
python .\run_aiops2022_native.py `
  --dataset-root "D:\AIOps\data\aiops2022\faults" `
  --subset "2022-03-21-cloudbed2" `
  --output "output\aiops2022-native-20220321-cloudbed2" `
  --no-resume
```
