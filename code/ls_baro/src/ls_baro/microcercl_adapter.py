"""MicroCERCL reader: native metric ownership, pre-injection topology snapshot."""
from __future__ import annotations
import csv, hashlib, json, math, re
from collections import Counter
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from .contracts import AnalysisWindow, EvaluationRecord, MetricBinding, MetricSeries, Relation, RuntimeEntity, RuntimeGraph

@lru_cache(maxsize=200000)
def epoch(s):
    return int(datetime.strptime(s.strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())

def read_csv(p):
    with p.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def case_paths(root):
    return sorted(p for app in Path(root).iterdir() if app.is_dir() and app.name.endswith('_chaos')
                  for owner in app.iterdir() if owner.is_dir()
                  for p in owner.iterdir() if p.is_dir())

def label_for(case):
    match = re.search(r'_(cpu|mem|net)_(\d+)$', case.name)
    if not match:
        raise ValueError('unsupported_case_name')
    fault, ordinal = match.groups()
    suffix = {'cpu':'cpu_load', 'mem':'mem_load', 'net':'net_latency'}[fault]+'_'+ordinal
    records = []
    for p in sorted(case.parent.glob('*label*.txt')):
        heading = ''
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if re.search(r'_(cpu_load|mem_load|net_latency)_\d+$', line):
                heading = line
            if line.endswith('start create.') and heading.endswith('_'+suffix):
                records.append((epoch(line[:19]), str(p), heading))
    if len(records) != 1:
        raise ValueError(f'label_matches:{len(records)}')
    return records[0], fault

@dataclass
class MicroCERCLRequest:
    graph: RuntimeGraph
    observations: tuple
    window: AnalysisWindow
    evaluation: EvaluationRecord
    audit: dict

class MicroCERCLAdapter:
    def load(self, case, offset_hours=8, excluded_metric_files=()):
        case = Path(case)
        excluded_metric_files = frozenset(excluded_metric_files)
        (label_time, label_path, label_heading), fault = label_for(case)
        trigger = label_time - offset_hours*3600
        entities, relations, bindings, observations = {}, {}, [], []
        graph_files, quality, unresolved, ownership_evidence = [], Counter(), [], []
        def eid(kind, ns, name):
            key = f'microcercl:{kind}:{quote(ns+"/"+name,safe="")}'
            entities[key] = RuntimeEntity(key, kind, ns+'/'+name if ns else name)
            return key
        def relation(kind, src, dst, ref):
            key = (kind,src,dst)
            relations[key] = Relation('microcercl:relation:'+hashlib.sha256(repr(key).encode()).hexdigest()[:24],src,dst,kind,ref)
        namespace_data = []
        for folder in sorted(case.iterdir()):
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            metrics = folder/'metrics'
            ns = folder.name
            if ns == 'node':
                p = folder/'node.csv'
                files = [(p,'host')] if p.exists() else []
            else:
                files = [(metrics/n,k) for n,k in [('instance.csv','pod'),('svc_metric.csv','service'),('latency.csv','service'),('success_rate.csv','service'),('svc_qps.csv','service')]
                         if (metrics/n).exists() and n not in excluded_metric_files]
            pods, services, hosts = {}, {}, {}
            for p,kind in files:
                rows = read_csv(p)
                if not rows or 'timestamp' not in rows[0]:
                    raise ValueError('missing_timestamp:'+str(p))
                times = [epoch(r['timestamp']) for r in rows]
                if len(set(times)) != len(times):
                    raise ValueError('duplicate_timestamp:'+str(p))
                if times != sorted(times):
                    quality['sorted_metric_files']+=1
                    rows=sorted(rows,key=lambda r:epoch(r['timestamp']))
                    times=[epoch(r['timestamp']) for r in rows]
                for col in rows[0]:
                    if col == 'timestamp': continue
                    if kind == 'host':
                        m = re.fullmatch(r'\(node\)(.+?)_(edge_network|network|cpu|memory)',col)
                        if not m: unresolved.append(str(p)+':'+col); continue
                        name = m.group(1)
                        owner = eid(kind,'',name); hosts[name]=owner
                    elif kind == 'pod':
                        m = re.fullmatch(r'(.+)_(cpu|memory|network)',col)
                        if not m: unresolved.append(str(p)+':'+col); continue
                        name=m.group(1); owner=eid(kind,ns,name); pods[name]=owner
                    else:
                        name = col.split('&')[0]
                        # Service-level CSVs without '&' use the entire column as service name.
                        owner=eid(kind,ns,name); services[name]=owner
                    values=[]; timestamps=[]
                    for t,row in zip(times,rows):
                        try: value=float(row[col])
                        except (ValueError,TypeError): quality['non_numeric_values']+=1; continue
                        if math.isfinite(value): timestamps.append(t); values.append(value)
                        else: quality['non_finite_values']+=1
                    mid='microcercl:metric:'+quote(ns+'/'+p.name+'/'+col,safe='')
                    observations.append(MetricSeries(mid,tuple(timestamps),tuple(values)))
                    bindings.append(MetricBinding(mid,owner,str(p)))
            namespace_data.append((ns,metrics,pods,services,hosts))
        # Host dictionary shared across all namespaces; never obtained from labels.
        hosts={name:owner for _,_,_,_,h in namespace_data for name,owner in h.items()}
        for ns,metrics,pods,services,_ in namespace_data:
            for pod,pid in pods.items():
                # Kubernetes deployment-name + ReplicaSet hash + instance suffix.
                m=re.fullmatch(r'(.+)-[a-z0-9]{6,12}-[a-z0-9]{5}',pod)
                service=m.group(1) if m else None
                if service in services:
                    relation('ownership',services[service],pid,'validated-name-rule:'+ns+'/'+pod)
                    ownership_evidence.append({'namespace':ns,'pod':pod,'service':service,'rule':'exact_deployment_prefix'})
                else: unresolved.append('ownership:'+ns+'/'+pod)
            p=metrics/'graph.csv'
            if not p.exists(): continue
            rows=read_csv(p); graph_files.append(str(p))
            snapshot=max((epoch(r['timestamp']) for r in rows if epoch(r['timestamp'])<=trigger),default=None)
            if snapshot is None: raise ValueError('no_pre_injection_topology:'+ns)
            for r in rows:
                if epoch(r['timestamp'])!=snapshot: continue
                a,b=r['source'],r['destination']
                if a in pods and b in hosts:
                    relation('deployment',hosts[b],pods[a],str(p)+':instance-to-server')
                elif a in services and b in services:
                    relation('dependency',services[a],services[b],str(p)+':caller-to-callee')
                else: unresolved.append('graph:'+ns+'/'+a+'->'+b)
        if not observations: raise ValueError('no_observations')
        window=AnalysisWindow(trigger-300,trigger-1,trigger,trigger+299)
        for s in observations:
            pre=[v for t,v in zip(s.timestamps,s.values) if window.baseline_start<=t<=window.baseline_end]
            post=[v for t,v in zip(s.timestamps,s.values) if window.detection_start<=t<=window.detection_end]
            if len(pre)<2 or len(post)<2: quality['insufficient_window_metrics']+=1
            elif len(set(pre))==1 or len(set(post))==1: quality['constant_window_metrics']+=1
            else: quality['baro_eligible_metrics']+=1
        if not quality['baro_eligible_metrics']: raise ValueError('no_baro_eligible_metrics')
        target_ns={'bookinfo_chaos':'bookinfo','hipster_chaos':'hipster','sock_shop_chaos':'cloud-sock-shop'}[case.parent.parent.name]
        root=case.parent.name
        # Label-dependent evaluation aliases only: never add entities/edges or bindings.
        candidates=[e for e in entities.values() if e.kind=='service' and e.name.startswith(target_ns+'/')
                    and (e.name.split('/',1)[1]==root or e.name.split('/',1)[1].startswith(root+'-'))]
        if case.name.startswith('cloud_pod_') and not root.endswith('-edge'):
            candidates=[e for e in candidates if not e.name.split('/',1)[1].endswith('-edge')]
        exact=[e for e in candidates if e.name.split('/',1)[1]==root]
        candidates=exact or candidates
        roots=tuple({'kind':'service','id':e.id,'name':e.name} for e in candidates) if len(candidates)==1 else ()
        audit={'case_id':str(case.relative_to(case.parents[2])),'application':case.parent.parent.name,'fault':fault,
               'label_heading':label_heading,'label_file':label_path,'label_offset_hours':offset_hours,
               'trigger_epoch':trigger,'window_seconds':300,'root_match_count':len(candidates),
               'entities':dict(Counter(e.kind for e in entities.values())),
               'relations':dict(Counter(r.kind for r in relations.values())),
               'metrics':len(observations),'quality':dict(quality),'unresolved_count':len(unresolved),
               'unresolved_examples':unresolved[:20],
               'excluded_metric_files':sorted(excluded_metric_files)}
        graph=RuntimeGraph(tuple(entities.values()),tuple(relations.values()),tuple(bindings),'MicroCERCL','1.0',metadata={
            'scope':'all_monitored_namespaces','topology_policy':'latest_snapshot_at_or_before_injection',
            'ownership_rule':'exact_known_service_after_kubernetes_suffix_removal','ownership_evidence':ownership_evidence,
            'unresolved':unresolved,'metric_policy':'native_all_supported_files_no_cross_file_dedup',
            'excluded_metric_files':sorted(excluded_metric_files),
            'unsupported_metric_files':['resource.csv','call.csv','instances_num.csv'],
            'graph_files':graph_files})
        return MicroCERCLRequest(graph,tuple(observations),window,EvaluationRecord(audit['case_id'],roots,(),
            'public_injection_time_minus_explicit_8h'),audit)



