# 第五轮实验交接文档：K=4 GMM聚类解析复盘（exp404，停用HAM预测）

> **交接时间**：2026-09-09
> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/exp404_cluster_analysis/`
> **任务性质**：停用HAM因子全部预测方向迭代，不做HAM调参/公式优化；仅对现有K=4 GMM聚类体系做解析、复盘、可视化、归档
> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/exp4_lookahead_fix/HANDOVER_round4_lookahead_fix.md`
> **Git 提交**：见底部（已提交 main）
> **接收方**：监督 agent / 后续接手者

---

## 0. 给接手者的三句话速览

1. **当前市场状态**：截至 2026-07-13，碳酸锂处于 **State 1（高库存弱价差态）**，该状态在近90个交易日占 71%（64天），是近期主导状态。
2. **4状态画像**：State 0=低持仓中性、State 1=高库存弱价差（当前）、State 2=高价差高波（样本最少仅26天）、State 3=高波动高基差。各状态最显著区分特征已量化（见 exp404a 报告）。
3. **历史规律**：全样本平均每 7.2 个交易日切换一次状态，47次切换。State 1 历史转出 73.3% 去 State 3；State 0/2/3 都高频回 State 1——**State 1 是市场的"枢纽状态"**。

---

## 1. 环境（与前几轮一致）

- **正确解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / sklearn 1.9.0 / matplotlib 3.11.1）
- **⚠️ 坑**：`.venv_ham/` 是废 venv，别用；LSP 报 "pandas could not be resolved" 是误报，忽略
- 自检：`/usr/bin/python3 -c "import pandas,numpy,sklearn,matplotlib; print('OK')"`

---

## 2. 实验编号与产物清单

| 编号 | 脚本 | 内容 | 产物 |
|------|------|------|------|
| **exp404a** | `exp404a_feature_analysis.py` | 滚动窗口状态标签生成 + 聚类特征解析 | `data/exp404a_state_labels_rolling.csv`, `data/exp404a_feature_stats.csv`, `data/exp404a_windows.json`, `reports/exp404a_cluster_feature_analysis.md` |
| **exp404b** | `exp404b_state_review.py` | 时序复盘 + 自动市场分析文本 + 可视化 | `reports/exp404b_state_review.md`, `reports/exp404b_state_review.png`, `data/exp404b_switches.csv` |

### 复现命令
```bash
cd /home/ubuntu/lithium-engine
E=model_ham/exp404_cluster_analysis
/usr/bin/python3 $E/exp404a_feature_analysis.py   # 聚类特征解析+滚动状态标签 (~30s)
/usr/bin/python3 $E/exp404b_state_review.py       # 时序复盘+文本+图 (~10s)
```
`random_state=42` 固定，完全可复现。

---

## 3. 核心结果

### 3.1 滚动窗口状态标签（exp404a）
- 训练窗口180 / 预测20 / 滚动20，共 **17个窗口**，样本外 **340行**
- **严格防泄露**：每窗口仅用窗口内历史 fit scaler + GMM，禁止全量拟合聚类
- 状态分布：State 0=60天(18%) / State 1=125天(37%) / State 2=26天(8%) / State 3=129天(38%)

### 3.2 各状态最显著区分特征（exp404a）
| State | 名称 | 最显著特征 | z均值 | 次显著 |
|-------|------|-----------|-------|--------|
| 0 | 低持仓中性态 | 持仓 | -0.84 | 波动率 |
| 1 | 高库存弱价差态（当前） | 近月1-近月3价差 | -0.47 | 持仓 |
| 2 | 高价差高波态 | 近月1-近月3价差 | +0.67 | 基差 |
| 3 | 高波动高基差态 | 波动率 | +0.53 | 持仓 |

- **近月1-近月3价差** 是 State 1/2 的核心区分特征（一正一负，区分度最强）
- **持仓** 区分 State 0（低）vs State 3（高）
- **波动率** 区分 State 3（高）vs State 0（低）

### 3.3 时序复盘（exp404b）
- 当前状态：State 1（截至 2026-07-13），近90日占 71%
- 近90日演变主线：S3↔S1 反复切换（14段），市场在"高波动高基差"与"高库存弱价差"间震荡
- 全样本平均持续：State 0=6.7天 / State 1=7.8天 / State 2=4.3天 / State 3=7.6天
- 切换频率：每 7.2 个交易日一次，共 47 次
- 转移矩阵：State 1→State 3 占 73.3%；State 0/2/3 都高频回 State 1

---

## 4. 硬性约束验证（本轮全程遵守）

- ✅ **停用HAM预测**：不引入 n_c/n_f/Total_Demand 任何预测逻辑，不调参、不改公式
- ✅ **禁止全量拟合聚类**：滚动窗口内每窗口仅用窗口内历史 fit GMM/scaler
- ✅ **禁止涨跌预测**：自动市场分析文本仅客观陈述状态/指标/历史统计，含明确客观性声明
- ✅ **样本外合法标签**：状态标签全部来自滚动窗口样本外预测，无事后拟合
- ✅ **独立exp编号**：exp404a/exp404b，报告存 `exp404_cluster_analysis/` 目录
- ✅ **提交GitHub**：见底部 git commit

---

## 5. 关键文件路径速查

| 用途 | 路径 |
|------|------|
| 本轮工作目录 | `/home/ubuntu/lithium-engine/model_ham/exp404_cluster_analysis/` |
| 聚类特征解析报告 | `exp404_cluster_analysis/reports/exp404a_cluster_feature_analysis.md` |
| 状态统计表(CSV) | `exp404_cluster_analysis/data/exp404a_feature_stats.csv` |
| 滚动状态标签 | `exp404_cluster_analysis/data/exp404a_state_labels_rolling.csv` |
| 时序复盘报告 | `exp404_cluster_analysis/reports/exp404b_state_review.md` |
| 可视化(行情+雷达+时序) | `exp404_cluster_analysis/reports/exp404b_state_review.png` |
| 状态切换记录 | `exp404_cluster_analysis/data/exp404b_switches.csv` |
| 上一轮交接 | `/home/ubuntu/lithium-engine/model_ham/exp4_lookahead_fix/HANDOVER_round4_lookahead_fix.md` |

---

## 6. 局限性与诚实声明

1. **340行样本外标签 / 17窗口**：统计效力有限，转移矩阵占比有波动
2. **State 2 样本最少（26天）**：该状态统计量置信度低，结论仅供参考
3. **状态命名是人工标注**：`低持仓中性态` 等名称基于z均值最显著特征起的描述性名，非模型内置标签
4. **GMM滚动标签 ≠ 全量标签**：本任务严格按约束用滚动窗口重新打标签，与前几轮的 `market_state_label.csv`（75/25单次切分fit）结果会有差异——这是约束要求，非bug
5. **不含预测**：所有转移频率均为历史统计事实，不作未来判断

---

## 7. 给后续接手者的建议

1. **市场状态聚类定性价值成立**：K=4 能稳定划分4种市场阶段，作人工研判辅助
2. **State 1 是枢纽状态**：3/4 状态高频转回 State 1，可作为"市场常态"基准
3. **HAM定量预测已关闭**：见第四轮交接，本轮仅保留聚类定性价值
4. **若要扩展**：可加长数据序列提高 State 2 等小样本状态的统计效力

---

*生成时间: 2026-09-09 | 实验: exp404a-exp404b | 滚动窗口防泄露已验证 | 无涨跌预测 | 交接对象: 监督agent*
