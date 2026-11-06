import csv
from pathlib import Path
from ls_baro.microcercl_adapter import MicroCERCLAdapter

def fixture_case(tmp_path):
    case=tmp_path/'bookinfo_chaos'/'details'/'cloud_pod_details_cpu_1'
    metrics=case/'bookinfo'/'metrics'; metrics.mkdir(parents=True)
    (case.parent/'details_label.txt').write_text('details_cpu_load_1\n2024-01-13 13:59:05 start create.\n',encoding='utf-8')
    times=['2024-01-13 05:54:05','2024-01-13 05:59:00','2024-01-13 05:59:05','2024-01-13 06:04:00']
    def write(p,header,rows):
        with p.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f); w.writerow(header); w.writerows(rows)
    write(metrics/'instance.csv',['timestamp','details-v1-abcdef12-abcde_cpu'],[[t,v] for t,v in zip(times,[1,2,8,9])][::-1])
    write(metrics/'svc_metric.csv',['timestamp','details-v1&cpu_usage'],[[t,v] for t,v in zip(times,[1,2,8,9])])
    write(metrics/'graph.csv',['source','destination','timestamp'],[
        ['details-v1-abcdef12-abcde','server-1',times[1]],
        ['details-v1-abcdef12-abcde','future-server',times[3]]])
    nodes=case/'node'; nodes.mkdir()
    write(nodes/'node.csv',['timestamp','(node)server-1_edge_network','(node)server-1_cpu'],[[t,v,v] for t,v in zip(times,[1,2,8,9])])
    return case

def test_native_hosts_snapshot_and_unordered_series(tmp_path):
    request=MicroCERCLAdapter().load(fixture_case(tmp_path))
    assert len([e for e in request.graph.entities if e.kind=='host'])==1
    assert len([r for r in request.graph.relations if r.kind=='deployment'])==1
    assert len([r for r in request.graph.relations if r.kind=='ownership'])==1
    assert request.audit['quality']['sorted_metric_files']==1
    assert request.audit['root_match_count']==1
    assert all(tuple(sorted(s.timestamps))==s.timestamps for s in request.observations)
    assert request.window.detection_start==request.audit['trigger_epoch']

def test_root_label_does_not_change_graph_or_observations(tmp_path):
    case=fixture_case(tmp_path); before=MicroCERCLAdapter().load(case)
    (case.parent/'details_label.txt').write_text('different-root_cpu_load_1\n2024-01-13 13:59:05 start create.\n',encoding='utf-8')
    after=MicroCERCLAdapter().load(case)
    assert before.graph==after.graph
    assert before.observations==after.observations
    assert before.window==after.window

def test_exclude_shared_source_service_resource_metrics(tmp_path):
    request=MicroCERCLAdapter().load(
        fixture_case(tmp_path), excluded_metric_files=('svc_metric.csv',))
    metric_ids={series.metric_id for series in request.observations}
    assert all('svc_metric.csv' not in metric_id for metric_id in metric_ids)
    assert any('instance.csv' in metric_id for metric_id in metric_ids)
    assert request.audit['excluded_metric_files']==['svc_metric.csv']
    assert request.graph.metadata['excluded_metric_files']==['svc_metric.csv']
