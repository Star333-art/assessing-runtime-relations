"""Host-unobserved counterfactual using frozen one-pass additive scores.

All Host bases are set to zero in every case and every arm. Pod/Service bases,
graphs, candidates, scorer, lambda, normalization and original files are fixed.
Root-layer scores can be recovered exactly algebraically from the frozen four
arms: Host final becomes its incoming increment; Pod D becomes Mapping, Pod C
becomes Ownership; Service scores do not change. Six native cases additionally
validate this replay against direct execution of the unchanged shared core.
"""
import hashlib,json
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ls_baro.contracts import RuntimeEntity,RuntimeGraph,Relation,MetricBinding,RCAConfig
from ls_baro.canonical_core import aggregate,propagation
from ls_baro.topology import propagate_once
from ls_baro.scoring import score_with_baro_dataframe_equivalent
from evaluate_aiops2025_ownership import load_day,epoch

SOURCE=ROOT/'output/aiops2025_ownership_pilot'
MAIN=ROOT/'output/aiops2025_lsdb_protocol_20261023'
OUT=ROOT/'output/aiops2025_host_zero_20261023'
DATA=ROOT.parent/'data/AgenticOpsEval/AIOps2025'
ARMS={'mapping':(), 'deployment':('deployment',),'ownership':('ownership',),
      'containment':('deployment','ownership')}
EXCLUDED={'29f88351-232','0718e0f9-92','d32bcd36-104'}

def scores_and_metrics(g,score,target):
    # Same deterministic ordering as the frozen pilot.
    g=g.assign(evaluated_score=score).sort_values(['evaluated_score','entity'],ascending=[False,True])
    root=float(g.loc[g.entity==target,'evaluated_score'].iloc[0])
    rank=int(np.flatnonzero(g.entity.to_numpy()==target)[0]+1)
    higher=int((g.evaluated_score>root).sum());tied=int((g.evaluated_score==root).sum())
    row=dict(rank=rank,tie_size=tied,root_score=root,candidates=len(g))
    for k in range(1,6):
        row[f'top{k}']=int(rank<=k)
        row[f'tie_top{k}']=float(np.clip((k-higher)/tied,0,1))
    row['avg5']=sum(row[f'top{k}'] for k in range(1,6))/5
    row['tie_avg5']=sum(row[f'tie_top{k}'] for k in range(1,6))/5
    return row

def main():
    OUT.mkdir(exist_ok=True)
    source_files=[SOURCE/'rankings.csv',SOURCE/'case_results.csv',SOURCE/'case_audit.csv',
        SOURCE/'deployment_evidence.csv',SOURCE/'ownership_evidence.csv',MAIN/'label_audit.csv']
    core_files=[ROOT/'src/ls_baro'/n for n in ['scoring.py','canonical_core.py','topology.py']]
    original_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files+core_files}
    audit=pd.read_csv(MAIN/'label_audit.csv');audit=audit[audit.status=='single_entity_main'].copy()
    assert len(audit)==272
    rk=pd.read_csv(SOURCE/'rankings.csv');rk=rk[rk.case_id.isin(audit.case_id)]
    native_cases=pd.read_csv(MAIN/'case_results.csv')
    base_audit=pd.read_csv(SOURCE/'case_audit.csv').set_index('case_id')
    de=pd.read_csv(SOURCE/'deployment_evidence.csv');oe=pd.read_csv(SOURCE/'ownership_evidence.csv')
    output=[];score_rows=[];counterfactual={};pod_additive_error=0.0
    for x in audit.itertuples():
        target=json.loads(x.targets)[0];cid=x.case_id
        case=rk[rk.case_id==cid]
        by={a:case[case.arm==a].set_index('entity').sort_index() for a in ARMS}
        names=by['mapping'].index
        assert all(v.index.equals(names) for v in by.values())
        assert all(np.array_equal(v.base.to_numpy(),by['mapping'].base.to_numpy()) for v in by.values())
        base=by['mapping'].base.to_numpy()
        if x.level=='host':
            assert np.allclose(by['ownership'].score,base,rtol=1e-12,atol=1e-10)
            changed={a:(by[a].score.to_numpy()-base if a in ['deployment','containment'] else np.zeros(len(base))) for a in ARMS}
        elif x.level=='pod':
            # Incoming sources are only Host via D and Service via O; source
            # degrees for these senders do not change when adding the other kind.
            error=np.max(np.abs(by['containment'].score.to_numpy()-(by['deployment'].score+by['ownership'].score-base).to_numpy()))
            pod_additive_error=max(pod_additive_error,float(error))
            assert np.allclose(by['containment'].score,by['deployment'].score+by['ownership'].score-base,rtol=1e-12,atol=1e-8)
            changed={'mapping':base.copy(),'deployment':base.copy(),
                'ownership':by['ownership'].score.to_numpy().copy(),
                'containment':by['ownership'].score.to_numpy().copy()}
        else:
            changed={a:by[a].score.to_numpy().copy() for a in ARMS}
        for a in ARMS:
            g=by[a].reset_index()
            for variant,score in [('native',by[a].score.to_numpy()),('host_zero',changed[a])]:
                stats=scores_and_metrics(g,score,target)
                if variant=='native':
                    old=native_cases[(native_cases.case_id==cid)&(native_cases.arm==a)].iloc[0]
                    assert stats['rank']==old['rank']
                output.append(dict(case_id=cid,level=x.level,arm=a,variant=variant,**stats))
                for entity,before,after in zip(names,by[a].score.to_numpy(),score):
                    score_rows.append(dict(case_id=cid,level=x.level,arm=a,variant=variant,
                        entity=entity,base_native=float(by[a].loc[entity,'base']),score=float(after)))
            counterfactual[(cid,a)]=dict(zip(names,changed[a]))
    # Independently validate original and masked scores on six data cases,
    # chosen by source order: first two cases of each labelled layer, no outcome
    # selection. Score metrics once and execute the shared propagation core.
    labels={l['uuid']:l for l in (json.loads(line) for line in (DATA/'groundtruth.jsonl').read_text(encoding='utf-8').splitlines())}
    chosen=audit.groupby('level',sort=False).head(2)
    checks=[];max_native_error=0.0;max_masked_error=0.0
    for day,selected in chosen.assign(day=chosen.case_id.map(base_audit.day)).groupby('day'):
        print('Direct-core validation day',day,flush=True)
        data,_,_,_=load_day(DATA,day)
        series={k:(g.t.to_numpy(),g.value.to_numpy(),str(g.kind.iloc[0]),str(g.entity.iloc[0]))
            for k,g in data.sort_values('t').groupby('metric',sort=False)}
        del data
        for x in selected.itertuples():
            cid=x.case_id;start=epoch(labels[cid]['start_time']);metrics={};owners={};candidates=set()
            for key,(ts,vs,kind,name) in series.items():
                mask=(ts>=start-600)&(ts<start+600)
                if not mask.any():continue
                metrics[key]=list(zip(ts[mask],vs[mask]));owners[key]=(kind,name);candidates.add((kind,name))
            ids={e:json.dumps(e) for e in candidates};relations=[]
            for r in de[de.case_id==cid].itertuples():
                relations.append(Relation(f'{r.node}->{r.pod}',ids[('host',r.node)],ids[('pod',r.pod)],'deployment',r.source))
            for r in oe[oe.case_id==cid].itertuples():
                relations.append(Relation('ownership:'+r.service+'->'+r.pod,ids[('service',r.service)],ids[('pod',r.pod)],'ownership',r.source))
            entities=tuple(RuntimeEntity(ids[e],*e) for e in sorted(ids))
            graph=RuntimeGraph(entities,tuple(relations),tuple(MetricBinding(k,ids[owners[k]],'observed_metric') for k in metrics),'aiops2025',day)
            config=RCAConfig(relation_groups=('mapping',),propagation_lambda=.1,direction='bidirectional',edge_normalization='row',metric_policy='all')
            scores=dict(score_with_baro_dataframe_equivalent(metrics,start))
            native,_=aggregate(graph,scores,{},dict(zip(metrics,metrics)),config)
            zero={e.id:0.0 if e.kind=='host' else native[e.id] for e in entities}
            assert all(zero[e.id]==native[e.id] for e in entities if e.kind!='host')
            for a,kinds in ARMS.items():
                config=RCAConfig(relation_groups=('mapping',*kinds),propagation_lambda=.1,direction='bidirectional',edge_normalization='row')
                edges,_=propagation(graph,config)
                old,_=propagate_once(native,edges,.1);masked,_=propagate_once(zero,edges,.1)
                saved=rk[(rk.case_id==cid)&(rk.arm==a)].set_index('entity')
                for e in entities:
                    if e.kind=='service':assert old[e.id]==masked[e.id]
                    if e.kind!=x.level:continue
                    ne=abs(old[e.id]-saved.loc[e.name,'score']);me=abs(masked[e.id]-counterfactual[(cid,a)][e.name])
                    max_native_error=max(max_native_error,ne);max_masked_error=max(max_masked_error,me)
                    assert np.isclose(old[e.id],saved.loc[e.name,'score'],rtol=1e-12,atol=1e-8)
                    assert np.isclose(masked[e.id],counterfactual[(cid,a)][e.name],rtol=1e-12,atol=1e-8)
                expected=next(r for r in output if r['case_id']==cid and r['arm']==a and r['variant']=='host_zero')
                ordered=sorted([e for e in entities if e.kind==x.level],key=lambda e:(-masked[e.id],e.name,e.id))
                target=json.loads(x.targets)[0]
                assert next(i+1 for i,e in enumerate(ordered) if e.name==target)==expected['rank']
                checks.append(dict(case_id=cid,level=x.level,arm=a,passed=True))
    df=pd.DataFrame(output);df.to_csv(OUT/'case_results.csv',index=False)
    pd.DataFrame(score_rows).to_csv(OUT/'candidate_scores.csv',index=False)
    pd.DataFrame(checks).to_csv(OUT/'direct_core_checks.csv',index=False)
    summaries=[]
    for cohort,cases in [('main272',df),('deployment_covered269',df[~df.case_id.isin(EXCLUDED)])]:
        for scope,g in [('all',cases),*cases.groupby('level')]:
            for (variant,arm),sub in g.groupby(['variant','arm']):
                for evaluation,prefix in [('deterministic',''),('tie_expected','tie_')]:
                    row=dict(cohort=cohort,scope=scope,variant=variant,arm=arm,evaluation=evaluation,cases=len(sub))
                    row.update({f'top{k}':sub[prefix+f'top{k}'].mean() for k in range(1,6)})
                    row['avg5']=sub[prefix+'avg5'].mean();summaries.append(row)
    summary=pd.DataFrame(summaries);summary.to_csv(OUT/'summary.csv',index=False)
    for a in ARMS:
        s=df[(df.level=='service')&(df.arm==a)]
        p=s.pivot(index='case_id',columns='variant',values='rank')
        assert (p.native==p.host_zero).all()
    assert len(df)==272*8 and not df.duplicated(['case_id','variant','arm']).any()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in original_hashes.items())
    protocol=dict(experiment='Host-unobserved: b_host=0 for every case and arm',
        arms=ARMS,main_cohort=272,secondary_cohort=269,
        lambda_value=.1,direction='bidirectional',normalization='row',steps=1,
        candidates_and_relations_unchanged=True,pod_and_service_base_scores_unchanged=True,
        source_files_and_core_unchanged=True,service_scores_and_ranks_unchanged=True,
        replay='Frozen additive score replay for all 272 cases, direct metric/core validation on first two cases of each layer',
        direct_validation_cases=chosen.case_id.tolist(),direct_case_arm_checks=len(checks),
        max_native_direct_score_error=max_native_error,max_masked_direct_replay_error=max_masked_error,
        all_pod_additive_identity_max_error=pod_additive_error,
        tie_policy='Exact equal evaluated score; expected Top-k under uniform random tie breaking; no score epsilon added',
        scope='Post-hoc observability ablation; retain original evidence results; no dependency arm exists in frozen graph',
        source_and_core_hashes=original_hashes,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (OUT/'verification.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    lines=['# AIOps2025 Host初始分数置零实验','',
        '这是Host直接证据不可用的事后受控实验，不替换原始观测结果。每个案例、每组配置统一令所有Host初始分数为零，Pod与Service分数、候选集、原始关系和公式保持不变。',
        '主评价仍为272个原生单实体故障；269例Deployment覆盖对照单独输出。只比较已有Mapping、Deployment、Ownership、Containment四组；不补造Dependency图。',
        '全272例基于固定单步加法公式重放：Host最终分数为原传入增量；Pod的Deployment置零结果等于Mapping、Containment等于Ownership；Service分数不变。',
        '另选每层源文件顺序前两例，共6例，重读指标并通过未修改的共享核心直接计算验证，共24个案例配置。浮点误差和一致性检查见verification.json。','']
    for scope in ['all','host','pod','service']:
        lines+=['## '+scope+'（272例主队列）','',
            '| 观测条件 | 配置 | 排名口径 | N | Top-1 | Top-3 | Top-5 | Avg@5 |',
            '|---|---|---|---:|---:|---:|---:|---:|']
        subset=summary[(summary.cohort=='main272')&(summary.scope==scope)]
        for r in subset.itertuples():
            lines.append(f'| {r.variant} | {r.arm} | {r.evaluation} | {r.cases} | {r.top1:.2%} | {r.top3:.2%} | {r.top5:.2%} | {r.avg5:.4f} |')
    lines+=['','## 解释边界','',
        'Host置零时Mapping及Ownership的Host排名全部并列。确定性命中率受名称排序影响，并列期望命中率表示零证据下的对照；不将固定名称排序解释为异常识别能力。',
        '置零是统一屏蔽某类直接证据，不是筛选有利案例。原始结果及269例完整性敏感性均保留；不能用置零结果替代原始证据条件下的负结果。',
        '当前不可变初始分数的一次传播中，Host不会通过两跳向Service传递证据。所有Service分数和排名已逐例核验保持不变。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(summary[(summary.cohort=='main272')&(summary.scope.isin(['all','host']))][
        ['scope','variant','arm','evaluation','cases','top1','top3','top5','avg5']].to_string(index=False))
    print('Direct checks:',len(checks),'native error',max_native_error,'masked replay error',max_masked_error)

if __name__=='__main__':main()
