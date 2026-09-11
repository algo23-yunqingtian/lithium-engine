# HAM + 因子项目交接文档

> **生成时间**：2026-09-11（最终结题版）
> **分支**：`li-factor-research` (commit `03276da` + `后续归档commit`)
> **状态**：HAM 项目 + li-factor-research 均已结题

---

## 【已结题项目】

### 1. HAM 项目 (exp410-exp437)

**结论**：不再做预测因子研发。HAM 仅作为市场状态观测指标，依靠影子盘持续样本外观测。

**核心证据**：
- 原 HAM `ham_dev_mean` 训练集 IC=+0.450 → 盲测试集 IC=-0.592（信号完全反转 -1.32x）
- 4 组改造（分歧解构/自适应参数/多主体拓展/多因子合成）全部无法改善盲测表现
- 正交残差 IC=0.055 (p=0.192) 不显著，独立信息不足
- HAM 内部高度共线，19 特征正交筛选后仅保留 1 个（保留率 14%）

**保留价值**：✅ 市场分歧/拥挤状态定性观测指标、✅ 投研复盘辅助、✅ 风控辅助（IC 告警体系 OK/WARNING/CRITICAL）

**不再用于**：❌ 价格涨跌预测、❌ Alpha 收益生成、❌ 实盘交易信号、❌ 生产因子库入库

### 2. li-factor-research 项目（6 候选因子单因子检验）

**结论**：6 个候选日频因子全部不满足样本外稳健性要求，全部淘汰。不再继续挖掘碳酸锂日度收益预测因子。

**候选因子清单与结果**：

| # | 因子 | 类型 | 全样本IC | 训练IC | 盲测IC | 信号反转 | 判定 |
|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|
| ① | 上游开工率20日环比 | 供给侧 | +0.066 | +0.069 | -0.033 | ⚠️是 | ❌ reject_critical |
| ② | 动力电池排产20日环比 | 需求侧 | -0.114 | -0.127 | -0.002 | 否 | ❌ reject |
| ③ | 库存预期差 | 预期差 | N/A | N/A | N/A | — | ❌ reject_insufficient |
| ④ | 现货成交溢价 | 价差 | +0.015 | +0.009 | +0.050 | 否 | ❌ reject |
| ⑤ | 跨期价差+曲率 | 期限结构 | -0.086** | -0.113** | +0.053 | ⚠️是 | ❌ reject_critical |
| ⑥ | 拥挤度 | 风控状态 | +0.010 | +0.004 | +0.056 | 否 | ❌ reject |

**核心原因**：碳酸锂仅 763 个交易日，样本量偏少、高噪声（年化>60%）、产业结构多次突变（2023崩盘→2024反弹→2025下行→2026反弹），训练/盲测 IC 反转是品种结构性特征，不是单一模型问题。

**HAM+因子双轮验证一致**：HAM 反转比 -1.32x，F5 跨期结构反转比 -0.47x。信号反转不是 HAM 特有缺陷，而是碳酸锂品种的结构性特征。

**F5/F6 保留定位**：
- F5 跨期价差+曲率 → 市场状态/风控观察指标（不用于涨跌预测）
- F6 拥挤度 → 风控仓位管理辅助（拥挤度>0.7 降低仓位暴露）

### 3. 项目资产归档位置

| 产物 | 位置 |
|:---|:---|
| HAM 代码+报告+数据 | `exp436-research` 分支 commit `1e5a21a` |
| HAM 审计报告 | `reports/ham_final_defense_audit.md` §12 |
| HAM 研究报告 | `reports/exp436_research_report.md` |
| HAM 结题文档 | `model_ham/HAM_Project_Final_Document.md` §4.16 |
| 因子构建脚本 | `factor_research/scripts/build_factors.py` |
| 因子检验脚本 | `factor_research/scripts/factor_screen.py` |
| 因子可视化脚本 | `factor_research/scripts/plot_results.py` |
| 因子 IC 图表 | `factor_research/data/factor_ic_summary.png` |
| 因子日频数据 | `factor_research/data/factors_daily.csv` |
| 检验结果 JSON | `factor_research/data/factor_screen_results.json` |
| 因子检验报告 | `factor_research/li_factor_screen_report.md` |
| Datahub 经验总结（合并版） | `/home/ubuntu/data-harbor/ham_project_archive/factor_research_summary.md` |
| Datahub HAM 项目总结 | `/home/ubuntu/data-harbor/ham_project_archive/ham_full_project_summary.md` |
| Datahub 核心脚本 | `/home/ubuntu/data-harbor/ham_project_archive/scripts/` (9 个 .py) |
| Datahub 关键数据 | `/home/ubuntu/data-harbor/ham_project_archive/data/` (10 个 json/csv) |
| 因子项目分支 | `li-factor-research`（已推送 GitHub） |

---

## 【后续可选研究方向】

### 方向 A：降频至周频/月频，重新检验基本面因子中长期预测效果

| 维度 | 方案 |
|:---|:---|
| 检验频率 | 周频（~140 周）/ 月频（~37 月） |
| 候选因子 | F1 开工率周环比、F2 排产月环比、F3 库存周环比 |
| 前向收益 | 5 日持有 / 20 日持有 |
| 检验框架 | 同本版：尾部 20% 盲测 + Bonferroni + 正交残差 IC |
| 可行性 | 中。样本量仍偏少，但信噪比应提升 5-10x |
| 风险 | 若方向 A 也失败，则彻底终结因子挖掘路线 |

### 方向 B：行情状态过滤器（推荐，优先执行）

| 维度 | 方案 |
|:---|:---|
| 核心思路 | 放弃涨跌预测，转做行情状态识别与风控择时 |
| 输入指标 | F5 跨期价差+曲率、F6 拥挤度、HAM disagreement |
| 状态分类 | 4 类：趋势健康 / 趋势拥挤 / 反转信号 / 结构失衡 |
| 输出 | 风控仓位乘数（0.3x ~ 1.0x），非方向信号 |
| 验证方式 | 影子盘持续采集 + 状态转换矩阵回测 |
| 可行性 | 高。F5/F6 覆盖率>95%，HAM 已有 3 年样本外数据 |
| 与 HAM 衔接 | exp432"失效指纹=拥挤一致性"，F6 拥挤度正是量化该指纹的指标 |

**优先级**：方向 B > 方向 A。方向 B 确定性高、与现有资产衔接好、风险低。

---

## 【HAM 经验教训（所有后续因子研究必须遵守）】

1. ❌ 不能只做全样本回测——必须设置尾部 20% 盲测试集
2. ❌ 不能只看置换检验——全样本 p 值可能掩盖样本外反转
3. ❌ 不能只看原始 IC——必须做正交残差 IC（剔除传统因子后的独立信息）
4. ❌ 不能盲目增加因子数量——HAM 19 特征正交筛选后仅剩 1 个，共线是隐患
5. ✅ 必须区分收益预测因子 vs 风控状态因子
6. ✅ 必须报告自由度增量和过拟合风险评估
7. ❌ 不能只看全样本显著——F5 全样本 p=0.021 但盲测 IC 符号反转，Bonferroni 后 p=0.084
8. ❌ 不能低频基本面日频化——F1/F2/F3 周频 ffill 到日频后信噪比极低

---

## 【服务器清理结果】

| 清理项 | 数量 | 状态 |
|:---|---:|:---:|
| `__pycache__` 目录 | 13 | ✅ 已删 |
| `.pyc` 文件 | 16 | ✅ 已删 |
| `.log` 文件 | 5 | ✅ 已删 |
| `.png` 临时绘图 | 10+ | ✅ 已删 |

**保留**：已提交到 GitHub + Datahub 的脚本/报告/核心数据。禁止删除：main 主干、影子盘运行目录、生产流水线代码。

---

*交接文档生成时间：2026-09-11 | HAM exp410-exp437 + li-factor-research 全流程归档完成 | 双轮验证结论一致：不做碳酸锂日频收益预测因子*
