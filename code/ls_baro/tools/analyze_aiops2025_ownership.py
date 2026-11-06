"""Audit the unchanged-formula four-arm ownership experiment."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('D:/AIOps/LS-baro/output/aiops2025_ownership_pilot')
OLD=Path('D:/AIOps/LS-baro/output/aiops2025_deployment_pilot')
DATA=Path('D:/AIOps/data/AgenticOpsEval/AIOps2025')

def main():
    a=pd.read_csv(ROOT/'case_audit.csv').fillna(0)
    r=pd.read_csv(ROOT/'case_results.csv')
    k=pd.read_csv(ROOT/'rankings.csv')
    own=pd.read_csv(ROOT/'ownership_evidence.csv')
    dep=pd.read_csv(ROOT/'deployment_evidence.csv')
    assert len(a)==400 and len(r)==1600 and not r.duplicated(['case_id','arm']).any()
    keys=['case_id','arm','level','entity']
    old=pd.read_csv(OLD/'rankings.csv').sort_values(keys).reset_index(drop=True)
    new=k[k.arm.isin(['mapping','deployment'])].sort_values(keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(old.drop(columns=['base','score']),new.drop(columns=['base','score']),check_exact=True)
    max_reproduction_error=float(np.max(np.abs(old[['base','score']].to_numpy()-new[['base','score']].to_numpy())))
    np.testing.assert_allclose(old[['base','score']],new[['base','score']],rtol=1e-12,atol=1e-10)
    base=k.pivot(index=['case_id','level','entity'],columns='arm',values='base')
    assert all((base[c]==base.mapping).all() for c in base)
    counts=r.pivot(index='case_id',columns='arm',values='candidate_count')
    assert all((counts[c]==counts.mapping).all() for c in counts)
    hosts=k[k.level=='host'].pivot(index=['case_id','entity'],columns='arm',values='score')
    assert (hosts.mapping==hosts.ownership).all()
    services=k[k.level=='service'].pivot(index=['case_id','entity'],columns='arm',values='score')
    assert (services.mapping==services.deployment).all()
    service_messages=services.subtract(services.mapping,axis=0)
    half_error=float((service_messages.containment-.5*service_messages.ownership).abs().max())
    assert np.allclose(service_messages.containment,.5*service_messages.ownership,rtol=1e-10,atol=1e-8)
    labels={x['uuid']:x for x in map(json.loads,(DATA/'groundtruth.jsonl').read_text(encoding='utf-8').splitlines())}
    for edges in [own,dep]:
        onset=edges.case_id.map(lambda c:pd.Timestamp(labels[c]['start_time']).timestamp())
        assert (edges.observed_at<onset).all() and (edges.observed_at>=onset-600).all()
        assert not edges.duplicated(['case_id','pod']).any()
    covered_ids=set(a.loc[(a.target_observed>0)&a.window_within_day.astype(bool),'case_id'])
    covered=r[r.case_id.isin(covered_ids)].copy()
    summary=covered.groupby(['level','arm']).agg(cases=('case_id','size'),
        top1=('top1','mean'),top3=('top3','mean'),top5=('top5','mean'),mrr=('mrr','mean')).reset_index()
    summary.to_csv(ROOT/'covered_summary.csv',index=False)
    changes=[]
    for level,g in covered.groupby('level'):
        ranks=g.pivot(index='case_id',columns='arm',values='rank').fillna(np.inf)
        for arm in ['deployment','ownership','containment']:
            changes.append(dict(level=level,arm=arm,cases=len(ranks),
                improved=int((ranks[arm]<ranks.mapping).sum()),
                unchanged=int((ranks[arm]==ranks.mapping).sum()),
                worsened=int((ranks[arm]>ranks.mapping).sum())))
    pd.DataFrame(changes).to_csv(ROOT/'rank_changes.csv',index=False)
    coverage=[]
    for level,g in a.groupby('level'):
        coverage.append(dict(level=level,cases=len(g),cases_with_ownership=int((g.ownership_edges>0).sum()),
            mean_ownership_edges=float(g.ownership_edges.mean()),
            mean_pod_candidates=float(g.pods.mean()),mean_deployment_edges=float(g.deployment_edges.mean()),
            ambiguous_ownership_cases=int((g.ambiguous_ownership>0).sum())))
    pd.DataFrame(coverage).to_csv(ROOT/'ownership_coverage.csv',index=False)
    # Roots connected by an observed ownership edge: diagnostic cohort only.
    connected={}
    for cid,g in own.groupby('case_id'):
        label=labels[cid]; targets=label['instance'] if isinstance(label['instance'],list) else [label['instance']]
        names=set(g.service) if label['instance_type']=='service' else set(g.pod) if label['instance_type']=='pod' else set()
        connected[cid]=bool(set(targets)&names)
    rc=covered[covered.case_id.map(connected).fillna(False).astype(bool)]
    rc.groupby(['level','arm']).agg(cases=('case_id','size'),top1=('top1','mean'),
        top3=('top3','mean'),top5=('top5','mean')).to_csv(ROOT/'root_connected_summary.csv')
    paths=[Path('D:/AIOps/LS-baro')/s for s in ['tools/evaluate_aiops2025_ownership.py',
        'tools/analyze_aiops2025_ownership.py','src/ls_baro/scoring.py',
        'src/ls_baro/canonical_core.py','src/ls_baro/topology.py']]
    old_prov=json.loads((OLD/'provenance.json').read_text())
    for path in paths[2:]:
        assert hashlib.sha256(path.read_bytes()).hexdigest()==old_prov['sha256'][str(path)]
    verify=dict(cases=400,arms=4,covered_cases=len(covered_ids),
        mapping_deployment_ranks_exactly_reproduced=True,maximum_csv_score_difference=max_reproduction_error,
        reproduction_rtol=1e-12,reproduction_atol=1e-10,score_and_propagation_source_unchanged=True,
        candidate_sets_identical=True,base_scores_identical=True,baseline_only_edges=True,
        ownership_host_invariance=True,deployment_service_invariance=True,
        containment_service_message_equals_half_ownership=True,half_message_maximum_residual=half_error,
        lambda_value=.1,direction='bidirectional',normalization='row',
        sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    (ROOT/'verification.json').write_text(json.dumps(verify,indent=2),encoding='utf-8')
    report=['# AIOps 2025 Ownership relation pilot',
        'Original formula and parameters unchanged: signed BARO maximum RobustScaler; raw scores; max entity aggregation; lambda 0.1; one immutable-base-score pass; bidirectional; selected outgoing-degree row normalization.',
        'Ownership edges are observed from pre-onset trace process.serviceName and process.tags[name], with namespace hipstershop. Latest baseline mapping is retained; conflicting latest service names are omitted. No root-label ownership or Pod-name heuristic is used. Missing trace ownership remains missing; graph coverage is incomplete.',
        'All 400 cases audited. Main accuracy table covers 345 cases with at least one acceptable annotated identifier in candidate set: 82 host, 68 business Pod, 195 service cases. 55 database Pod cases remain outside the current metric adapter. Identifier lists may contain aliases, not necessarily simultaneous root causes. Rank is best among acceptable identifiers.',
        f'Mapping and Deployment ranks exactly reproduce the previous pilot. Maximum CSV score difference is {max_reproduction_error}; numerical reproduction was checked at rtol=1e-12, atol=1e-10 (CSV checkpoint round trips). Score and propagation source hashes are unchanged; candidate sets/base scores are identical across arms.',
        '## Covered-case accuracy\n\n'+summary.to_string(index=False),
        '## Rank changes relative to Mapping\n\n'+pd.DataFrame(changes).to_string(index=False),
        '## Observed ownership coverage\n\n'+pd.DataFrame(coverage).to_string(index=False),
        f'For every evaluated service score, the Containment propagation increment equals half of the Ownership increment within numerical tolerance (maximum residual {half_error}). This verifies that adding Deployment changes selected outgoing degrees and halves the Pod-to-Service messages in these observed graphs. It does not add a direct Deployment-to-Service message.',
        'No significance claim, threshold optimization, score clipping, incoming normalization, or model change is performed. This is a pilot under observed, incomplete trace ownership; results must not be generalized to complete ownership graphs.',
    ]
    (ROOT/'REPORT.md').write_text('\n\n'.join(report)+'\n',encoding='utf-8')
    print(summary.to_string(index=False))
    print(pd.DataFrame(changes).to_string(index=False))
    print(pd.DataFrame(coverage).to_string(index=False))

if __name__=='__main__':
    main()
