# 项目说明

## 1. 项目目标

本项目使用运行时图和异常分数对根因候选实体进行排序。实验固定评分、实体绑定、候选集合和一步传播规则，比较 Deployment、Ownership 与 Dependency 等关系。目标层分数屏蔽、源分数置换、均匀源分数、关系度数与保度重连用于比较不同条件下的排序结果。

## 2. 目录分工

下表中的路径相对于公开仓库根目录。

| 路径 | 用途 |
|---|---|
| `code/ls_baro/` | 核心实现、数据适配、实验与审计脚本、测试 |
| `code/re2_support/` | RCAEval RE2 冻结结果重放代码 |
| `results/core_evidence/` | 核心实验的处理后证据、协议与校验记录 |
| `results/core_evidence/final_requirements/` | 匹配案例清单、聚类重采样协议与结果、重连审计汇总 |
| `results/aiops2022/` | AIOps2022 边界实验的保留子集 |
| `results/re2/` | RE2 冻结逐案例结果、运行配置和组件汇总 |
| `results/extended_roles/` | AIOps2022 与 RE2 支撑性实验的配对汇总 |
| `provenance/` | 证据来源、文件哈希和审计记录 |

## 3. 数据与实验入口

本节中的核心实验脚本路径相对于 `code/ls_baro/`。

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
- `run_microcercl_hipster_relation_split.py`：Hipster 子集的八种关系配置对比。
- `tools/evaluate_microcercl_layers.py`：按目录编码 Pod 根因重放分层评价。
- `tools/summarize_microcercl_lsdb_protocol.py`：形成 615 例原生 Pod 口径汇总。
- `tools/evaluate_microcercl_relation_mechanisms.py`：关系机制对照。
- `tools/evaluate_microcercl_shared_source_sensitivity.py`：排除实例聚合 Service 资源指标后的敏感性分析。

### RCAEval RE2

RE2 重放覆盖 90 个 Online Boutique Service 根因案例。图中包含 Metric、LogSource、Operation 和 Service，采用前向两步传播，`lambda=0.05`。

- [RE2 重放脚本](../re2_support/run_heterogeneous_graph_component_ablation.py)：比较 BARO only、Ownership、Ownership + Service calls、Ownership + Operation calls 和 Full。
- [RE2 结果目录](../../results/re2/)：冻结的 90 例逐案例结果、运行配置和组件汇总。
- `tools/audit_extended_dataset_roles.py`：从冻结逐案例排名重新计算 Top-1/3/5 与 Avg@5，并生成配对差值和探索性置信区间。
- [补充实验结果](../../results/extended_roles/)：AIOps2022 与 RE2 的配对汇总。

冻结组件文件中的 `avg5` 字段保存的是倒数排名。复算脚本从逐案例 `rank` 计算 Avg@5。重放的输入与限制见 [ARTIFACT_LIMITATIONS.md](../../ARTIFACT_LIMITATIONS.md)。

从仓库根目录按 README 的说明[重放 RE2](../../README.md#replay-the-supporting-re2-result)。

### 跨数据集机制与边界

- `tools/evaluate_relation_evidence_mechanisms.py`：LSDB 与 AIOps2025 的统一机制矩阵。
- `tools/evaluate_lambda_mask_sensitivity.py`：原始、部分屏蔽和完全屏蔽条件下的 λ 检查。
- `run_aiops2022_ablation.py`、`run_aiops2022_native.py`：AIOps2022 边界实验。
- `run_relation_supplements.py`、`tools/analyze_relation_supplements.py`：LSDB 与 MicroCERCL 的补充传播和固定参考分母实验。
- `tools/audit_extended_dataset_roles.py`：AIOps2022 与 RE2 的配对汇总。
- `tools/plot_entity_observation_followup.py`：生成冻结跟进实验的图形资产。

## 4. 运行与核验

在仓库根目录使用当前 Python 环境安装依赖并运行核心测试：

```powershell
python -m pip install -r requirements.txt
python -m pip install -e code/ls_baro
python -m pytest -q code/ls_baro/tests
```

原始遥测的项目来源和获取说明见 [THIRD_PARTY.md](../../THIRD_PARTY.md)。核验结果时，可查阅[核心实验快照](../../results/core_evidence/)和[补充审计文件](../../results/core_evidence/final_requirements/)。证据来源和文件哈希见 [provenance](../../provenance/) 与 [MANIFEST_SHA256.csv](../../MANIFEST_SHA256.csv)。

## 5. 复现口径

- 已知公开故障发生时间和根因所在层，只在原生根因层内排序；
- 核心实验按并列分数下的期望排名计算指标；
- 屏蔽对象是评分后的直接实体分数，不等同于原始遥测或采集器缺失；
- 核心传播为固定一步、双向计算边、源行归一化和 `lambda=0.1`；
- 完全屏蔽时，正值 λ 仅统一缩放传播分数，不改变同一任务内排序；
- 区间跨零表示额外优势尚未建立，不表示真实关系与对照等效；
- 置信区间与多重比较用于探索性分析。

