# 碳酸锂因子研究经验沉淀（HAM + li-factor-research 合并版）

> **归档位置**：`/home/ubuntu/data-harbor/ham_project_archive/factor_research_summary.md`
> **合并来源**：HAM 项目 (exp410-exp437) + li-factor-research (6 候选因子单因子检验)
> **归档时间**：2026-09-11
> **供其他 Agent 阅读使用**

---

## 一、项目全貌

| 项目 | 周期 | 目标 | 方法 | 结论 |
|:---|:---|:---|:---|:---|
| HAM (exp410-exp437) | 2024-02 ~ 2026-09 | 碳酸锂交易者分歧结构因子 | 异质交易模型+滚动回测+影子盘 | 训练IC=+0.450→盲测IC=-0.592，信号反转，不做预测因子 |
| li-factor-research | 2026-09-11 | 6个新候选日频因子单因子检验 | 尾部20%盲测+Bonferroni+正交残差IC | 6/6全部拒绝，2/6信号反转，不做预测因子 |

**两轮独立验证结论完全一致：碳酸锂日频收益预测因子不具备样本外稳健性。**

---

## 二、核心结论（必读）

1. **不做碳酸锂日频收益预测因子研发。** 两轮独立验证（HAM 19特征 + li-factor 6因子）均失败。
2. **信号反转是碳酸锂品种的结构性特征，不是单一模型问题。** HAM 反转比 -1.32x，F5 跨期结构反转比 -0.47x，方向相反但模式相同。
3. **根因：763 交易日样本量偏少 + 高噪声（年化>60%）+ 产业结构多次突变。** 不是方法论缺陷。
4. **F5 期限结构、F6 拥挤度保留为市场状态/风控观察指标**，不用于涨跌预测。
5. **后续方向：** 方向 A（降频周/月频检验）或方向 B（行情状态过滤器，推荐）。

---

## 三、检验方法论（可复用模板）

### 3.1 因子检验标准流程

```
1. 因子构建：从本地 DB 读取原始数据，计算因子值（日频对齐）
2. 尾部 20% 盲测：按时间排序，前 80% 训练，后 20% 盲测
3. 全样本 IC + 训练集 IC + 盲测集 IC（Spearman Rank IC）
4. 置换检验（500 次随机置换，训练集上计算 p 值）
5. Bonferroni 校正（置换 p × 因子总数）
6. 正交残差 IC（用 close/动量回归收益率，取残差后算因子 IC）
7. 信号反转检测（训练 IC × 盲测 IC < 0 → reject_critical）
8. 判定：accept / reject / reject_critical / reject_insufficient
```

### 3.2 判定规则

| 判定 | 条件 |
|:---|:---|
| ✅ accept | 无信号反转 + 置换 p<0.05 + Bonferroni p<0.05 + 正交残差IC>0.03 + 盲测IC>0.02 |
| ❌ reject | 未通过任一条件但无信号反转 |
| ❌ reject_critical | 信号反转（训练/盲测 IC 异号）|
| ❌ reject_insufficient | 有效样本 < 30 |

### 3.3 HAM 6 条经验教训

1. ❌ 不能只做全样本回测——必须设置尾部 20% 盲测试集
2. ❌ 不能只看置换检验——全样本 p 值可能掩盖样本外反转
3. ❌ 不能只看原始 IC——必须做正交残差 IC
4. ❌ 不能盲目增加因子数量——HAM 19 特征正交筛选后仅剩 1 个，共线是隐患
5. ✅ 必须区分收益预测因子 vs 风控状态因子
6. ✅ 必须报告自由度增量和过拟合风险评估

### 3.4 li-factor-research 新增 2 条经验

7. ❌ 不能只看全样本显著——F5 全样本 p=0.021 但盲测 IC 符号反转，Bonferroni 后 p=0.084
8. ❌ 不能低频基本面日频化——F1/F2/F3 周频 ffill 到日频后，信噪比极低，IC 均不显著

---

## 四、因子结果速查

| 因子 | 样本 | 覆盖率 | 全样本IC | 训练IC | 盲测IC | Bonferroni p | 正交残差IC | 反转 | 判定 |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| F1 开工率20日环比 | 162 | 21.3% | +0.066 | +0.069 | -0.033 | 1.000 | +0.050 | ⚠️是 | reject_critical |
| F2 排产20日环比 | 249 | 32.7% | -0.114 | -0.127 | -0.002 | 0.480 | -0.084 | 否 | reject |
| F3 库存预期差 | 24 | 3.1% | N/A | N/A | N/A | N/A | N/A | — | reject_insufficient |
| F4 现货溢价 | 476 | 62.5% | +0.015 | +0.009 | +0.050 | 1.000 | +0.037 | 否 | reject |
| F5 跨期价差+曲率 | 732 | 96.1% | -0.086** | -0.113** | +0.053 | 0.084 | -0.073 | ⚠️是 | reject_critical |
| F6 拥挤度 | 754 | 99.0% | +0.010 | +0.004 | +0.056 | 1.000 | +0.019 | 否 | reject |

> HAM `ham_dev_mean`: 训练 IC=+0.450 → 盲测 IC=-0.592（反转比 -1.32x）

---

## 五、数据源与资产位置

### 5.1 数据源

| 数据源 | 表 | 频率 | 日期范围 | 路径 |
|:---|:---|:---:|:---:|:---|
| lithium.db prices | 主力合约日OHLCV | 日 | 2023-07~2026-09 | `/home/ubuntu/lithium_calendar/lithium.db` |
| lithium.db lithium_weekly | SMM周频25指标 | 周 | 2023-06~2026-09 | 同上 |
| lithium.db lithium_monthly | SMM月频产量 | 月 | 2017~2026 | 同上 |
| lithium.db contract_daily_all | 全合约日行情 | 日 | 2023-07~2026-09 | 同上 |
| lc_spot.db spot_price | 现货+基差 | 日 | 2023-07~2026-09 | `/home/ubuntu/lc_futures_data/data/lc_spot.db` |

### 5.2 脚本资产

| 产物 | 位置 |
|:---|:---|
| 因子构建脚本 | `lithium-engine/factor_research/scripts/build_factors.py` |
| 因子检验脚本 | `lithium-engine/factor_research/scripts/factor_screen.py` |
| 因子可视化脚本 | `lithium-engine/factor_research/scripts/plot_results.py` |
| 因子日频数据 | `lithium-engine/factor_research/data/factors_daily.csv` |
| 检验结果 JSON | `lithium-engine/factor_research/data/factor_screen_results.json` |
| 因子 IC 图表 | `lithium-engine/factor_research/data/factor_ic_summary.png` |
| 检验报告 | `lithium-engine/factor_research/li_factor_screen_report.md` |
| 本经验文档 | `data-harbor/ham_project_archive/factor_research_summary.md` |
| HAM 项目总结 | `data-harbor/ham_project_archive/ham_full_project_summary.md` |

### 5.3 Git 分支

| 分支 | 用途 | 状态 |
|:---|:---|:---:|
| `main` | 生产主干 | 不受影响 |
| `exp436-research` | HAM 项目归档分支 | 已结题 |
| `li-factor-research` | 本项目分支 | 已结题，已推送 |

---

## 六、后续研究方向

### 方向 A：降频检验（可选，中可行性）

降频至周频/月频，重新检验 F1/F2/F3 的中长期预测效果。前向收益改为 5 日/20 日持有。
- 若方向 A 也失败 → 彻底终结碳酸锂因子挖掘路线
- 预期信噪比提升 5-10x，但样本量仍偏少（140 周/37 月）

### 方向 B：行情状态过滤器（推荐，高可行性）

使用【F5 期限结构 + F6 拥挤度 + HAM 分歧指标】三维组合构建市场状态过滤器。
- 4 类状态：趋势健康 / 趋势拥挤 / 反转信号 / 结构失衡
- 输出：风控仓位乘数（0.3x~1.0x），非方向信号
- 与 HAM exp432"失效指纹=拥挤一致性"衔接
- 无过拟合风险（非预测因子，仅状态分类）

---

*文档生成：2026-09-11 | HAM + li-factor-research 双轮验证完成 | 供所有 Agent 参考*
