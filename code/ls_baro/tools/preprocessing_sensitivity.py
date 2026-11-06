"""Fixed BARO-inspired near-constant filter sensitivity. Core formula unchanged.

This isolates drop_near_constant(threshold=0.1) from BARO's optional domain
knowledge preprocessing. It is NOT the full dk_select_useful pipeline.
Adapters' missing-value policies are retained; no imputation is claimed.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote
import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ls_baro.contracts import RCAConfig,RuntimeEntity,RuntimeGraph,MetricBinding,Relation
from ls_baro.canonical_core import aggregate,propagation
from ls_baro.topology import propagate_once
from ls_baro.engine import _raw_metric_key
from ls_baro.scoring import filter_metrics,score_with_baro_dataframe_equivalent
from ls_baro.lsdb_adapter import LSDBAdapter
from ls_baro.microcercl_adapter import MicroCERCLAdapter,case_paths
from ls_baro.aiops2022_adapter import AIOps2022Adapter

ARMS={'mapping':(), 'deployment':('deployment',),'ownership':('ownership',),
      'containment':('deployment','ownership')}

def evaluate(out,dataset,case,graph,metrics,keys,trigger,level,truths,policy,expected=None):
    """Keep candidates, graphs and windows identical across preprocessing arms."""
    metrics=filter_metrics(metrics,policy)
    scores={}; kept={}; stats=Counter(total=len(metrics))
    for key,samples in metrics.items():
        pre=np.array([v for t,v in samples if t<trigger],float)
        post=np.array([v for t,v in samples if t>=trigger],float)
        pre=pre[np.isfinite(pre)];post=post[np.isfinite(post)]
        if not len(pre) or not len(post):stats['empty']+=1;continue
        if np.all(pre==pre[0]) or np.all(post==post[0]):stats['constant']+=1;continue
        scorer=RobustScaler().fit(pre.reshape(-1,1))
        score=float(np.max(scorer.transform(post.reshape(-1,1))))
        scores[key]=score
        near=(np.mean(pre!=pre[0])<=.1 or np.mean(post!=post[0])<=.1)
        zero=np.percentile(pre,75)==np.percentile(pre,25)
        stats['eligible']+=1;stats['zero_iqr']+=int(zero)
        stats['removed_near_constant']+=int(near)
        stats['removed_near_constant_zero_iqr']+=int(near and zero)
        if not near:kept[key]=score
    descriptors={}
    for key in metrics:
        try:d=json.loads(key)
        except (ValueError,TypeError):d={}
        descriptors[key]=d if isinstance(d,dict) else {}
    ids=[e.id for e in sorted(graph.entities,key=lambda e:(e.name,e.id)) if e.kind==level]
    truths=set(truths)&set(ids)
    expected=expected or {}
    for variant,ss in [('original',scores),('near_constant_0p1',kept)]:
        config=RCAConfig(relation_groups=('mapping',),metric_policy=policy,
            direction='bidirectional',edge_normalization='row')
        base,_=aggregate(graph,ss,descriptors,keys,config)
        for arm,kinds in ARMS.items():
            config=RCAConfig(relation_groups=('mapping',*kinds),propagation_lambda=.1,
                direction='bidirectional',edge_normalization='row')
            edges,_=propagation(graph,config)
            final,_=propagate_once(base,edges,.1)
            ordered=sorted(ids,key=lambda i:-final[i])
            rank=min([ordered.index(i)+1 for i in truths],default=None)
            record=dict(dataset=dataset,case_id=case,level=level,variant=variant,arm=arm,
                rank=rank,candidates=len(ids),covered=bool(truths),
                truth_scored=any(base[i]!=0 for i in truths),scored_metrics=len(ss),**stats)
            # Use the original AIOps2022 tie convention as a separate output.
            if truths:
                best=max(final[i] for i in truths)
                positions=[j+1 for j,i in enumerate(ordered) if abs(final[i]-best)<=1e-12]
                record['mean_tie_rank']=sum(positions)/len(positions)
                for k in [1,3,5]:record[f'fractional_top{k}']=sum(p<=k for p in positions)/len(positions)
            if variant=='original' and arm in expected:
                er=expected[arm]
                actual=record['mean_tie_rank'] if dataset=='AIOps2022' else rank
                if abs(actual-er)>1e-10:
                    raise AssertionError(f'Historical reproduction failed: {dataset} {case} {arm}: {actual} != {er}')
            with (out/'cases.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(record)+'\n')

def request_inputs(req):
    keys={s.metric_id:_raw_metric_key(s.metric_id) for s in req.observations}
    w=req.window
    metrics={keys[s.metric_id]:[(t,v) for t,v in zip(s.timestamps,s.values)
        if w.baseline_start<=t<=w.baseline_end or w.detection_start<=t<=w.detection_end]
        for s in req.observations}
    return metrics,keys,w.detection_start

def run(args):
    out=args.out;out.mkdir(parents=True,exist_ok=True)
    (out/'protocol.json').write_text(json.dumps(dict(
        variant='isolated BARO optional near-constant filter, NOT full dk_select_useful',
        rule='retain iff >10% of values differ from first in BOTH windows',
        threshold_source='BARO utility.drop_near_constant default=0.1',
        fitting='normal and anomalous windows separately, as BARO preprocess',
        frozen='raw signed RobustScaler/max; lambda=.1; bidirectional; selected row; immutable single pass',
        imputation='adapters unchanged; no ffill/imputation implemented',
        units='unchanged; no guessed unit conversion',
        candidate_policy='all original graph entities retained even if filtered scores absent',
        status='post-hoc sensitivity; do not replace original primary results',
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2),encoding='utf-8')
    done=set()
    if (out/'cases.jsonl').exists():
        counts=Counter((x['dataset'],x['case_id']) for x in
            (json.loads(l) for l in (out/'cases.jsonl').read_text().splitlines()))
        assert all(n==8 for n in counts.values()),'Partial case checkpoint; use a fresh output'
        done=set(counts)
    completed=0
    def process(ds,cid,req,expected):
        nonlocal completed
        if (ds,cid) in done:return
        metrics,keys,trigger=request_inputs(req)
        root=req.evaluation.root_causes[0]
        level=root.get('kind',root.get('type'))
        truths=[e.id for e in req.graph.entities if any(
            e.id==x.get('id') or (e.kind==x.get('type') and e.name==x.get('key'))
            for x in req.evaluation.root_causes)]
        evaluate(out,ds,cid,req.graph,metrics,keys,trigger,level,
            truths,'exclude_last_seen',expected)
        completed+=1
        if completed%10==0:print(ds,'completed',completed,flush=True)
    if args.dataset in ['all','lsdb']:
        historical=pd.read_csv(ROOT/'output/lsdb_relation_split_l0p1/relation_cases.csv',dtype={'case_id':str})
        adapter=LSDBAdapter()
        cases=sorted(historical.case_id.unique(),key=int)
        for cid in cases[:args.limit or None]:
            h=historical[historical.case_id==cid]
            names={'Mapping-only':'mapping','Deployment':'deployment','Ownership':'ownership','Containment':'containment'}
            expected={names[r.experiment]:r.baro_rank for r in h.itertuples() if r.experiment in names}
            process('LSDB',cid,adapter.load(ROOT.parent/'LatentScope/data/dataset_b',cid),expected)
    if args.dataset in ['all','microcercl']:
        historical=pd.read_csv(ROOT.parent/'paper/AIOps_Journal_SMR_revision/evidence/microcercl_case_ranks.csv')
        # Historical compact ranks are checked below after identifying schema.
        adapter=MicroCERCLAdapter()
        errors=[]
        for path in case_paths(ROOT.parent/'data/abnormal_20240615/abnormal'):
            cid=str(path.relative_to(ROOT.parent/'data/abnormal_20240615/abnormal'))
            if ('MicroCERCL',cid) in done:continue
            try:req=adapter.load(path)
            except ValueError as e:
                errors.append({'case_id':cid,'reason':str(e)});continue
            h=historical[historical.case_id==req.audit['case_id']]
            names={'mapping-only':'mapping','containment-only':'containment'}
            expected={names[r.arm]:r.service_rank for r in h.itertuples() if r.arm in names}
            process('MicroCERCL',req.audit['case_id'],req,expected)
            if args.limit and completed>=args.limit:break
        (out/'microcercl_exclusions.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
    if args.dataset in ['all','aiops2022']:
        adapter=AIOps2022Adapter(ROOT.parent/'data/aiops2022')
        source=ROOT/'output/aiops2022-20220321-cloudbed2'
        old=pd.read_csv(source/'cases.csv')
        for spec in adapter.cases():
            if spec.subset!='2022-03-21-cloudbed2':continue
            h=old[(old.case_id==spec.case_id)&(old.scope=='level')]
            names={'Mapping-only':'mapping','Containment-only':'containment'}
            expected={names[r.arm]:r.rank for r in h.itertuples() if r.arm in names}
            process('AIOps2022',spec.case_id,adapter.load(spec.case_id),expected)
            if args.limit and completed>=args.limit:break
    if args.dataset in ['all','aiops2025']:
        from evaluate_aiops2025_ownership import load_day,epoch
        root=ROOT.parent/'data/AgenticOpsEval/AIOps2025'
        source=ROOT/'output/aiops2025_ownership_pilot'
        labels=[json.loads(l) for l in (root/'groundtruth.jsonl').read_text(encoding='utf-8').splitlines()]
        audits=pd.read_csv(source/'case_audit.csv').set_index('case_id')
        old=pd.read_csv(source/'case_results.csv')
        de=pd.read_csv(source/'deployment_evidence.csv');oe=pd.read_csv(source/'ownership_evidence.csv')
        for day,group in audits.groupby('day',sort=True):
            selected=[x for x in labels if x['uuid'] in group.index and ('AIOps2025',x['uuid']) not in done]
            if not selected:continue
            data,_,_,_=load_day(root,day)
            series={k:(g.t.to_numpy(),g.value.to_numpy(),str(g.kind.iloc[0]),str(g.entity.iloc[0]))
                for k,g in data.sort_values('t').groupby('metric',sort=False)}
            del data
            for label in selected:
                cid=label['uuid'];start=epoch(label['start_time']);metrics={};owners={};candidates=set()
                for key,(ts,vs,kind,name) in series.items():
                    mask=(ts>=start-600)&(ts<start+600)
                    if not mask.any():continue
                    candidates.add((kind,name));metrics[key]=list(zip(ts[mask],vs[mask]));owners[key]=(kind,name)
                ids={e:json.dumps(e) for e in candidates};rels=[]
                for r in de[de.case_id==cid].itertuples():
                    rels.append(Relation('d:'+r.node+'->'+r.pod,ids[('host',r.node)],ids[('pod',r.pod)],'deployment',r.source))
                for r in oe[oe.case_id==cid].itertuples():
                    rels.append(Relation('o:'+r.service+'->'+r.pod,ids[('service',r.service)],ids[('pod',r.pod)],'ownership',r.source))
                graph=RuntimeGraph(tuple(RuntimeEntity(ids[e],*e) for e in sorted(ids)),tuple(rels),
                    tuple(MetricBinding(k,ids[owners[k]],'observed_metric') for k in metrics),'aiops2025',day)
                level=audits.loc[cid,'level'];targets=json.loads(audits.loc[cid,'targets'])
                truths=[ids[(level,t)] for t in targets if (level,t) in ids]
                expected={r.arm:r.rank for r in old[(old.case_id==cid)&old['rank'].notna()].itertuples()}
                evaluate(out,'AIOps2025',cid,graph,metrics,{k:k for k in metrics},start,level,truths,'all',expected)
                completed+=1
                print('AIOps2025',day,cid,'completed',completed,flush=True)
                if args.limit and completed>=args.limit:break
            if args.limit and completed>=args.limit:break
    records=pd.DataFrame([json.loads(l) for l in (out/'cases.jsonl').read_text().splitlines()])
    records.to_csv(out/'cases.csv',index=False)
    summaries=[]
    for (ds,level,variant,arm),g in records[records.covered].groupby(['dataset','level','variant','arm']):
        row=dict(dataset=ds,level=level,variant=variant,arm=arm,cases=len(g),
            avg_removed_metrics=g.removed_near_constant.mean(),avg_retained_metrics=g.scored_metrics.mean())
        for k in [1,3,5]:
            row[f'top{k}']=g[f'fractional_top{k}'].mean() if ds=='AIOps2022' else (g['rank']<=k).mean()
        row['mrr']=(1/(g.mean_tie_rank if ds=='AIOps2022' else g['rank'])).mean()
        summaries.append(row)
    summary=pd.DataFrame(summaries);summary.to_csv(out/'summary.csv',index=False)
    print(summary[summary.arm.isin(['mapping','containment'])].to_string(index=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',choices=['all','lsdb','microcercl','aiops2022','aiops2025'],required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--limit',type=int,default=0)
    run(p.parse_args())
