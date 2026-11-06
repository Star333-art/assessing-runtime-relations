# 项目说明

## 1. 项目目标

本项目为论文 *Assessing Runtime Relations under Incomplete Entity
Observations* 提供实验实现与可复现证据。研究固定指标评分、实体绑定、候选集合和一步传播公式，改变 Deployment、Ownership 与 Dependency 等运行时关系，并通过目标层直接实体分数屏蔽、得分置换、统一源得分、度数基线和保度重连，判断排名恢复是否依赖正确的得分—端点对齐。

论文主张的是一套受控评估协议，而不是一组能够无条件迁移的固定关系配置。

## 2. 目录分工

| 路径 | 用途 |
|---|---|
| `src/ls_baro/` | 当前统一运行图、数据适配、评分和一步传播实现 |
| `tests/` | 当前实现的回归与适配器测试 |
| `tools/` | 当前论文使用的实验、汇总、敏感性与审计脚本 |
| `output/` | 已冻结的逐案例结果、协议、校验记录和汇总表 |
| `wasted/` | 不进入当前论文证据链的历史、试验性或已被替代代码 |
| `../paper/JSS_20261030/JSS_entity_evidence_20261026/` | 英文稿、中文稿、图表、证据快照和 Overleaf 交付包 |

`wasted/` 不是删除区。文件保留原始内容，可按 `wasted/README.md` 的映射恢复。

## 3. 项目证据链

### LSDB

- `run_ls_relation_split.py`：88 例、八种关系配置的原始观测消融。
- `tools/analyze_ls_relation_split.py`：分层结果和归一化审计。
- `tools/verify_lsdb_coverage.py`：实体、关系端点和适配覆盖核验。
- `tools/complete_entity_observation_controls.py`：LSDB 直接实体分数屏蔽及结构对照。

### AIOps2025

- `tools/download_aiops2025.ps1`：从公开 AgenticOpsEval 仓库检出 AIOps2025 数据的 PowerShell 下载脚本。
- `tools/evaluate_aiops2025_ownership.py`：冻结实体分数与关系适配的基础运行。
- `tools/analyze_aiops2025_ownership.py`：关系覆盖和权重机制审计。
- `tools/summarize_aiops2025_lsdb_protocol.py`：按原生根因层的 272 例汇总。
- `tools/evaluate_aiops2025_host_zero.py`：Host 直接分数置零实验。
- `tools/evaluate_aiops2025_observability_and_rewire.py`：屏蔽梯度和保度重连。
- `tools/analyze_aiops2025_single_entity_mechanism.py`：传播消息主导案例审计。
- `tools/preprocessing_sensitivity.py` 与 `tools/recompute_lsdb_protocol_excl_gap_cases.py`：预处理和覆盖缺口稳健性检查。

### MicroCERCL

- `tools/audit_microcercl.py`：公开数据读取、案例审计和冻结四臂结果。
- `run_microcercl_hipster_relation_split.py`：产生论文所用 Hipster 八臂历史输出。
- `tools/evaluate_microcercl_layers.py`：按目录编码 Pod 根因重放分层评价。
- `tools/summarize_microcercl_lsdb_protocol.py`：形成 615 例原生 Pod 口径汇总。
- `tools/evaluate_microcercl_relation_mechanisms.py`：关系机制对照。
- `tools/evaluate_microcercl_shared_source_sensitivity.py`：排除实例聚合 Service 资源指标后的敏感性分析。

### RCAEval RE2（支撑性边界实验）

RE2 没有被舍弃，但不属于 LSDB、AIOps2025 和 MicroCERCL 所执行的完整屏蔽—机制对照实验。论文使用 90 个 Online Boutique Service 根因案例，将其作为异构关系扩展的边界证据：图中包含 Metric、LogSource、Operation 和 Service，采用前向两步传播、`lambda=0.05`，不含原生 Host--Pod Deployment，因此不能与一步双向 Host/Pod/Service 核心协议合并。

- `../baro-main/run_heterogeneous_graph_component_ablation.py`：论文采用的冻结结果重放入口，比较 BARO only、Ownership、Ownership + Service calls、Ownership + Operation calls 和 Full。
- `../baro-main/output/re2_heterogeneous_dedup_components_l0p05_forward/`：冻结的 90 例逐案例结果、运行配置和组件汇总。
- `tools/audit_extended_dataset_roles.py`：从冻结逐案例排名重新计算 Top-1/3/5 与 Avg@5，并生成配对差值和探索性置信区间。
- `output/extended_dataset_roles_20261028/REPORT.md`：论文采用的 RE2 汇总及其支撑性证据角色说明。

历史组件文件中的 `avg5` 字段实际保存的是 reciprocal rank。论文中的 RE2 Avg@5 没有直接使用该字段，而是由 `audit_extended_dataset_roles.py` 按当前定义从逐案例 `rank` 重新计算。结果仅支持“新增异构关系未产生可辨识的 Avg@5 增益，且 Top-5 存在天花板”；不能写成整个基线已经饱和，也不能用于判定核心协议中的对齐依赖恢复。历史的 raw-telemetry Route-B 生成器引用了一个当前工作区已经缺失的图构建模块，因而已移入 `../baro-main/wasted/historical_re2_generation/`，不作为可运行代码交付。投稿代码包提供冻结逐案例结果、配置、来源哈希和可验证的重放/复算脚本，不声称能够从 RE2 原始遥测端到端重建该历史支撑实验。

重放冻结组件消融：

```powershell
Set-Location D:/AIOps/baro-main
& $python ./run_heterogeneous_graph_component_ablation.py `
  --output output/re2_heterogeneous_dedup_components_l0p05_forward `
  --lambda 0.05 `
  --direction forward `
  --replay-existing
```

从冻结结果复算论文口径：

```powershell
Set-Location D:/AIOps/LS-baro
& $python ./tools/audit_extended_dataset_roles.py
```

### 跨数据集机制与边界

- `tools/evaluate_relation_evidence_mechanisms.py`：LSDB 与 AIOps2025 的统一机制矩阵。
- `tools/evaluate_lambda_mask_sensitivity.py`：原始、部分屏蔽和完全屏蔽条件下的 λ 检查。
- `run_aiops2022_ablation.py`、`run_aiops2022_native.py`：AIOps2022 边界实验。
- `run_relation_supplements.py`、`tools/analyze_relation_supplements.py`：LSDB 与 MicroCERCL 的补充传播和固定参考分母实验。
- `tools/audit_extended_dataset_roles.py`：AIOps2022 与 RE2 的论文角色及配对汇总。
- `tools/plot_entity_observation_followup.py`：生成冻结跟进实验的图形资产。

## 4. 论文目录中的构建脚本

论文目录为：

```text
D:/AIOps/paper/JSS_20261030/JSS_entity_evidence_20261026
```

其中：

- `build_assets.py` 从 `LS-baro/output/` 的冻结结果生成论文图表；
- `finalize_delivery.py` 复制证据快照、执行引用与 PDF 完整性检查并生成 Overleaf ZIP；
- `build_chinese_word.py` 生成对应的中文 Word 稿；
- `verify_references.py` 用于参考文献 DOI 元数据复核；
- `main.tex`、`references.bib`、`tables/` 和 `figures/` 构成英文期刊稿源码。

## 5. 推荐运行顺序

在 `D:/AIOps/LS-baro` 中安装当前包并运行测试：

```powershell
$python = "D:/anaconda3/python.exe"
& $python -m pip install -e .
& $python -m pytest -q
```

需要重新生成论文图表时：

```powershell
Set-Location D:/AIOps/paper/JSS_20261030/JSS_entity_evidence_20261026
& $python build_assets.py
```

随后使用 Overleaf 或本地 LaTeX 编译 `main.tex`。在 `main.pdf`、`main.log` 和 `main.bbl` 已生成后，可执行：

```powershell
& $python finalize_delivery.py
& $python build_chinese_word.py
```

## 6. 复现口径

- 已知公开故障发生时间和根因所在层，只在原生根因层内排序；
- 所有主表使用精确并列期望评分；
- 屏蔽对象是评分后的直接实体分数，不等同于原始遥测或采集器缺失；
- 核心传播为固定一步、双向计算边、源行归一化和 `lambda=0.1`；
- 完全屏蔽时，正值 λ 仅统一缩放传播分数，不改变同一任务内排序；
- 区间跨零表示额外优势尚未建立，不表示真实关系与对照等效；
- 当前区间与多重比较结果均按论文所述作为探索性证据解释。

## 7. 维护规则

1. 新脚本只有在产生当前论文表、图、逐案例结果或必要审计时才放在根目录或 `tools/`。
2. 一次性迁移脚本、被替代实现、未进入正文或附录的探索代码放入 `wasted/`。
3. 不覆盖冻结输出；新实验使用带日期或协议名称的新目录。
4. 每个主实验保留协议、随机种子、来源路径、源码哈希和逐案例结果。
5. 修改评分、聚合、候选或传播公式时，必须重新计算所有受影响结果，不能只更新论文文字。

