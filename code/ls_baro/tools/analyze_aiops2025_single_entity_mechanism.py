"""Arithmetic mechanism audit on the fixed 272-case single-entity cohort."""
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/aiops2025_lsdb_protocol_20261023'
def main():
    a=pd.read_csv(OUT/'label_audit.csv');a=a[a.status=='single_entity_main']
    r=pd.read_csv(OUT/'case_results.csv')
    ranks=pd.read_csv(ROOT/'output/aiops2025_ownership_pilot/rankings.csv')
    edge=pd.read_csv(ROOT/'output/aiops2025_ownership_pilot/ownership_evidence.csv')
    transitions=[];losses=[]
    for level,g in r.groupby('level'):
        p=g.pivot(index='case_id',columns='arm',values='rank')
        old=p.mapping<=1;new=p.containment<=1
        transitions.append(dict(level=level,cases=len(p),gained=int((~old&new).sum()),
            lost=int((old&~new).sum()),baseline_hits=int(old.sum()),containment_hits=int(new.sum())))
    for x in a.itertuples():
        target=json.loads(x.targets)[0]
        case=r[r.case_id==x.case_id].set_index('arm')
        if case.loc['mapping','rank']!=1 or case.loc['containment','rank']<=1:continue
        b=ranks[(ranks.case_id==x.case_id)&(ranks.arm=='mapping')].set_index('entity')
        c=ranks[(ranks.case_id==x.case_id)&(ranks.arm=='containment')].set_index('entity')
        w=c[c['rank']==1].iloc[0];winner=c[c['rank']==1].index[0]
        item=dict(case_id=x.case_id,level=x.level,target=target,winner=winner,
            root_base=b.loc[target,'base'],root_final=c.loc[target,'score'],
            winner_base=w.base,winner_final=w.score,winner_message=w.score-w.base,
            root_message=c.loc[target,'score']-b.loc[target,'base'])
        if x.level=='service':
            item['root_observed_ownership_edges']=int(((edge.case_id==x.case_id)&(edge.service==target)).sum())
        losses.append(item)
    loss=pd.DataFrame(losses);transition=pd.DataFrame(transitions)
    assert len(loss)==43
    assert (loss.winner_message.abs()>loss.winner_base.abs()).all()
    loss.to_csv(OUT/'top1_loss_messages.csv',index=False)
    transition.to_csv(OUT/'top1_transitions.csv',index=False)
    pre=pd.read_json(ROOT/'output/preprocessing_aiops2025_20261023/cases.jsonl',lines=True)
    pre=pre[pre.case_id.isin(a.case_id)&pre.arm.isin(['mapping','containment'])]
    preprocessing=[]
    for (variant,arm),g in pre.groupby(['variant','arm']):
        assert len(g)==272
        preprocessing.append(dict(variant=variant,arm=arm,cases=len(g),
            top1=(g['rank']<=1).mean(),top3=(g['rank']<=3).mean(),top5=(g['rank']<=5).mean(),
            avg5=sum((g['rank']<=k).mean() for k in range(1,6))/5))
    pd.DataFrame(preprocessing).to_csv(OUT/'near_constant_sensitivity.csv',index=False)
    service=loss[loss.level=='service']
    findings=dict(top1_gained=int(transition.gained.sum()),top1_lost=int(transition.lost.sum()),
        top1_loss_winners_message_dominant=43,
        service_top1_losses=len(service),
        service_top1_losses_without_observed_root_ownership=int((service.root_observed_ownership_edges==0).sum()),
        service_top1_losses_with_zero_root_increment=int((service.root_message==0).sum()),
        interpretation='Arithmetic explanation of rank flips; not proof of physical causes or dataset noise',
        sensitivity='Fixed optional BARO near-constant rule only; no full default missing-data/unit replay claimed')
    (OUT/'mechanism_verification.json').write_text(json.dumps(findings,indent=2),encoding='utf-8')
    lines=['# AIOps2025 单实体口径：下降机制核验','',
        '同一272例、相同候选与初始分数，比较 Mapping-only 和 Containment。',
        '本分析解释分数与排序变化，不将统计关联当作已证实的物理传播原因。','',
        '| 层级 | N | 基线Top-1命中 | Containment命中 | 新增命中 | 丢失命中 |',
        '|---|---:|---:|---:|---:|---:|']
    for t in transition.itertuples():lines.append(f'| {t.level} | {t.cases} | {t.baseline_hits} | {t.containment_hits} | {t.gained} | {t.lost} |')
    lines+=['', '43例Top-1丢失案例的新首位候选，传播增量的绝对值都超过其自身初始分数。该结果证明新首位主要受传入消息影响，但不单独证明源指标是噪声。',
        f'25例Service Top-1丢失案例中，{findings["service_top1_losses_without_observed_root_ownership"]}例没有观测到根因服务的ownership边；{findings["service_top1_losses_with_zero_root_increment"]}例根因服务传播增量为零。这是关系覆盖与增益不对称的线索，尚非全部下降的因果归因。',
        '', '## 固定近常数过滤对照', '',
        '规则来自BARO可选drop_near_constant的默认10%阈值，分别检查正常窗和异常窗；不是完整BARO数据读取预处理。以下仅重汇总同一272例，不混入73例网络关系故障。','',
        '| 预处理 | 配置 | Top-1 | Top-3 | Top-5 | Avg@5 |','|---|---|---:|---:|---:|---:|']
    for t in pd.DataFrame(preprocessing).itertuples():lines.append(f'| {t.variant} | {t.arm} | {t.top1:.2%} | {t.top3:.2%} | {t.top5:.2%} | {t.avg5:.4f} |')
    lines+=['', '过滤减少部分Top-1下降，但Containment仍低于同预处理的Mapping基线；Top-3、Top-5和Avg@5并未全面改善。因此不能把下降简单归因于近常数指标噪声，也不能称预处理已恢复跨数据集收益。',
        'LSDB自身也存在大幅传播增量；其12个Host案例的Host初始分数均为零，而AIOps2025的82个Host案例仅1例全部Host初始分数为零。因此两者的直接证据起点不同，不能只凭传播分数大就解释全部迁移差异。']
    (OUT/'MECHANISM.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(findings,indent=2));print(transition.to_string(index=False))
    print(pd.DataFrame(preprocessing).to_string(index=False))
if __name__=='__main__':main()
