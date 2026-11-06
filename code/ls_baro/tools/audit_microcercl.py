"""Audit MicroCERCL and run frozen four-arm ablations (no downloads)."""
import argparse, json, sys, time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ls_baro.microcercl_adapter import MicroCERCLAdapter, case_paths
from ls_baro.contracts import RCAConfig
from ls_baro.engine import RCAEngine
from ls_baro.scoring import score_with_official_baro

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,default=Path('D:/AIOps/data/abnormal_20240615/abnormal'))
    ap.add_argument('--output',type=Path,default=Path('D:/AIOps/LS-baro/output/microcercl'))
    ap.add_argument('--limit',type=int,default=0)
    ap.add_argument('--run',action='store_true')
    ap.add_argument('--application',default='all')
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    cases=[p for p in case_paths(args.root) if args.application=='all' or p.parent.parent.name==args.application]
    if args.limit: cases=cases[:args.limit]
    counts=Counter(); stats=defaultdict(list); engine=RCAEngine(); start=time.time()
    with (args.output/'audit.jsonl').open('w',encoding='utf-8') as af, (args.output/'results.jsonl').open('w',encoding='utf-8') as rf:
        for i,p in enumerate(cases,1):
            try:
                request=MicroCERCLAdapter().load(p)
                row={**request.audit,'status':'ok'}; counts['adapter_ok']+=1
                if args.run:
                    bases=None
                    frozen_scores=score_with_official_baro({s.metric_id:[(t,v) for t,v in zip(s.timestamps,s.values) if request.window.baseline_start<=t<=request.window.baseline_end or request.window.detection_start<=t<=request.window.detection_end] for s in request.observations},request.window.detection_start,score_mode='baro_dataframe_equivalent',metric_policy='exclude_last_seen')
                    engine=RCAEngine(scorer=lambda metrics,trigger,root:frozen_scores)
                    for arm,groups in [('mapping-only',('mapping',)),('containment-only',('mapping','containment')),('dependency-only',('mapping','dependency')),('Full',('mapping','containment','dependency'))]:
                        config=RCAConfig(relation_groups=groups,propagation_lambda=.1,direction='bidirectional',edge_normalization='row')
                        result=engine.analyze(request.graph,request.observations,request.window,config)
                        if bases is None: bases=result.base_scores
                        elif bases!=result.base_scores: raise AssertionError('base_scores_differ_between_arms')
                        labels={r['id'] for r in request.evaluation.root_causes}
                        rank=next((j for j,e in enumerate(result.rankings.get('service',[]),1) if e['id'] in labels),None)
                        global_rank=next((j for j,e in enumerate(result.rankings['all_entities'],1) if e['id'] in labels),None)
                        output={'case_id':row['case_id'],'application':row['application'],'fault':row['fault'],'arm':arm,
                                'root_match_count':row['root_match_count'],'service_rank':rank,'global_rank':global_rank,
                                'result':asdict(result)}
                        rf.write(json.dumps(output,ensure_ascii=False)+'\n'); rf.flush()
                        if rank is not None: stats[(row['application'],arm)].append((rank,global_rank))
                    counts['four_arm_ok']+=1
            except Exception as exc:
                row={'case_id':str(p.relative_to(args.root)),'status':'error','error':str(exc)}; counts['errors']+=1
            af.write(json.dumps(row,ensure_ascii=False)+'\n'); af.flush()
            if i%10==0 or i==len(cases): print(f'{i}/{len(cases)} {dict(counts)} elapsed={time.time()-start:.1f}s',flush=True)
    summary={'cases':len(cases),'counts':dict(counts),'elapsed_seconds':time.time()-start,'metrics':[]}
    for (app,arm),ranks in stats.items():
        summary['metrics'].append({'application':app,'arm':arm,'n':len(ranks),
            **{f'service_top{k}':sum(r<=k for r,g in ranks)/len(ranks) for k in (1,3,5)},
            'service_mrr':sum(1/r for r,g in ranks)/len(ranks),
            **{f'global_top{k}':sum(g<=k for r,g in ranks)/len(ranks) for k in (1,3,5)}})
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__': main()

