# 参考文献核验记录

核验日期：2026-10-26。共19条正文引用，未使用填充性未引用条目。

## 出版书目

Crossref官方API已核验BARO、LatentScope、TrinityRCL、Eadro、RCAEval、MicroIRC、CIRCA和ASE 2024实证研究的题名、作者、年份、刊会、页码与DOI，原始响应见`reference_verification.json`。

核验中发现TrinityRCL作者表需包含Haifeng Shen，已修正。

MicroRCA的Crossref直接查询出现SSL连接中断，使用IEEE论文页面及作者公开资料交叉核查：

- https://ieeexplore.ieee.org/document/9110353/
- https://doi.org/10.1109/NOMS47738.2020.9110353
- 作者：Li Wu, Johan Tordsson, Erik Elmroth, Odej Kao；NOMS 2020。

MicroIRC另与用户提供的原版论文首页核对，期刊为Journal of Systems and Software 216 (2024) 112145，DOI为10.1016/j.jss.2024.112145。

## 预印本及软件论文

以下题名、作者与版本来自原始发布页：

- PyRCA：https://arxiv.org/abs/2306.11417
- MicroCERCL：https://arxiv.org/abs/2406.13604
- TORAI：https://arxiv.org/abs/2604.13522；页面注明FSE 2026接收，本文引用其arXiv版本，不推测未核对的正式DOI。
- Does Graph Structure Earn Its Place：https://arxiv.org/abs/2609.27069；明确标注预印本。
- AgenticOpsEval相关基准论文：https://arxiv.org/abs/2606.29193；明确标注预印本。
- scikit-learn：https://jmlr.org/papers/v12/pedregosa11a.html。

## 数据和仓库

- https://github.com/eBay/LatentScope
- https://github.com/WDCloudEdge/MicroCERCL
- https://www.aiops.cn/gitlab/aiops-live-benchmark/agenticopseval
- https://www.unb.ca/cic/datasets/iomt-dataset-2024.html

正文只使用这些资源支持各自的数据、任务或方法描述，不把所有相关工作作为相同评价口径下的数值基线。
核验API返回的Bootstrap Methods元数据是检索记录的一部分，未在最终参考文献中加入该条。
