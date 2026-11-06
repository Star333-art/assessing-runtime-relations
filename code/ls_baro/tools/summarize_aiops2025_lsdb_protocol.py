"""Audit native AIOps2025 fault scope; one labelled entity per main case."""
import hashlib,json
from collections import Counter
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT.parent/'data/AgenticOpsEval/AIOps2025'
SOURCE=ROOT/'output/aiops2025_ownership_pilot'
OUT=ROOT/'output/aiops2025_lsdb_protocol_20261023'
ARMS=['mapping','deployment','ownership','containment']

def summarize(df,scope):
    out=[]
    for arm in ARMS:
        g=df[df.arm==arm]
        assert len(g)==g.case_id.nunique()
        row=dict(scope=scope,arm=arm,cases=len(g))
        for k in range(1,6):
            row[f'top{k}_hits']=int((g['rank']<=k).sum())
            row[f'top{k}']=float((g['rank']<=k).mean())
        row['avg5']=sum(row[f'top{k}'] for k in range(1,6))/5
        out.append(row)
    return pd.DataFrame(out)

def main():
    OUT.mkdir(exist_ok=True)
    labels=[json.loads(l) for l in (DATA/'groundtruth.jsonl').read_text(encoding='utf-8').splitlines()]
    prior=pd.read_csv(SOURCE/'case_audit.csv').set_index('case_id')
    results=pd.read_csv(SOURCE/'case_results.csv')
    rankings=pd.read_csv(SOURCE/'rankings.csv')
    audits=[]
    for label in labels:
        cid=label['uuid'];level={'node':'host','pod':'pod','service':'service'}[label['instance_type']]
        targets=label['instance'] if isinstance(label['instance'],list) else [label['instance']]
        targets=list(dict.fromkeys(map(str,targets)))
        relation=bool(label.get('source') or label.get('destination'))
        if relation:status='network_relation_separate'
        elif len(targets)!=1:status='multiple_identifiers_need_resolution'
        elif prior.loc[cid,'target_observed']<1:status='single_target_not_in_metric_adapter'
        else:status='single_entity_main'
        audits.append(dict(case_id=cid,native_instance_type=label['instance_type'],level=level,
            fault_type=label['fault_type'],targets=json.dumps(targets),
            source=label.get('source',''),destination=label.get('destination',''),
            status=status,target_observed=int(prior.loc[cid,'target_observed'])))
    audit=pd.DataFrame(audits)
    main=audit[audit.status=='single_entity_main']
    assert main.level.value_counts().to_dict()=={'service':122,'host':82,'pod':68}
    selected=results[results.case_id.isin(main.case_id)].copy()
    assert len(selected)==272*4 and not selected.duplicated(['case_id','arm']).any()
    for row in main.itertuples():
        target=json.loads(row.targets)[0]
        g=rankings[(rankings.case_id==row.case_id)&(rankings.entity==target)]
        expected=selected[selected.case_id==row.case_id].set_index('arm')['rank']
        assert len(g)==4
        for r in g.itertuples():assert r.rank==expected[r.arm]
    audit.to_csv(OUT/'label_audit.csv',index=False)
    selected.to_csv(OUT/'case_results.csv',index=False)
    summary=summarize(selected,'all')
    by_level=pd.concat([summarize(g,level) for level,g in selected.groupby('level')])
    summary.to_csv(OUT/'summary.csv',index=False)
    by_level.to_csv(OUT/'by_level.csv',index=False)
    info=dict(native_counts=dict(Counter(audit.native_instance_type)),
        status_counts=dict(Counter(audit.status)),main_cases=272,
        main_level_counts=dict(Counter(main.level)),
        native_label_semantics='README: instance_type=fault layer; instance=fault injection object',
        rank_policy='known native fault layer, deterministic saved score/name order',
        formula_and_preprocessing_unchanged=True,
        network_relation_policy='73 source/destination cases excluded from single-entity main; not treated as aliases',
        unresolved_policy='55 database Pod cases require identifier resolution and metric-adapter expansion',
        source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(__file__),DATA/'groundtruth.jsonl',DATA/'README.md',SOURCE/'case_results.csv',SOURCE/'rankings.csv']})
    (OUT/'verification.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    lines=['# AIOps2025 按 LSDB 单实体根因口径的评价', '',
        '原始 groundtruth 明确规定 instance_type 为故障层级，instance 为故障注入对象。原生分布为 Host/Node 82、Pod 123、Service 195；不是由部署或归属关系扩展出的三类标签。',
        '', '## 评价范围', '',
        '- 单实体主评价272例：Host 82、Pod 68、Service 122。列表只有一个名称的标签与标量标签同等处理。',
        '- 73例Service层网络故障带source/destination，指向服务间故障关系；不把两端任一命中直接当作单实体根因正确，不并入主评价。',
        '- 55例数据库Pod标签包含具体Pod及较粗名称等多个标识，且当前指标适配未覆盖目标；需先解析真实实体并补适配，不能作为零命中的算法失败。',
        '- 每例只在原始标注的实体层内评价一次；不将Pod所在Host或所属Service另计为根因。',
        '- 原始预处理与公式保持不变；近常数过滤敏感性另存，不混入本表。', '',
        '| 配置 | N | Top-1 | Top-3 | Top-5 | Avg@5 |','|---|---:|---:|---:|---:|---:|']
    for r in summary.itertuples():lines.append(f'| {r.arm} | {r.cases} | {r.top1:.2%} | {r.top3:.2%} | {r.top5:.2%} | {r.avg5:.4f} |')
    lines+=['', 'Avg@5为Top-1到Top-5五项命中率的均值。历史345例主表包含上述73例网络关系故障，故应标记为旧的any-endpoint评价，不能与这里的单实体主结果混称同一协议。',
        '该子集仍仅覆盖当前指标适配与观测到的关系；ownership覆盖不完整。不能代表全部400例的完整基准复现。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(info,ensure_ascii=False,indent=2)[:1400])
    print(summary[['scope','arm','cases','top1','top3','top5','avg5']].to_string(index=False))
    print(by_level[['scope','arm','cases','top1','top3','top5','avg5']].to_string(index=False))

if __name__=='__main__':main()
