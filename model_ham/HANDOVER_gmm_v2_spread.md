# 碳酸锂 GMM 市场状态聚类（第二轮：补入 spread_near1_near3）— 交接文档

> **交接时间**：2026-09-09
> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/`
> **任务性质**：GMM 无监督聚类（K=4）+ 条件 RankIC + 消融回测
> **核心变化**：补入滚动近月1-近月3跨期价差 `spread_near1_near3`（动态滚动合约，非固定 LC01/LC03）
> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/HANDOVER_gmm_state_cluster.md`

---

## 1. 一句话总结

用 5 个基本面特征对碳酸锂日线做 GMM 无监督聚类（BIC 选 K=4），划分 4 种市场状态，再做条件 RankIC、状态收益、状态持续性、LightGBM 消融回测四项分析。**GMM 已定型，后续无需重训，只复用 `state_id` 标签**。

---

## 2. 环境与运行方式

### 2.1 关键：用系统 python3，不是 venv
- **正确的解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / scipy 1.18.1 / sklearn 1.9.0 / lightgbm 4.7.0 / matplotlib 3.11.1）
- **⚠️ 坑**：`lithium-engine/.venv_ham/` 是废 venv，**别用它**
- LSP 报 "pandas could not be resolved" 是误报，忽略即可

### 2.2 全部用相对路径，工作目录必须是 `lithium-engine/`
```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 model_ham/step1_build_cluster_input.py
/usr/bin/python3 model_ham/step2_gmm_cluster.py
/usr/bin/python3 model_ham/step3_analyze_plot.py
/usr/bin/python3 model_ham/step4_state_analysis.py
/usr/bin/python3 model_ham/step5_ablation.py
```

### 2.3 中文字体（出图必须）
```python
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
```
step3 已内置。"Failed to find font weight normal, now using 500" 是正常警告。

---

## 3. 数据源映射（5 项特征从哪来）

| 字段 | 来源 | 构造方式 | 覆盖 |
|------|------|----------|------|
| warehouse_stock | `zhiji_external_data.json: zhiji_wh_receipt` | 交易所仓单库存(日线)，669天 | 668/760 |
| basis_spot_main | `raw_data/lithium_future.csv` + `raw_data/spot_price.csv` | `spot_price - close` | 732/760 |
| **spread_near1_near3** | **`lithium.db: contract_prices`** | **每日按合约代码排序，取 volume>0 的第1和第3个活跃合约，close_near1 - close_near3** | **652/760 (85.8%)** |
| hv_20d | `raw_data/lithium_future.csv` | `log(close).rolling(20).std() * sqrt(242)` | 741/760 |
| oi_main | `raw_data/lithium_future.csv` | `position` | 760/760 |

### spread_near1_near3 定义（严格执行）
- **near1** = 当日排序第1的近月合约（距离到期最近的活跃可交割合约，volume>0）
- **near3** = 当日排序第3的近月合约（距离到期第三近的活跃可交割合约）
- spread = near1收盘价 − near3收盘价
- **动态滚动**：合约随时间滚动切换，非固定 LC01/LC03
- 缺失日 107 天（活跃合约不足3个），标记 NaN，**未插值**

### 原始数据位置（都在 model_ham/ 或上级目录）
- `model_ham/raw_data/lithium_future.csv` — 760 行，LC0 主力日线
- `model_ham/raw_data/spot_price.csv` — 732 行，现货价+基差
- `model_ham/zhiji_external_data.json` — zhiji_wh_receipt(仓单669天)
- `/home/ubuntu/lithium_calendar/lithium.db` — contract_prices 表(8687行, 44个合约)
- `model_ham/ham_factors_full_800d.csv` — 740 行，HAM 因子(n_c/n_f_minus_n_c/Total_Demand)

---

## 4. GMM 聚类结果（K=4）

### BIC 对比
| K | BIC |
|---|-----|
| 2 | 4787.12 |
| 3 | 4406.69 |
| 4 | 4167.70 |

BIC 在 [2,3,4] 单调下降，选候选内最优 K=4。

### 状态分布
| State | 命名 | 占比 | 天数 |
|-------|------|------|------|
| 0 | 偏紧/去库(清淡) | 26.3% | 140 |
| 1 | 宽松/累库 | 13.3% | 71 |
| 2 | 过渡/中性 | 40.0% | 213 |
| 3 | 资金博弈/深贴水 | 20.5% | 109 |

### 各状态特征均值
| State | warehouse_stock | basis_spot_main | spread_near1_near3 | hv_20d | oi_main |
|-------|----------------|-----------------|-------------------|--------|---------|
| 0 | 29,532 | -1,981 | -261 | 1.20 | 447,354 |
| 1 | 19,447 | -1,468 | -1,311 | 1.03 | 236,418 |
| 2 | 31,624 | -390 | -675 | 0.58 | 268,924 |
| 3 | 32,244 | -1,581 | **-2,971** | 0.49 | 194,043 |

### 训练/测试切分
- 训练集 399 行 (2023-12-06 ~ 2026-01-08)
- 测试集 134 行 (2026-01-09 ~ 2026-07-30)
- Z-score 标准化仅用训练集统计量
- GMM 参数：covariance_type="full", n_init=10, max_iter=1000, random_state=42

### 测试集状态分布
| State | 训练集 | 测试集 |
|-------|--------|--------|
| 0 | 75 (18.8%) | 65 (48.5%) |
| 1 | 51 (12.8%) | 20 (14.9%) |
| 2 | 170 (42.6%) | 43 (32.1%) |
| 3 | 103 (25.8%) | **6 (4.5%)** |

⚠️ **测试集 State 3 仅 6 样本**，跨状态样本外验证严重不足。

---

## 5. 四项分析结论

### 5.1 条件 RankIC（state_ic_result.md）
- **训练集内 2 项显著**：
  - State 0: n_c RankIC=-0.283, p=0.014 ✅
  - State 3: Total_Demand RankIC=-0.209, p=0.034 ✅
- 全样本唯一接近显著：State 3 的 Total_Demand (p=0.075)，但 Bonferroni 校正后不显著
- **离散因子问题**：n_c 全局仅 4 个唯一值，n_f_minus_n_c 仅 3 个 → RankIC 功效极低
- **核心提示**：全样本结果=样本内分析，不能作为外推有效性证据

### 5.2 状态收益（state_return_stats.md）
- State 0（偏紧）：5d 均值 +1.87% 最高，胜率 50.7%
- State 3（资金博弈）：5d 均值 -0.56%，胜率 33.9% 最低
- State 2（中性）：5d 胜率 58.6% 最高
- 波动自洽验证：收益波动排序(0>1>2>3)与聚类 hv_20d 排序一致

### 5.3 状态持续性（state_persistence.md）
- **整体延续概率 85.0%**（532 对，452 对保持）
- State 0 最稳(91.4%)，State 2 最易离开(84.0%)
- 主要切换通道 State 2↔3（15+16 次）
- 转移矩阵近似对角阵，无高频震荡伪状态

### 5.4 消融回测（ablation_result.md）
| 组别 | 特征 | RMSE | 方向准确率 |
|------|------|------|-----------|
| A | 仅基本面(5项) | 0.5419 | 45.5% |
| B | 仅 HAM 三因子 | 0.5053 | **54.5%** |
| C | 基本面+HAM | 0.5380 | 47.8% |
| D | 全特征+state+交互 | 0.5374 | 48.5% |

- **B 组（仅 HAM）方向准确率最高 54.5%**
- 加入基本面反而降低准确率（45.5% → 47.8%），说明基本面特征噪声大
- State 交互项未提升性能（D 组仅略高于 C 组）
- 上一轮未做消融实验，无对比基准

---

## 6. 与上一轮的关键差异

| 维度 | 上一轮 | 本轮 |
|------|--------|------|
| 特征数 | 4（缺 spread） | **5（补入 spread_near1_near3）** |
| 状态分布 | 91% 挤在 State 3 | **更均衡（40/26/21/13%）** |
| State 3 跨期画像 | 无法刻画 | **均值 -2971，深贴水清晰** |
| 条件 RankIC 显著项 | 0 项 | **2 项（State 0 n_c, State 3 Total_Demand）** |
| 状态持续性 | 93.3% | **85.0%**（略有下降但仍优秀） |
| 消融实验 | 未做 | **4 组完整对照** |
| 数据损失 | 无 spread 列 | 剔除 NaN 后 760→533 行（损失 30%） |

---

## 7. 交付物清单（全部在 model_ham/）

### 数据文件
| 文件 | 说明 |
|------|------|
| `cluster_input.csv` | 5 项特征输入数据 760 行 |
| `market_state_label.csv` | 533 交易日 + state_id + 5 原始特征 + proba_max |
| `ham_state_combined.csv` | state 标签左连 HAM 因子（533 行） |
| `gmm_params.json` | GMM 训练参数 + BIC 表 + scaler 统计量 |

### 报告文档
| 文件 | 说明 |
|------|------|
| `state_cluster_report.md` | 聚类分析报告（BIC理由+特征画像+局限性） |
| `state_distribution_plot.png` | 4 状态×5 特征分布对比图 |
| `state_ic_result.md` | 分状态 RankIC + t/p（训练集 vs 全样本）+ Bonferroni |
| `state_return_stats.md` | 各状态未来 1d/3d/5d 收益统计 |
| `state_persistence.md` | 状态持续性 + 转移矩阵 |
| `ablation_result.md` | 4 组消融对照实验报告 |

### 脚本（从 lithium-engine/ 跑）
- `step1_build_cluster_input.py` — 构造 5 项输入特征
- `step2_gmm_cluster.py` — GMM 训练 + BIC 选择 + 打标签
- `step3_analyze_plot.py` — 聚类报告 + 出图 + HAM 拼接
- `step4_state_analysis.py` — 条件 RankIC + 收益 + 持续性
- `step5_ablation.py` — LightGBM 消融回测

---

## 8. 全部必守的局限性（已写入每个文档）

1. **spread_near1_near3 缺失 107 天（14.1%）**：活跃合约不足 3 个时标记 NaN，未插值
2. **剔除 NaN 后损失 30% 数据**（760→533 行），时间范围缩短至 2023-12~2026-07
3. **测试集状态失衡**：State 3 测试集仅 6 样本，跨状态样本外验证严重不足
4. **全样本 RankIC = 样本内分析**，非样本外预测检验
5. **离散因子唯一值过少**：n_c(4个) / n_f_minus_n_c(3个)，RankIC 功效极低
6. **多重比较**：部分 p<0.05 经 Bonferroni 校正后不显著
7. **GMM 高斯假设**：金融序列厚尾非平稳，状态边界为概率软划分
8. **K 仅在 [2,3,4] 内选**：BIC 单调下降，非全局最优断言
9. **仓单库存≠社会库存**：用交易所显性仓单代理，系统性低估真实库存
10. **消融实验测试集仅 134 行**，统计波动大，结论不可过度解读

---

## 9. 下一步建议

1. **补数据损失**：当前剔除 227 行（30%），考虑对 spread_near1_near3 缺失日做"活跃合约≥2 时算 1-2 月差"的降级方案，或补充合约月数据
2. **样本外真检验**：当前无法做（测试集 State 3 仅 6 样本），需要更长时间序列或降低 K
3. **多期 RankIC**：当前仅 fwd_1d，可加 3d/5d 累计收益因子检验
4. **离散因子换检验法**：n_c/n_f_minus_n_c 改用分组均值 t 检验而非 RankIC
5. **消融实验扩展**：加入更多超参数搜索、交叉验证、或换用其他模型（XGBoost/RF）
6. **前端可视化**：将状态聚类结果集成到 lithium-dashboard 看板

---

## 10. 复现验证（接手后第一件事）

```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 -c "import pandas,numpy,scipy,sklearn,lightgbm,matplotlib; print('环境OK')"
# 按顺序跑 step1->step5，检查：
# - step1: 5项特征覆盖度（spread_near1_near3 85.8%）
# - step2: BIC 表 + K=4 + 状态分布 140/71/213/109
# - step3: state_distribution_plot.png 中文正常显示
# - step4: 整体延续概率 85.0%
# - step5: B组 HAM 方向准确率最高 54.5%
```
random_state=42 固定，结果应完全可复现。
