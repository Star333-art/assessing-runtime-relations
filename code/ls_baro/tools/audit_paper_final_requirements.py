"""Replay dependence and rewiring diagnostics from saved experimental records."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT.parent / 'paper/JSS_20261030/JSS_entity_evidence_20261026'
OUT = PAPER / 'evidence/final_requirements'
OUT.mkdir(parents=True, exist_ok=True)
cases = pd.read_csv(PAPER / 'evidence/microcercl_shared_source_case_results.csv')
labels = pd.read_csv(ROOT / 'output/microcercl_layer_evaluation_20261023/label_audit.csv')
print('Conditions:', sorted(cases.condition.unique()))
wide = cases.pivot_table(index=['application', 'case_id'], columns='condition', values='tie_avg5').reset_index()
wide = wide.merge(labels[['case_id', 'pod_reference']], on='case_id', validate='one_to_one')
wide.to_csv(OUT / 'microcercl_matched_cases.csv', index=False)
real = 'real' if 'real' in wide else 'masked_real'
if real not in wide:
    raise ValueError(f'Inspect condition names: {list(wide.columns)}')
rows = []
for app, frame in wide.groupby('application'):
    groups = list(frame.groupby('pod_reference'))
    for control in [c for c in cases.condition.unique() if c != real and not c.startswith('native')]:
        sums = np.array([(g[real]-g[control]).sum() for _, g in groups])
        counts = np.array([len(g) for _, g in groups])
        rng = np.random.default_rng(20261106)
        sampled = rng.integers(0, len(groups), size=(5000, len(groups)))
        boot = sums[sampled].sum(axis=1)/counts[sampled].sum(axis=1)
        leave = (sums.sum()-sums)/(counts.sum()-counts)
        rows.append(dict(application=app, control=control, cases=len(frame), pod_groups=len(groups), difference=float(sums.sum()/counts.sum()), low=float(np.quantile(boot,.025)), high=float(np.quantile(boot,.975)), leave_one_pod_min=float(leave.min()), leave_one_pod_max=float(leave.max())))
pd.DataFrame(rows).to_csv(OUT / 'pod_cluster_sensitivity.csv', index=False)
print(pd.DataFrame(rows).to_string(index=False))
rew = pd.read_csv(ROOT / 'output/microcercl_relation_mechanisms_20261028/rewiring_integrity.csv')
rew = rew[rew.relation.eq('deployment')]
rew = pd.concat([rew, pd.read_csv(PAPER / 'evidence/microcercl_rewiring_integrity.csv')], ignore_index=True)
summary = rew.groupby('relation').agg(replicates=('accepted_swaps','size'), swaps_min=('accepted_swaps','min'), swaps_median=('accepted_swaps','median'), swaps_max=('accepted_swaps','max'), mean_changed=('fraction_edges_changed','mean'))
summary['mean_retained'] = 1-summary.mean_changed
summary.to_csv(OUT / 'microcercl_swap_summary.csv')
print(summary.to_string())
(OUT / 'protocol.json').write_text(json.dumps(dict(seed=20261106, repeats=5000, cluster='native Pod identity within application', weighting='case-weighted ratio of resampled cluster sums to sizes', limitation='Pod clusters do not establish independent deployments; exploratory only'),indent=2),encoding='utf-8')

# Recorded host placement is a reproducible grouping, not an independent batch ID.
placed = wide.merge(labels[['case_id', 'deployment_hosts']], on='case_id', validate='one_to_one')
placed['placement_group'] = placed.deployment_hosts.map(lambda x: '|'.join(sorted(json.loads(x))))
placed.to_csv(OUT / 'placement_case_list.csv', index=False)
placement_rows = []
for app, frame in placed.groupby('application'):
    groups = list(frame.groupby('placement_group'))
    for control in [c for c in cases.condition.unique() if c != real and not c.startswith('native')]:
        sums = np.array([(g[real]-g[control]).sum() for _, g in groups])
        counts = np.array([len(g) for _, g in groups])
        sampled = np.random.default_rng(20261106).integers(0,len(groups),(5000,len(groups)))
        boot = sums[sampled].sum(1)/counts[sampled].sum(1)
        leave = (sums.sum()-sums)/(counts.sum()-counts) if len(groups)>1 else np.array([np.nan])
        placement_rows.append(dict(application=app,control=control,groups=len(groups),low=np.quantile(boot,.025),high=np.quantile(boot,.975),leave_min=leave.min(),leave_max=leave.max()))
pd.DataFrame(placement_rows).to_csv(OUT / 'placement_cluster_sensitivity.csv',index=False)
print(pd.DataFrame(placement_rows).to_string(index=False))
ls = pd.read_csv(ROOT / 'output/relation_evidence_mechanisms_20261028/rewiring_integrity.csv')
ls.groupby(['dataset','relation','root_layer']).agg(replicates=('accepted_swaps','size'),minimum=('accepted_swaps','min'),median=('accepted_swaps','median'),maximum=('accepted_swaps','max'),changed=('fraction_edges_changed','mean')).to_csv(OUT / 'lsdb_swap_counts.csv')
ai = pd.read_csv(ROOT / 'output/aiops2025_observability_rewire_20261026/rewiring_integrity.csv')
pd.DataFrame([dict(relation=r,replicates=len(ai),minimum=ai[r+'_accepted_swaps'].min(),median=ai[r+'_accepted_swaps'].median(),maximum=ai[r+'_accepted_swaps'].max(),retained=1-ai[r+'_fraction_edges_changed'].mean()) for r in ['deployment','ownership']]).to_csv(OUT / 'aiops2025_swap_counts.csv',index=False)
