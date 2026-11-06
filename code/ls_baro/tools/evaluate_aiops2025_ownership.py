"""Frozen four-arm relation pilot; observed baseline trace ownership, unchanged propagation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import argparse
import hashlib
import json
from collections import Counter, defaultdict
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from ls_baro.scoring import score_with_baro_dataframe_equivalent
from ls_baro.contracts import RuntimeEntity, RuntimeGraph, Relation, MetricBinding, RCAConfig
from ls_baro.canonical_core import aggregate, propagation
from ls_baro.topology import propagate_once

NULLS = {'', 'null', 'none', 'nan', '<na>'}

def valid(x):
    return str(x).strip().lower() not in NULLS

def seconds(series):
    values = pd.to_datetime(series, utc=True, errors='raise')
    return np.array([x.timestamp() for x in values], dtype=float)

def epoch(value):
    return pd.Timestamp(value).timestamp()

def load_day(root, day):
    """Keep every infra tag in metric identity; never merge devices/mountpoints."""
    frames, deployment, schema_rows = [], [], []
    for path in sorted((root / day / 'metric-parquet').rglob('*.parquet')):
        if path.name.startswith('._'):
            continue
        rel = str(path.relative_to(root)).replace('\\', '/')
        if '/infra/infra_node/' in rel:
            kind, entity_col = 'host', 'kubernetes_node'
        elif '/infra/infra_pod/' in rel:
            kind, entity_col = 'pod', 'pod'
        elif '/apm/pod/' in rel:
            kind, entity_col = 'pod', 'object_id'
        elif '/apm/service/' in rel:
            kind, entity_col = 'service', 'object_id'
        else:
            continue  # namespace and database layers are outside this pilot contract
        df = pq.read_table(path).to_pandas()
        numeric = [f.name for f in pq.read_schema(path)
                   if pa.types.is_integer(f.type) or pa.types.is_floating(f.type)]
        if 'time' not in df or entity_col not in df or not numeric:
            raise ValueError(f'Unrecognized schema: {path}')
        times = seconds(df['time'])
        entity = df[entity_col].astype(str)
        keep = entity.map(valid).to_numpy()
        if kind == 'pod' and 'instance' in df and '/infra/' in rel:
            for t, pod, node in zip(times, entity, df['instance'].astype(str)):
                if valid(pod) and valid(node):
                    deployment.append((t, pod, node, rel))
        tags = [c for c in df if c not in numeric and c not in {'time', 'kpi_name'}]
        # String tag combination is a series identity, separate from its entity owner.
        identities = df[tags].astype(str).apply(
            lambda row: json.dumps(row.to_dict(), sort_keys=True), axis=1)
        family = '/'.join(rel.split('/')[2:-1])
        for metric in numeric:
            keys = family + '|' + metric + '|' + identities
            frames.append(pd.DataFrame({
                't': times[keep], 'kind': kind, 'entity': entity.to_numpy()[keep],
                'metric': keys.to_numpy()[keep],
                'value': pd.to_numeric(df[metric], errors='coerce').to_numpy()[keep],
            }))
        schema_rows.append({'path': rel, 'rows': len(df), 'kind': kind,
                            'numeric_columns': numeric, 'identity_tags': tags})
    data = pd.concat(frames, ignore_index=True)
    finite = np.isfinite(data['value'].to_numpy())
    nonfinite = int((~finite).sum())
    data = data.loc[finite].drop_duplicates(['t', 'metric', 'value'])
    if data.duplicated(['t', 'metric']).any():
        raise ValueError(f'Conflicting metric samples in {day}')
    return data, pd.DataFrame(deployment, columns=['t','pod','node','source']), schema_rows, nonfinite

def ownership_before(root, day, left, start):
    """Use observed process.serviceName + process tags.name; no name heuristics or labels."""
    latest = {}
    paths = []
    hours = pd.date_range(pd.Timestamp(left,unit='s',tz='UTC').tz_convert('Asia/Shanghai').floor('h'),
                          pd.Timestamp(start-0.001,unit='s',tz='UTC').tz_convert('Asia/Shanghai').floor('h'),freq='h')
    for hour in hours:
        trace_dir=root/hour.date().isoformat()/'trace-parquet'
        paths.extend(p for p in trace_dir.glob(f'*_{hour.hour:02d}-00-00.parquet') if not p.name.startswith('._'))
    for path in sorted(set(paths)):
        table=pq.read_table(path,columns=['startTimeMillis','process'],
            filters=[('startTimeMillis','>=',int(left*1000)),('startTimeMillis','<',int(start*1000))])
        if not len(table):
            continue
        import pyarrow.compute as pc
        proc=table['process'].combine_chunks()
        tags=proc.field('tags'); flat=pc.list_flatten(tags)
        parent=pc.list_parent_indices(tags)
        d=pd.DataFrame({'t':table['startTimeMillis'].to_numpy()/1000,
                        'service':proc.field('serviceName').to_pylist()})
        for key in ['name','namespace']:
            mask=pc.equal(flat.field('key'),key)
            idx=pc.filter(parent,mask).to_numpy()
            if len(set(idx))!=len(idx):
                raise ValueError('Duplicate process tag key: '+key)
            d[key]=pd.Series(pc.filter(flat.field('value'),mask).to_pylist(),index=idx)
        d=d[(d.namespace=='hipstershop')&d['name'].notna()&d.service.notna()]
        for pod,g in d.groupby('name',sort=False):
            t=float(g.t.max()); services=set(g.loc[g.t==t,'service'])
            if not valid(pod) or not all(valid(v) for v in services):
                continue
            if pod not in latest or t>latest[pod][0]:
                latest[pod]=(t,services,str(path.relative_to(root)))
            elif t==latest[pod][0]:
                latest[pod][1].update(services)
    return latest


def run(root, out):
    out.mkdir(parents=True, exist_ok=True)
    labels = [json.loads(x) for x in (root / 'groundtruth.jsonl').read_text(
        encoding='utf-8').splitlines() if x.strip()]
    grouped = defaultdict(list)
    for label in labels:
        day = pd.Timestamp(label['start_time']).tz_convert('Asia/Shanghai').date().isoformat()
        grouped[day].append(label)
    protocol = dict(pre_seconds=600, post_seconds=600, propagation_lambda=.1,
                    direction='bidirectional', normalization='row', aggregation='max',
                    score='shared BARO DataFrame equivalent: signed maximum RobustScaler',
                    candidate_policy='all entities with finite samples in case window, identical arms',
                    deployment_policy='last baseline observation per Pod; ambiguous latest nodes omitted',
                    rank_policy='deterministic score then entity name; best rank among annotated targets',
                    metrics='infra_node,infra_pod,apm/pod,apm/service; all numeric columns',
                    labels_used_for='window onset and evaluation only; no key_metrics filtering',
                    exclusions='namespace/database layers outside pilot; no dependency tested',
                    ownership_policy='latest baseline trace process.serviceName + process.tags.name; omit ambiguous',
                    isolation='other labelled incident overlaps [onset-600,onset+600)',
                    scope='pilot, does not establish causal effectiveness or full benchmark replication')
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    audits, results, rankings, edges_saved, schemas = [], [], [], [], []
    ownership_saved=[]
    completed=set()
    if (out/'case_results.csv').exists():
        audits=pd.read_csv(out/'case_audit.csv').fillna(0).to_dict('records')
        results=pd.read_csv(out/'case_results.csv').to_dict('records')
        rankings=pd.read_csv(out/'rankings.csv').to_dict('records')
        edges_saved=pd.read_csv(out/'deployment_evidence.csv').to_dict('records')
        ownership_saved=pd.read_csv(out/'ownership_evidence.csv').to_dict('records')
        completed={x['case_id'] for x in audits}

    for day, cases in sorted(grouped.items()):
        if all(x['uuid'] in completed for x in cases):
            print(f'RESUME skip {day}',flush=True)
            continue
        print(f'LOAD {day} cases={len(cases)}', flush=True)
        data, deploy, day_schemas, nonfinite = load_day(root, day)
        schemas.extend(day_schemas)
        # Store each series once, then use array slices for all cases on this day.
        series = {}
        for key, g in data.groupby('metric', sort=False):
            g = g.sort_values('t')
            series[key] = (g['t'].to_numpy(), g['value'].to_numpy(),
                           str(g['kind'].iloc[0]), str(g['entity'].iloc[0]))
        del data
        for label in cases:
            cid, start = label['uuid'], epoch(label['start_time'])
            stop, left = start+600, start-600
            metrics, owners, status = {}, {}, Counter()
            candidates, retained_entities = set(), set()
            low_samples, zero_iqr = 0, 0
            for key, (ts, vs, kind, name) in series.items():
                mask = (ts >= left) & (ts < stop)
                if not mask.any():
                    continue
                t, v = ts[mask], vs[mask]
                owner = (kind, name)
                candidates.add(owner)
                pre, post = v[t < start], v[t >= start]
                low_samples += int(len(pre)<10 or len(post)<10)
                if not len(pre) or not len(post):
                    status['missing_window'] += 1
                elif np.all(pre==pre[0]) or np.all(post==post[0]):
                    status['constant_window'] += 1
                else:
                    status['retained'] += 1
                    retained_entities.add(owner)
                    zero_iqr += int(np.percentile(pre,75)==np.percentile(pre,25))
                metrics[key] = list(zip(t.tolist(),v.tolist()))
                owners[key] = owner
            scored = dict(score_with_baro_dataframe_equivalent(metrics, start))
            assert len(scored) == status['retained']
            ids = {e: json.dumps(e) for e in candidates}
            rels = []
            ambiguous = 0
            if not deploy.empty:
                observed = deploy.loc[(deploy.t>=left)&(deploy.t<start)]
                for pod, g in observed.groupby('pod'):
                    latest = g.loc[g.t == g.t.max()]
                    nodes = set(latest.node)
                    if len(nodes)!=1:
                        ambiguous += 1
                        continue
                    node = next(iter(nodes))
                    a,b = ('host',node),('pod',pod)
                    if a not in ids or b not in ids:
                        continue
                    rid = f'{node}->{pod}'
                    rels.append(Relation(rid,ids[a],ids[b],'deployment',str(latest.source.iloc[0])))
                    edges_saved.append(dict(case_id=cid,node=node,pod=pod,
                        observed_at=float(latest.t.iloc[0]),source=str(latest.source.iloc[0])))
            ownership_evidence=ownership_before(root,day,left,start)
            ambiguous_ownership=0
            for pod,(t,services,source) in ownership_evidence.items():
                if len(services)!=1:
                    ambiguous_ownership+=1
                    continue
                service=next(iter(services))
                a,b=('service',service),('pod',pod)
                if a not in ids or b not in ids:
                    continue
                rels.append(Relation('ownership:'+service+'->'+pod,ids[a],ids[b],'ownership',source))
                ownership_saved.append(dict(case_id=cid,service=service,pod=pod,observed_at=t,source=source))
            entities = tuple(RuntimeEntity(ids[e],*e) for e in sorted(ids))
            bindings = tuple(MetricBinding(k,ids[owners[k]],'observed_metric') for k in metrics)
            graph = RuntimeGraph(entities,tuple(rels),bindings,'aiops2025',day)
            config = RCAConfig(relation_groups=('mapping',),propagation_lambda=.1,
                direction='bidirectional',edge_normalization='row',metric_policy='all')
            base,_ = aggregate(graph,scored,{},dict(zip(metrics,metrics)),config)
            deployment_config = RCAConfig(relation_groups=('mapping','deployment'),
                propagation_lambda=.1,direction='bidirectional',edge_normalization='row',metric_policy='all')
            prop_edges,_ = propagation(graph,deployment_config)
            final,_ = propagate_once(base,prop_edges,.1)
            level = {'node':'host','pod':'pod','service':'service'}[label['instance_type']]
            targets = label['instance'] if isinstance(label['instance'],list) else [label['instance']]
            targets = set(map(str,targets))
            other = [x['uuid'] for x in labels if x['uuid']!=cid
                and epoch(x['start_time'])<stop and epoch(x['end_time'])>left]
            audit = dict(case_id=cid,day=day,level=level,target_count=len(targets),
                targets=json.dumps(sorted(targets)),target_observed=sum((level,t) in ids for t in targets),
                target_retained=sum((level,t) in retained_entities for t in targets),
                metrics=len(metrics),**status,low_sample_series=low_samples,zero_iqr_series=zero_iqr,
                nodes=sum(e[0]=='host' for e in ids),pods=sum(e[0]=='pod' for e in ids),
                services=sum(e[0]=='service' for e in ids),deployment_edges=sum(r.kind=='deployment' for r in rels),
                ownership_edges=sum(r.kind=='ownership' for r in rels),ambiguous_ownership=ambiguous_ownership,
                ambiguous_pods=ambiguous,overlapping_incidents=len(other),
                window_within_day=(left>=pd.Timestamp(day,tz='Asia/Shanghai').timestamp()
                    and stop<=pd.Timestamp(day,tz='Asia/Shanghai').timestamp()+86400),
                day_nonfinite_samples=nonfinite)
            audits.append(audit)
            arm_scores={'mapping':base,'deployment':final}
            for arm,groups in [('ownership',('mapping','ownership')),('containment',('mapping','containment'))]:
                arm_config=RCAConfig(relation_groups=groups,propagation_lambda=.1,
                    direction='bidirectional',edge_normalization='row',metric_policy='all')
                arm_edges,_=propagation(graph,arm_config)
                arm_scores[arm],_=propagate_once(base,arm_edges,.1)
            for arm,scores in arm_scores.items():
                ordered = sorted([e for e in entities if e.kind==level],
                    key=lambda e:(-scores[e.id],e.name,e.id))
                ranks = [i+1 for i,e in enumerate(ordered) if e.name in targets]
                rank = min(ranks) if ranks else None
                result = dict(case_id=cid,day=day,level=level,arm=arm,rank=rank,
                    top1=int(rank is not None and rank<=1),top3=int(rank is not None and rank<=3),
                    top5=int(rank is not None and rank<=5),mrr=1/rank if rank else 0,
                    candidate_count=len(ordered),isolated=not other,
                    all_targets_observed=audit['target_observed']==len(targets),
                    window_within_day=audit['window_within_day'])
                results.append(result)
                for i,e in enumerate(ordered):
                    rankings.append(dict(case_id=cid,arm=arm,level=level,
                        entity=e.name,rank=i+1,base=base[e.id],score=scores[e.id]))
            assert all(base[e.id]==final[e.id] for e in entities if e.kind=='service')
        pd.DataFrame(audits).to_csv(out/'case_audit.csv',index=False)
        pd.DataFrame(results).to_csv(out/'case_results.csv',index=False)
        pd.DataFrame(rankings).to_csv(out/'rankings.csv',index=False)
        pd.DataFrame(edges_saved).to_csv(out/'deployment_evidence.csv',index=False)
        pd.DataFrame(ownership_saved).to_csv(out/'ownership_evidence.csv',index=False)
        print(f'DONE {day} cumulative={len(audits)}',flush=True)
    pd.DataFrame(schemas).to_json(out/'schema_audit.json',orient='records',indent=2)
    df = pd.DataFrame(results)
    summaries = []
    for cohort, mask in [('all',np.ones(len(df),dtype=bool)),
        ('observed_targets_complete_windows',df.all_targets_observed & df.window_within_day),
        ('isolated_observed_targets_complete_windows',df.isolated & df.all_targets_observed & df.window_within_day)]:
        summary = df.loc[mask].groupby(['level','arm']).agg(
            cases=('case_id','size'),top1=('top1','mean'),top3=('top3','mean'),
            top5=('top5','mean'),mrr=('mrr','mean')).reset_index()
        summary.insert(0,'cohort',cohort)
        summaries.append(summary)
    pd.concat(summaries).to_csv(out/'summary.csv',index=False)
    (out/'run_info.json').write_text(json.dumps(dict(cases=len(audits),
        levels=dict(Counter(x['level'] for x in audits)),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        deployment_service_score_invariance=True),indent=2),encoding='utf-8')
    print(pd.concat(summaries).to_string(index=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    run(a.root,a.out)
