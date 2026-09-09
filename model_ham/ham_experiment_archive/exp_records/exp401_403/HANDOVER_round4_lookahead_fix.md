# 第四轮实验交接文档：HAM前视修复 + 收尾验证 + 归档总结（exp401-exp403）

> **交接时间**：2026-09-09
> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/exp4_lookahead_fix/`
> **任务性质**：碳酸锂 GMM/HAM 第四轮——修复第三轮发现的 HAM 因子 1 日前视 bug + 全套重跑 + 收尾归档
> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/exp3_audit_optimize/HANDOVER_round3_audit_optimize.md`
> **接收方**：监督 agent / 后续接手者

---

## 0. 给接手者的三句话速览

1. **最重磅发现**：修复 HAM 前视后，`n_c` 从 `mean=0.40/std=0.47` **塌陷为 `mean=0.997/std=0.037` 近常数**——原版 n_c 的"变化性"几乎完全由那 1 日未来价格泄露撑起，修复后信息含量归零。这比第三轮报告的"前视虚高 0.7pp"严重得多：前视不是偏差，而是旧因子预测力的**唯一来源**。
2. **n_f_minus_n_c 确认为噪声**：修复后该因子塌陷为 `-0.99` 常数（716/720 天全投机主导）。两组对照（三因子 vs 剔除 n_f-n_c）滚动样本外结果**完全相同**（52.1%/77.1%/-24.88%），证明剔除与否毫无影响。
3. **结论收束**：修复前视后 HAM 因子定量预测能力**彻底不成立**（准确率 52.1% 随机区间、RankIC 0 项显著）。市场状态聚类的**定性价值**（K=4 稳健、持续性 85%）可保留作人工研判辅助。

---

## 1. 环境（与第三轮一致）

- **正确解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / scipy 1.18.1 / sklearn 1.9.0 / lightgbm 4.7.0）
- **⚠️ 坑**：`lithium-engine/.venv_ham/` 是废 venv，**别用**；LSP 报 "pandas could not be resolved" 是误报，忽略
- 自检：`/usr/bin/python3 -c "import pandas,numpy,scipy,sklearn,lightgbm,matplotlib; print('OK')"`

---

## 2. 实验编号与产物清单

| 编号 | 脚本 | 内容 | 产物 |
|------|------|------|------|
| **exp401** | `exp401_regen_factors.py` | 修复后重生成纯净 HAM 因子 | `data/exp401_ham_factors_pure.csv`, `logs/exp401_regen_factors.log`, 原版备份 |
| **exp402** | `exp402_rolling_backtest.py` | 滚动回测 + 两组对照 + 固定多头基准 | `reports/exp402_rolling_backtest_pure.md`/`.png`, `data/exp402_g1/g2_predictions.csv`, `data/exp402_windows_g1/g2.json` |
| **exp403** | `exp403_summary_archive.py` | 三版本总览表 + 方法论专章 + DataHub | `reports/exp403_overview_table.md`, `reports/exp403_methodology_lesson.md`, `data/exp403_datahub_metadata.json` |

**核心源码修复**：`model_ham/ham_model.py:48` profit 窗口 `price_change[t-W+1:t+1]` → `price_change[t-W:t]`

### 复现命令
```bash
cd /home/ubuntu/lithium-engine
E=model_ham/exp4_lookahead_fix
/usr/bin/python3 $E/exp401_regen_factors.py    # 重生成纯净因子 (~10s)
/usr/bin/python3 $E/exp402_rolling_backtest.py # 滚动回测+对照 (~60s)
/usr/bin/python3 $E/exp403_summary_archive.py  # 汇总归档 (~1s)
```
`random_state=42` 固定，完全可复现。

---

## 3. 本轮核心结果

### 3.1 因子塌陷（exp401）
| 因子 | 原版(含前视) | 纯净版 | 变化 |
|------|------------|--------|------|
| n_c | mean=0.40 / std=0.47 | mean=0.997 / std=0.037 | ⚠️ 塌陷为常数 |
| n_f-n_c | mean=0.19 / std=0.95 | mean=-0.99 / std=0.07 | ⚠️ 全投机主导 |
| Total_Demand | mean=-102.9 | mean=-28.2 | 相关 0.52 |
| profit_f | — | — | 相关 0.48 |

### 3.2 两组对照 + 基准（exp402，滚动样本外）
| 指标 | 对照1: 三因子 | 对照2: 两因子 | 固定多头基准 |
|------|--------------|--------------|-------------|
| 方向准确率 | 52.1% | 52.1% | — |
| 总收益 | +77.1% | +77.1% | +60.4% |
| 年化收益 | 77.1% | 77.1% | 60.4% |
| 最大回撤 | -24.88% | -24.88% | -34.78% |
| RankIC显著项 | 0 | 0 | — |

- 对照1与对照2**完全一致** → n_f_minus_n_c 为纯噪声
- 多空年化 77.1% > 多头 60.4%，回撤更小（但依赖滚动区间，非稳健超额）

### 3.3 三版本总览（exp403）
| 版本 | 样本外准确率 | HAM因子 | 关键特征 |
|------|------------|---------|---------|
| 旧单次切分(第二轮) | 54.5% | 含前视 | 幸运切点 |
| 旧滚动(exp306) | 50.0% | 含前视 | 54.5%消失 |
| 修复后滚动(exp402) | 52.1% | 纯净 | 因子塌陷,确认无预测力 |

---

## 4. 硬性约束验证（本轮全程遵守）

- ✅ **修复前视**：profit 窗口改为 `[t-W:t]`，最大价格索引 t，无 P(t+1)
- ✅ **禁止全量拟合**：滚动窗口内每窗口仅用窗口历史 fit GMM/scaler/缩尾
- ✅ **禁止全量标准化**：scaler 仅训练窗口 fit
- ✅ **禁止未来信息**：标签 shift(-N) 方向正确，特征基于 T 日
- ✅ **两组对照**：三因子 vs 剔除 n_f-n_c 两组对照
- ✅ **基准对比**：固定多头基准，输出年化/波动/回撤
- ✅ **方法论专章**：记录幸运切点问题
- ✅ **DataHub元数据**：exp403_datahub_metadata.json
- ✅ **无新泄露**：未发现新的时序泄露

---

## 5. 关键文件路径速查

| 用途 | 路径 |
|------|------|
| 本轮工作目录 | `/home/ubuntu/lithium-engine/model_ham/exp4_lookahead_fix/` |
| 核心源码修复 | `model_ham/ham_model.py` (line ~48) |
| 纯净因子 | `exp4_lookahead_fix/data/exp401_ham_factors_pure.csv` |
| 滚动回测报告 | `exp4_lookahead_fix/reports/exp402_rolling_backtest_pure.md` |
| 滚动回测图 | `exp4_lookahead_fix/reports/exp402_rolling_backtest_pure.png` |
| 三版本总览表 | `exp4_lookahead_fix/reports/exp403_overview_table.md` |
| 方法论专章 | `exp4_lookahead_fix/reports/exp403_methodology_lesson.md` |
| DataHub元数据 | `exp4_lookahead_fix/data/exp403_datahub_metadata.json` |
| 上一轮交接 | `/home/ubuntu/lithium-engine/model_ham/exp3_audit_optimize/HANDOVER_round3_audit_optimize.md` |

---

## 6. 局限性与诚实声明

1. **533 行数据 / 约 17 滚动窗口**：统计效力有限，方向可信、数值有波动
2. **多空年化 77.1% 是单边市 + 滚动区间产物**：与第三轮 exp306 的 -2.3pp 不可直接对比，口径/区间不同
3. **纯净 n_c 塌陷为常数**：这本身可能是参数(alpha=0.3,beta=0.5,gamma=2,W=20)在该市场下的固有性质，换参数未必改变结论
4. **Total_Demand 是唯一未塌陷的 HAM 因子**（相关 0.52），但 RankIC 仍不显著，单因子预测力未建立

---

## 7. 给后续接手者的建议

1. **HAM 因子定量预测能力可正式关闭**：三轮验证均无预测力，不建议再投入
2. **市场状态聚类保留定性价值**：K=4 状态标签作为人工研判辅助
3. **若要继续探索 HAM**：需换信息集（当前 Brock-Hommes 框架在碳酸锂上已证伪）
4. **方法论沉淀**：幸运切点 + 注释代码不一致 + 修复后重生成三条铁律，见 `exp403_methodology_lesson.md`

---

*生成时间: 2026-09-09 | 实验: exp401-exp403 | 前视已修复 | 交接对象: 监督agent*
