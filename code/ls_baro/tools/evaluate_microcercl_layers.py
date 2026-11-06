"""Evaluate directory-encoded MicroCERCL Pod references; replay frozen scores.

No Host root is inferred from a Pod placement. Service roots remain the original
service attribution task. Bookinfo's directory lacks an exact Pod name, so any
uniquely resolved ownership target is reported as an auxiliary derived reference.
"""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PureWindowsPath
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ls_baro.contracts import RuntimeEntity,RuntimeGraph,Relation,RCAConfig
from ls_baro.canonical_core import propagation
from ls_baro.topology import propagate_once

OUT=ROOT/'output/microcercl_layer_evaluation_20261023'
ARMS={'Mapping-only':(), 'Deployment':('deployment',), 'Ownership':('ownership',),
      'Dependency':('dependency',), 'Containment':('deployment','ownership'),
      'Deployment+Dependency':('deployment','dependency'),
      'Ownership+Dependency':('ownership','dependency'),
      'Full':('deployment','ownership','dependency')}
OLD={'mapping-only':'Mapping-only','containment-only':'Containment',
     'dependency-only':'Dependency','Full':'Full'}
NAMESPACE={'bookinfo_chaos':'bookinfo','hipster_chaos':'hipster','sock_shop_chaos':'cloud-sock-shop'}

def rank_metrics(ids,truth,final,names):
    ordered=sorted(ids,key=lambda i:(-final[i],names[i],i))
    rank=ordered.index(truth)+1
    positions=[j+1 for j,i in enumerate(ordered) if final[i]==final[truth]]
    return dict(rank=rank,top1=int(rank<=1),top3=int(rank<=3),top5=int(rank<=5),
        avg5=sum(rank<=k for k in range(1,6))/5,mrr=1/rank,
        tie_size=len(positions),fractional_top1=sum(p<=1 for p in positions)/len(positions),
        fractional_top3=sum(p<=3 for p in positions)/len(positions),
        fractional_top5=sum(p<=5 for p in positions)/len(positions))

def main():
    OUT.mkdir(exist_ok=True)
    rows=[];audits=[];replayed={};validation_count=0;max_error=0.0
    source=ROOT/'output/microcercl_full/results.jsonl'
    compact=pd.read_csv(ROOT/'output/microcercl_hipster_relation_split_l0p1/relation_cases.csv')
    hip_checks=0
    with source.open(encoding='utf-8') as f:
        for line in f:
            d=json.loads(line);cid=d['case_id'];r=d['result'];oldarm=OLD[d['arm']]
            if oldarm=='Mapping-only':
                assert cid not in replayed
                levels=['host','pod','service'];base=r['base_scores']
                entities=tuple(RuntimeEntity(e['id'],level,e['key']) for level in levels for e in r['rankings'][level])
                names={e.id:e.name for e in entities}
                graph=RuntimeGraph(entities,tuple(Relation(**x) for x in r['runtime_topology']),(),
                    'MicroCERCL','frozen-result-replay')
                assert set(base)==set(names)
                finals={}
                for arm,kinds in ARMS.items():
                    config=RCAConfig(relation_groups=('mapping',*kinds),propagation_lambda=.1,
                        direction='bidirectional',edge_normalization='row')
                    edges,_=propagation(graph,config)
                    finals[arm],_=propagate_once(base,edges,.1)
                replayed[cid]=finals
                parts=PureWindowsPath(cid).parts;app,service,dirname=parts
                service_id=r['rankings']['service'][int(d['service_rank'])-1]['id']
                ns=NAMESPACE[app];pods=[e for e in entities if e.kind=='pod']
                m=re.fullmatch(r'.+_pod_(.+)_(cpu|mem|net)_(\d+)',dirname)
                payload=m.group(1) if m else ''
                # Native directory stores '<deployment-name>_<exact-pod-name>'.
                # The deployment name can include an opaque suffix absent from
                # the parent service label (notably SockShop).
                exact_name=payload.split('_',1)[1] if '_' in payload else None
                if exact_name is not None and not re.fullmatch(r'[a-z0-9][a-z0-9.-]*',exact_name):
                    raise AssertionError('Invalid directory Pod reference: '+dirname)
                exact=[e.id for e in pods if e.name==ns+'/'+str(exact_name)] if exact_name else []
                owned=[x.target for x in graph.relations if x.kind=='ownership' and x.source==service_id]
                derived=list(set(owned)) if payload==service else []
                mode='directory_exact' if len(exact)==1 else ('unique_ownership_derived' if len(derived)==1 else 'unresolved')
                pod_id=exact[0] if len(exact)==1 else (derived[0] if len(derived)==1 else None)
                placements=[x.source for x in graph.relations if x.kind=='deployment' and x.target==pod_id]
                audit=dict(case_id=cid,application=app,service_reference=names[service_id],
                    directory_payload=payload,explicit_pod_name=exact_name,
                    pod_reference=names.get(pod_id),pod_reference_mode=mode,
                    exact_pod_matches=len(exact),ownership_consistent=pod_id in owned,
                    deployment_hosts=json.dumps([names[i] for i in placements]),
                    host_root_available=False,host_placement_is_not_root_label=True)
                audits.append(audit)
                # Host attribution is a different diagnostic task: identify the
                # baseline host carrying the directory-referenced faulty Pod.
                # It never turns that host into an independently injected cause.
                unique_hosts=list(set(placements))
                host_id=unique_hosts[0] if mode=='directory_exact' and len(unique_hosts)==1 else None
                for level,truth,reference in [('service',service_id,'original_service_attribution'),
                    ('pod',pod_id,mode),('host',host_id,'baseline_deployment_attribution')]:
                    if truth is None:continue
                    ids=[e.id for e in entities if e.kind==level]
                    for arm,final in finals.items():
                        stats=rank_metrics(ids,truth,final,names)
                        rows.append(dict(case_id=cid,application=app,level=level,reference=reference,
                            arm=arm,candidates=len(ids),root_base=base[truth],root_final=final[truth],**stats))
                        if app=='hipster_chaos' and level=='service':
                            old=compact[(compact.case_id==cid)&(compact.arm==arm)].iloc[0]
                            assert stats['rank']==old.service_rank,(cid,arm,stats['rank'],old.service_rank)
                            hip_checks+=1
                if len(audits)%100==0:print('Replayed cases',len(audits),flush=True)
            final=replayed[cid][oldarm]
            assert set(final)==set(r['final_scores'])
            error=max(abs(final[i]-r['final_scores'][i]) for i in final)
            max_error=max(max_error,error)
            assert all(np.isclose(final[i],r['final_scores'][i],rtol=1e-12,atol=1e-10) for i in final)
            validation_count+=1
    df=pd.DataFrame(rows);audit=pd.DataFrame(audits)
    assert len(audit)==882
    df.to_csv(OUT/'case_results.csv',index=False);audit.to_csv(OUT/'label_audit.csv',index=False)
    summaries=[]
    for app in ['all',*sorted(audit.application.unique())]:
        sub=df if app=='all' else df[df.application==app]
        for (level,reference,arm),g in sub.groupby(['level','reference','arm']):
            summaries.append(dict(application=app,level=level,reference=reference,arm=arm,
                cases=len(g),**{c:g[c].mean() for c in ['top1','top3','top5','avg5','mrr',
                    'fractional_top1','fractional_top3','fractional_top5']}))
    summary=pd.DataFrame(summaries);summary.to_csv(OUT/'summary.csv',index=False)
    modes=audit.set_index('case_id').pod_reference_mode
    matched=df[df.case_id.map(modes)=='directory_exact']
    matched_summary=matched.groupby(['application','level','reference','arm']).agg(
        cases=('case_id','size'),top1=('top1','mean'),top3=('top3','mean'),top5=('top5','mean'),
        avg5=('avg5','mean'),mrr=('mrr','mean')).reset_index()
    all_matched=matched.groupby(['level','reference','arm']).agg(
        cases=('case_id','size'),top1=('top1','mean'),top3=('top3','mean'),top5=('top5','mean'),
        avg5=('avg5','mean'),mrr=('mrr','mean')).reset_index()
    all_matched.insert(0,'application','all')
    pd.concat([all_matched,matched_summary]).to_csv(OUT/'matched_directory_reference_summary.csv',index=False)
    verification=dict(cases=882,historical_four_arm_score_comparisons=validation_count,
        max_abs_score_error=max_error,hipster_eight_arm_service_rank_checks=hip_checks,
        reference_modes=dict(Counter(audit.pod_reference_mode)),
        independent_host_root_labels=0,formula_changed=False,preprocessing_changed=False,
        source=str(source),source_bytes=source.stat().st_size,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (OUT/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    print(json.dumps(verification,indent=2))
    print(summary[(summary.application=='all')&(summary.level=='pod')].to_string(index=False))

if __name__=='__main__':main()
