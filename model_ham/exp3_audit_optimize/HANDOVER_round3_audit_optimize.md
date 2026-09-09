# 第三轮实验交接文档：反向校验+稳健性增强（exp301-exp309）

> **交接时间**：2026-09-09
> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/exp3_audit_optimize/`
> **任务性质**：碳酸锂 GMM 市场状态聚类第三轮——反向校验上一轮缺陷 + 稳健性增强实验
> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/HANDOVER_gmm_v2_spread.md`
> **Git 提交**：`729b280`（已推送 origin/main）
> **接收方**：监督 agent（接手后可复核结论、跑复现验证、决定下一步）

---

## 0. 给监督 agent 的三句话速览

1. **核心发现**：上一轮（第二轮）报告的 B组 HAM 因子样本外准确率 **54.5% 是"幸运切点"**，在严格滚动窗口回测下**完全消失至 50.0%**——上一轮最得意的结论经不起严格样本外检验。
2. **稳定结论只有 4 条**：GMM 聚类 K=4 稳健、状态持续性 85%、条件 RankIC 普遍不显著、聚类对 K 不敏感。其余（B组54.5%、RankIC显著项、HAM预测力、n_f-n_c方向、多空优于多头）**都是偶然性**。
3. **最大方法论教训**：单次时间切分（75/25）极不可靠，必须用滚动窗口。这一轮用滚动窗口把上一轮的亮点基本"打掉"了。

---

## 1. 环境（关键，照抄上一轮）

### 1.1 用系统 python3，不是 venv
- **正确解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / scipy 1.18.1 / sklearn 1.9.0 / lightgbm 4.7.0 / matplotlib 3.11.1）
- **⚠️ 坑**：`lithium-engine/.venv_ham/` 是废 venv，**别用**
- LSP 报 "pandas could not be resolved" 是误报，忽略

### 1.2 环境自检
```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 -c "import pandas,numpy,scipy,sklearn,lightgbm,matplotlib; print('环境OK')"
```

### 1.3 pandas 3.0 的两个新坑（本轮踩到）
- **LossySetitemError**：缩尾裁剪后 float 写不进 int64 列。修法：裁剪前 `df[col]=df[col].astype(float)`
- **silhouette_score 单标签报错**：滚动窗口小测试期可能只预测出1个状态，`ValueError: Number of labels is 1`。修法：先 `if len(np.unique(labels))>=2` 再算

---

## 2. 数据源（与上一轮一致，533行有效子集）

| 字段 | 来源 | 覆盖 |
|------|------|------|
| warehouse_stock | `zhiji_external_data.json:zhiji_wh_receipt` | 668/760 |
| basis_spot_main | `spot_price.csv` + `lithium_future.csv` | 732/760 |
| **spread_near1_near3** | `lithium.db:contract_prices`（滚动近月1-近月3价差） | 652/760 |
| hv_20d | `log(close).rolling(20).std()*sqrt(242)` | 741/760 |
| oi_main | `position` | 760/760 |
| n_c / n_f_minus_n_c / Total_Demand | `ham_factors_full_800d.csv` | — |

**有效子集**：剔除 NaN 后 **533 行**（2023-12-06 ~ 2026-07-30），训练 399 / 测试 134（前75%/后25%）。

**关键文件**（都在 `model_ham/`，本轮复用不改）：
- `ham_state_combined.csv` — state标签左连HAM因子（533行）
- `market_state_label.csv` — GMM打的状态标签
- `gmm_params.json` — 训练参数+BIC+scaler统计量
- `cluster_input.csv` — 5项特征输入

---

## 3. 实验编号与产物清单（exp301-exp309）

### 3.1 脚本（从 `lithium-engine/` 跑，相对路径）

| 编号 | 脚本 | 内容 | 产物 |
|------|------|------|------|
| **前置核查1+2** | `check_1_2_dataset_timeseries.py` | 确认54.5%数据集聚类 + 时序对齐校验 | `reports/exp301_audit1_dataset.md`, `logs/exp301_timeseries_align.log` |
| **前置核查2补充** | `check_ham_lookhead.py` | HAM因子前视量化 | `logs/exp302_ham_lookhead.log` |
| **前置核查3** | `check_3_subset_control.py` | 同子集对照（关闭spread复现旧版） | `reports/exp303_audit3_subset_control.md` |
| **实验1** | `exp1_winsorize.py` | 1%-99%缩尾增强 | `reports/exp304_exp1_winsorize.md`, `data/exp304_winsor_limits.json` |
| **实验2** | `exp2_robustness_K.py` | K=2/3/4稳健性 | `reports/exp305_exp2_robustness_K.md` |
| **实验3(核心)** | `exp3_rolling_backtest.py` | 滚动窗口时序回测 | `reports/exp306_exp3_rolling_backtest.md`, `reports/exp306_rolling_backtest.png`, `data/exp306_rolling_predictions.csv`, `data/exp306_rolling_windows.json` |
| **实验4** | `exp4_quantile_baseline.py` | 五分位分层+固定多头基准 | `reports/exp307_exp4_quantile_baseline.md` |
| **专章** | `exp5_ham_discrete.py` | HAM离散因子统计效力 | `reports/exp308_ham_discrete_factors.md` |
| **汇总** | `exp6_summary_archive.py` | 对比总览表+DataHub元数据+摘要 | `reports/exp309_overview_table.md`, `data/exp309_datahub_metadata.json`, `EXECUTION_SUMMARY.md` |

### 3.2 复现命令
```bash
cd /home/ubuntu/lithium-engine
E=model_ham/exp3_audit_optimize
/usr/bin/python3 $E/check_1_2_dataset_timeseries.py   # 前置核查1+2
/usr/bin/python3 $E/check_ham_lookhead.py              # HAM前视
/usr/bin/python3 $E/check_3_subset_control.py          # 前置核查3
/usr/bin/python3 $E/exp1_winsorize.py                  # 实验1
/usr/bin/python3 $E/exp2_robustness_K.py               # 实验2
/usr/bin/python3 $E/exp3_rolling_backtest.py           # 实验3(核心,~1-2min)
/usr/bin/python3 $E/exp4_quantile_baseline.py          # 实验4
/usr/bin/python3 $E/exp5_ham_discrete.py               # 专章
/usr/bin/python3 $E/exp6_summary_archive.py            # 汇总归档
```
random_state=42 固定，结果完全可复现。

---

## 4. 五个已发现的漏洞（监督 agent 重点核查）

### 漏洞1：B组54.5%是幸运切点（最重磅）
- 上一轮报告 B组(仅HAM)样本外准确率 54.5%（n=134）
- **滚动窗口回测（17窗口/340行）整体仅 50.0%**，54.5% 完全消失
- 逐期准确率 35%-70% 剧烈波动，仅 7/17 窗口 >50%
- 结论：上一轮单次75/25切分碰巧选中有利切点

### 漏洞2：上一轮"2项RankIC显著"是样本内假象
- 同子集重跑，Bonferroni校正(p<0.0014)后**新旧均0项显著**
- 上一轮"训练集内2项显著"是过拟合产物

### 漏洞3：训练集严重过拟合被掩盖
- A组训练集准确率83%，样本外仅45.5%
- 上一轮只报测试集54.5%，未披露训练集过拟合程度

### 漏洞4：HAM因子含1日前视（ham_model.py:48）
- `window_price_change = price_change[t-W+1:t+1]` 含 `price_change[t]=P(t+1)-P(t)`
- 量化：含前视版 vs 纯净版 n_c 相关仅 **0.015**（几乎不相关）
- 对准确率影响 **0.7pp**（非致命，但需透明标注）
- 修复方案：profit窗口改为 `price_change[t-W:t]`

### 漏洞5：n_f-n_c样本内外方向翻转
- 训练集与测试集分层收益相关系数 **-1.000**（完全相反）
- 该因子过拟合训练集

### 已确认无泄露
- shift(-N) 方向正确，全量一致性偏差 0.0
- GMM 仅训练集 fit，无跨集拟合泄露
- 特征侧8项均基于T日及之前数据

---

## 5. 硬性约束验证（本轮全程遵守）

- ✅ **禁止全量数据拟合GMM**：滚动窗口内每窗口仅用窗口历史fit
- ✅ **禁止全量数据标准化**：scaler仅训练窗口fit
- ✅ **禁止未来信息泄露**：标签shift(-N)方向正确，特征基于T日
- ✅ **样本内/样本外严格区分**：所有报告分开列出
- ✅ **多重检验校正**：Bonferroni应用于RankIC
- ✅ **离散因子专章**：n_c/n_f用分组均值t检验替代RankIC

---

## 6. 交接给监督 agent 的下一步建议

1. **重新评估HAM因子价值**：严格滚动下无预测力，考虑弃用或重构（这是本轮最值钱的结论）
2. **修复HAM前视**：`ham_model.py:48` profit窗口改为 `price_change[t-W:t]`，然后重跑全套
3. **扩充数据**：533行支持滚动窗口有限（仅17窗口），需更长序列提高统计效力
4. **离散因子重构**：n_c/n_f离散度太低（3-4档），考虑连续化或换检验法
5. **多基准对照**：加入动量/反转等基准对比模型超额（本轮只加了固定多头）

---

## 7. 局限性与诚实声明

1. **B组54.5%虽被打掉，但单次切分确实能复现**：54.5%在75/25切分下是真实的，只是不稳健——监督 agent 复核时两个口径都要看
2. **滚动窗口17个，统计效力有限**：结论方向可信，但具体数值（50.0%）有波动
3. **HAM前视影响量化用了gamma=2**：与原模型一致，但纯净版n_c重构后与原版几乎不相关，这个现象本身值得深挖
4. **固定多头基准+9.90%是单边市产物**：测试期恰好上涨，多头基准天然占优，不能简单对比多空策略

---

## 8. 关键文件路径速查

| 用途 | 路径 |
|------|------|
| 本轮工作目录 | `/home/ubuntu/lithium-engine/model_ham/exp3_audit_optimize/` |
| 执行摘要（结论优先） | `exp3_audit_optimize/EXECUTION_SUMMARY.md` |
| 对比总览表（新旧对比） | `exp3_audit_optimize/reports/exp309_overview_table.md` |
| 核心实验3报告 | `exp3_audit_optimize/reports/exp306_exp3_rolling_backtest.md` |
| 核心实验3图 | `exp3_audit_optimize/reports/exp306_rolling_backtest.png` |
| DataHub元数据 | `exp3_audit_optimize/data/exp309_datahub_metadata.json` |
| 滚动预测明细 | `exp3_audit_optimize/data/exp306_rolling_predictions.csv` |
| 上一轮交接 | `/home/ubuntu/lithium-engine/model_ham/HANDOVER_gmm_v2_spread.md` |
| 因子回测skill | `~/.hermes/skills/commodity-research/factor-model-backtest/` |

---

## 9. 监督 agent 快速验收清单

- [ ] 环境自检：`/usr/bin/python3 -c "import pandas,sklearn,lightgbm"` 返回OK
- [ ] 复现实验3：跑 `exp3_rolling_backtest.py`，确认整体准确率≈50.0%
- [ ] 核对漏洞1：上一轮54.5% vs 滚动50.0%（核心打脸）
- [ ] 核对漏洞4：`ham_model.py:48` profit窗口前视
- [ ] 确认硬性约束：滚动窗口内无全量fit
- [ ] Git确认：`git log --oneline -1` 应为 `729b280`

---

*生成时间: 2026-09-09 | 实验: exp301-exp309 | 严格防泄露已验证 | 交接对象: 监督agent*
