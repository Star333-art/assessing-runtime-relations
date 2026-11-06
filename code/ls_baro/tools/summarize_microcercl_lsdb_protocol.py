"""One case, one annotated entity layer: LSDB-style MicroCERCL evaluation."""
import hashlib
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'output/microcercl_layer_evaluation_20261023'
OUT=ROOT/'output/microcercl_lsdb_protocol_20261023'
ORDER=['Mapping-only','Deployment','Ownership','Dependency','Containment',
       'Deployment+Dependency','Ownership+Dependency','Full']

def summarize(df,scope):
    rows=[]
    for arm in ORDER:
        g=df[df.arm==arm]
        assert len(g)>0 and g.case_id.nunique()==len(g)
        assert (g['rank']>=1).all() and (g['rank']<=g.candidates).all()
        assert (g['rank']%1==0).all()
        row=dict(scope=scope,arm=arm,cases=len(g))
        for k in range(1,6):
            row[f'top{k}_hits']=int((g['rank']<=k).sum())
            row[f'top{k}']=float((g['rank']<=k).mean())
        row['avg5']=sum(row[f'top{k}'] for k in range(1,6))/5
        assert abs(row['avg5']-g['avg5'].mean())<1e-12
        rows.append(row)
    return pd.DataFrame(rows)

def main():
    OUT.mkdir(exist_ok=True)
    audit=pd.read_csv(SOURCE/'label_audit.csv')
    results=pd.read_csv(SOURCE/'case_results.csv')
    eligible=audit[audit.pod_reference_mode=='directory_exact']
    assert len(eligible)==615 and eligible.case_id.nunique()==615
    assert eligible.ownership_consistent.all()
    # Directory references identify injected Pods, not separately injected
    # Hosts or Services. Host/service attribution rows are not observations
    # in the main root-entity accuracy estimate.
    main=results[(results.level=='pod')&(results.reference=='directory_exact')].copy()
    assert set(main.case_id)==set(eligible.case_id)
    assert len(main)==615*8
    assert not main.duplicated(['case_id','arm']).any()
    assert (main.groupby('case_id').arm.nunique()==8).all()
    assert (main.groupby('case_id').candidates.nunique()==1).all()
    summary=summarize(main,'all')
    by_application=pd.concat([summarize(g,app) for app,g in main.groupby('application')])
    main.to_csv(OUT/'case_results.csv',index=False)
    summary.to_csv(OUT/'summary.csv',index=False)
    by_application.to_csv(OUT/'by_application.csv',index=False)
    excluded=audit[audit.pod_reference_mode!='directory_exact']
    excluded.to_csv(OUT/'excluded_from_main.csv',index=False)
    verification=dict(protocol='LSDB-style known-true-entity-layer deterministic ranking',
        cases=615,entity_layer='pod',application_counts=eligible.application.value_counts().to_dict(),
        candidate_scope='all original graph Pods across monitored namespaces; no target-label filtering',
        rank_policy='descending final score, entity name, stable ID; same as saved engine',
        avg5='mean of Top-1, Top-2, Top-3, Top-4, Top-5',
        excluded_unique_ownership_derived=207,excluded_directory_metric_mismatch=60,
        source_cases=882,root_references='exact directory-encoded Pod target plus metric-entity identity check',
        no_independent_injection_manifest_verification=True,
        host_and_service_attribution_rows_pooled=False,scoring_or_formula_changed=False,
        historical_replay_max_score_error=json.loads((SOURCE/'verification.json').read_text())['max_abs_score_error'],
        sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(__file__),SOURCE/'case_results.csv',SOURCE/'label_audit.csv']})
    (OUT/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    lines=['# MicroCERCL 按 LSDB 口径的根因实体评价', '',
        '每个故障案例只在目录注入目标对应的 Pod 层评价一次，不混合宿主机归属或服务归属结果。',
        '主表包含 615 例：Hipster 269 例，SockShop 346 例。候选为原运行图中所有监控命名空间的 Pod。',
        '207 例 Bookinfo 仅有 ownership 推导实例参考、60 例目录名称无法精确匹配，因此不纳入主表。',
        '这里的参考来自目录编码及实体一致性核验，尚无独立注入配置交叉证明。', '',
        '固定原始预处理、BARO signed RobustScaler、raw/max、lambda=0.1、双向、row 归一化和单步传播。',
        'Avg@5 为 Top-1 到 Top-5 五项命中率的均值。排名口径与 LSDB 主表相同。', '',
        '| 配置 | N | Top-1 | Top-3 | Top-5 | Avg@5 |',
        '|---|---:|---:|---:|---:|---:|']
    for r in summary.itertuples():
        lines.append(f'| {r.arm} | {r.cases} | {r.top1:.2%} | {r.top3:.2%} | {r.top5:.2%} | {r.avg5:.4f} |')
    lines+=['', '结论：Deployment 的 Pod 层总体变化很小；Ownership 与 Containment 提高 Top-5，降低 Top-1、Top-3 和 Avg@5。Full 的 Top-5 最好，但 Top-1 和 Avg@5 仍低于基线。不能将原服务归属任务的正收益写成实例根因定位的普遍收益。',
        '', '已核验：每个配置615例、无重复案例、候选集规模一致、Top-1到Top-5逐例重算与已存Avg@5一致；原始评分重放误差为0。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(summary[['arm','cases','top1_hits','top3_hits','top5_hits','top1','top3','top5','avg5']].to_string(index=False))
    print('\nBy application (Mapping and Containment):')
    print(by_application[by_application.arm.isin(['Mapping-only','Containment'])][
        ['scope','arm','cases','top1','top3','top5','avg5']].to_string(index=False))

if __name__=='__main__':main()
