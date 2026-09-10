# 碳酸锂日线 GMM 市场状态聚类 — 交接文档

> **交接时间**：2026-09-09
> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/`
> **任务性质**：无监督聚类（GMM）划分市场状态 + 条件 RankIC/收益/持续性分析
> **核心铁律**：聚类禁止使用 HAM 衍生变量；GMM 仅训练集拟合；不编造缺失数据；不重训 GMM（补充分析仅复用标签）

---

## 1. 一句话总结

用 4 个客观基本面特征对碳酸锂日线做 GMM 无监督聚类（BIC 选 K=4），划分出 4 种市场状态，再做条件 RankIC、状态收益、状态持续性三项后续分析。**GMM 模型已定型，后续无需重训，只复用 `state_id` 标签**。

---

## 2. 环境与运行方式

### 2.1 关键：用系统 python3，不是 venv
- **正确的解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / scipy 1.18.1 / sklearn 1.9.0 / matplotlib 3.11.1 全套齐备）
- **⚠️ 坑**：`lithium-engine/.venv_ham/` 是中途尝试建但没跑完的 venv，**别用它**（且它没派上用场，可删）
- LSP/Pyright 会报 "pandas could not be resolved" —— 那是它指错了 venv，**忽略即可**，实际运行无碍

### 2.2 全部用相对路径，工作目录必须是 `lithium-engine/`
```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 model_ham/step1_build_cluster_input.py
/usr/bin/python3 model_ham/step2_gmm_cluster.py
/usr/bin/python3 model_ham/step3_analyze_plot.py
/usr/bin/python3 model_ham/step4_state_analysis.py
```
脚本内 BASE="model_ham"，所以从 `lithium-engine/` 跑，不要从 model_ham/ 内跑。

### 2.3 中文字体（出图必须）
系统只有文泉驿正黑，**必须**在出图脚本里设置，否则中文显示为方块：
```python
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
```
step3 已内置。"Failed to find font weight normal, now using 500" 是正常警告，中文能正常显示，不是 bug。

---

## 3. 数据源映射（特征从哪来）

`cluster_input.csv` 的 6 个字段构造来源：

| 字段 | 来源 | 构造方式 | 覆盖 |
|------|------|----------|------|
| warehouse_stock | `zhiji_external_data.json: zhiji_wh_receipt` | 交易所仓单库存(日线)，缺失日 ffill | 668/740 |
| basis_spot_main | `ham_factors_full_800d.csv` | `spot_avg - close` | 740/740 |
| **spread_1_3** | **无数据** | **整列 NaN 剔除** | 0/740 |
| hv_20d | `ham_factors_full_800d.csv` | `log(close).rolling(20).std() * sqrt(242)` | 720/740 |
| oi_main | `ham_factors_full_800d.csv` | `position` | 740/740 |

### ⚠️ 最大数据缺口：spread_1_3
- 现有所有数据**只有主力连续合约价格**，无任何具体合约月（01/03月）序列
- 按"不编造"铁律整列 NaN 剔除，**最终只用 4 个特征聚类**
- 这是整个分析的首要局限，已写入全部文档
- **若要补全**：需补充不同合约月价格序列（知几/交易所合约月数据），重跑 step1 起全套

### 原始数据位置（都在 model_ham/）
- `ham_factors_full_800d.csv` — 740 行，含 close/position/spot_avg/basis + HAM因子(n_c/Total_Demand等)，**这就是任务说的 ham_factors_full.csv**
- `raw_data/fundamental_balance.csv` — 库存(稀疏，inventory_sample仅160行)
- `raw_data/lithium_future.csv` — 主力日线
- `zhiji_external_data.json` — 含 zhiji_wh_receipt(仓单669天，最佳库存源)

---

## 4. 聚类结果（GMM 已定型，K=4）

| State | 命名 | 占比 | 特征画像 |
|-------|------|------|----------|
| 0 | 偏紧/去库(清淡) | 17.8% (119天) | 低库存+低持仓+高波动 |
| 1 | 宽松/累库 | 30.2% (202天) | 高库存+低波动 |
| 2 | 过渡/中性 | 18.1% (121天) | 低波动平台期 |
| 3 | 资金博弈/高波动 | 33.8% (226天) | 高持仓+高波动+深贴水 |

- **BIC 选 K=4**：BIC 在 [2,3,4] 单调下降(5158→4853→4711)，选候选内最优
- **训练/测试切分**（防数据窥探，严格按日期，不随机）：
  - 训练集 534 行 (2023-12-06 ~ 2026-02-24)
  - 测试集 134 行 (2026-02-25 ~ 2026-09-07)
- **Z-score 标准化**：仅用训练集统计量拟合 scaler，测试集复用
- GMM 参数：covariance_type="full", n_init=10, max_iter=1000, random_state=42

---

## 5. 三项补充分析结论

### 5.1 条件 RankIC（state_ic_result.md）
- 训练集内：全部 p>0.11，**无一显著**
- 全样本唯一显著：State 1 的 Total_Demand (RankIC=-0.147, p=0.037)，但 Bonferroni 校正后不显著 + 训练集内未复现 → **稳健性不足**
- `n_c`/`n_f_minus_n_c` 子样本内仅 2-4 个唯一值(近类别变量)，RankIC 功效极低
- **核心提示**：全样本结果=样本内分析，**不能作为外推有效性证据**（文档已加粗）

### 5.2 状态收益（state_return_stats.md）
- State 3(资金博弈)：5d 均值 +1.23%、胜率 55.6% 最高，但波动最大(8.59%)
- State 1(宽松)：唯一负收益、波动最低
- **波动自洽验证**：收益波动排序(3>0>2>1) 与聚类 hv_20d 排序一致，证明聚类波动维度刻画有效

### 5.3 状态持续性（state_persistence.md）
- **整体延续概率 93.3%**（667 对，622 对保持）— 远超随机 25%，**聚类稳定性优秀，不每日乱跳**
- State 3 最稳(96.4%)，State 2 最易离开(88.4%)
- 主要切换通道 State 0↔2；State 3 近乎"孤岛"
- 转移矩阵近似单位阵，无高频震荡伪状态

---

## 6. 交付物清单（全部在 model_ham/）

### 主任务
| 文件 | 说明 |
|------|------|
| `cluster_input.csv` | 输入数据 740 行（spread_1_3 标记缺失） |
| `market_state_label.csv` | 668 交易日 + state_id + 4 原始特征 |
| `state_cluster_report.md` | 聚类分析报告（BIC理由+局限性） |
| `state_distribution_plot.png` | 4 状态×4 特征分布对比图 |
| `ham_state_combined.csv` | state 标签左连 HAM 因子（740 行） |

### 补充分析
| 文件 | 说明 |
|------|------|
| `state_ic_result.md` | 分状态 RankIC + t/p（训练vs全样本两套） |
| `state_return_stats.md` | 各状态未来1/3/5日收益统计 |
| `state_persistence.md` | 状态持续性 + 转移矩阵 |

### 脚本（可复现，从 lithium-engine/ 跑）
- `step1_build_cluster_input.py` — 构造输入特征
- `step2_gmm_cluster.py` — 预处理+GMM训练+BIC选择+打标签
- `step3_analyze_plot.py` — 统计解读+出图+拼接
- `step4_state_analysis.py` — 条件RankIC+收益+持续性（不重训GMM）

---

## 7. 全部必守的局限性（已写入每个文档）

1. **缺 spread_1_3 期限结构指标**：聚类与因子检验均不完整（首要局限）
2. **测试集样本严重失衡**：测试期 91% 样本落在 State 3，State 0/1/2 测试集仅 6/10/0 样本 → **跨状态样本外验证根本不足**
3. **全样本 RankIC = 样本内分析**，非样本外预测检验
4. **因子离散度问题**：n_c/n_f_minus_n_c 唯一值过少，RankIC 功效不足
5. **多重比较未校正**：p=0.037 经 Bonferroni 后不显著
6. **GMM 高斯假设**：金融序列厚尾非平稳，BIC 绝对值偏大，状态边界为概率软划分
7. **K 仅在 [2,3,4] 内选**：BIC 单调下降，非全局最优断言
8. **仓单库存≠社会库存**：用交易所显性仓单代理，系统性低估真实库存

---

## 8. 下一步建议（如果继续推进）

1. **补期限结构数据**：找到 LC01/LC03 合约月价格序列 → 重跑全套，这是最大缺口
2. **样本外真检验**：需要"训练期定状态 + 完全未见未来期算 IC"，当前测试期太短且状态单一，无法做
3. **多期 RankIC**：当前仅 fwd 1d，可加 3d/5d 累计收益因子检验（可能低估低频因子）
4. **离散因子换检验法**：n_c/n_f_minus_n_c 改用分组均值 t 检验而非 RankIC
5. **状态边界敏感性**：用 proba_max 筛高置信度样本做稳健性分析
6. **清理**：可删 `lithium-engine/.venv_ham/`（无用 venv）

---

## 9. 复现验证（接手后第一件事）

```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 -c "import pandas,numpy,scipy,sklearn,matplotlib; print('环境OK')"
# 按顺序跑 step1->step4，检查：
# - step2 输出 BIC 表 + K=4
# - market_state_label.csv 668 行，state 分布 119/202/121/226
# - step4 输出整体延续概率 0.933
```
random_state=42 固定，结果应完全可复现。
