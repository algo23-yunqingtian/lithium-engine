# 碳酸锂状态过滤器研究 — 阅读索引

> **项目**：碳酸锂状态过滤器研究
> **仓库**：`lithium-engine`
> **分支**：`li-factor-research`
> **最后更新**：2026-09-11

---

## 历史实验清单

### 1）HAM 异质主体模型（exp410 ~ exp437）

- **结论**：HAM 仅可作为分歧状态指标，禁止用于价格预测；存在严重样本外 IC 反转。
- **归档文档**：`model_ham/` 目录下各实验子目录的 `HANDOVER_*.md` + `reports/` 下对应报告
- **最终审计**：`reports/ham_final_defense_audit.md`、`reports/ham_code_audit.md`

### 2）li-factor-research：日频收益因子检验

- **结论**：6 个日频收益因子全部证伪；碳酸锂日度存在结构性样本外反转，不再挖掘日频涨跌预测因子。
- **归档文档**：`factor_research/li_factor_screen_report.md`
- **经验沉淀**：`data-harbor/ham_project_archive/factor_research_summary.md`
- **项目交接**：`project_handover.md`

---

## 当前待执行任务

构建**多指标行情状态过滤器**，仅用于风控择时，不预测涨跌。

### 约束条件

- 禁止全样本寻优
- 尾部 20% 盲测集不参与调参
- 特征上限 ≤ 10 个

---

## 文档导航

| 文档 | 路径 | 说明 |
|------|------|------|
| 因子检验终版报告 | `factor_research/li_factor_screen_report.md` | 6 候选因子单因子检验完整结果 |
| HAM+因子合并经验 | `data-harbor/ham_project_archive/factor_research_summary.md` | 两轮研究合并经验沉淀 |
| 项目交接文档 | `project_handover.md` | HAM + 因子项目双轮验证合并交接 |
| 本阅读索引 | `research_readme.md` | 你正在看的这份 |

---

## 数据说明

原始时序数据库、大型 CSV 数据文件**不上传 GitHub**，由本地 DeepSeek-Harness Agent 读取本地数据库。仓库仅归档文本类研究报告与经验文档。
