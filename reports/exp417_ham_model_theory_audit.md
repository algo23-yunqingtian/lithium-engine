# exp417 HAM 模型底层建模原理、数学形式与时序逻辑审计 — 主报告

> 生成时间：2026-09-10 | 基准：当前 HAM 模型，不改动任何代码与回测逻辑。
> 目标：剥离底层代码，用自然语言 + 数学公式解释 HAM；再次审计时序逻辑，确认不存在未来函数。
> 审计结论：① 代码层面**无未来函数**（shift-equivariance 8 日校验全部逐位一致）；② 模型层面存在**参数历史拟合与因子漂移风险**（exp416 跨品种已印证）。

---

## 模块1：HAM 主体建模原理说明

### 1.1 HAM 模拟的两类市场主体

HAM（Heterogeneous-Agent Model，异质主体模型）基于 Brock-Hommes 框架，抽象模拟市场里两类交易者：

| 主体 | 代号 | 代表现实中的交易者 | 定价锚 | 行为逻辑 |
|------|------|-------------------|--------|----------|
| **产业基本面 Agent** | Agent F | 产业链产业资本、产业买家、长线配置盘 | 供需公允价值 `P_fund` | 向公允价值回归，价格低于公允价值时买入（做多），高于时卖出（做空） |
| **投机趋势 Agent** | Agent C | 趋势投机者、CTA、动量交易盘 | 价格动量 `P(t)-P(t-1)` | 追涨杀跌，价格上涨时做多，下跌时做空 |

**重要声明：这是理论抽象，不是真实交易者的 1:1 复刻。**
- HAM 的两类主体是**建模者为了刻画"基本面定价"与"动量趋势"两种力量的对抗**而设计的理论抽象。
- 现实中并没有一群交易者严格遵循 `D_f = alpha×(P_fund - P)`，也没有一群严格遵循 `D_c = beta×(P - P_{t-1})`。
- 现实市场中，同一机构可能同时具备基本面与趋势特征；产业资本也可能投机，趋势交易者也看基本面。
- HAM 的**价值不在精确复刻交易者**，而在用两个对抗力量刻画市场"产业定价压力"与"投机追涨压力"的背离——这个背离（disagreement）才是策略信号的核心。

### 1.2 模型内部参数经济含义

| 参数 | 数值 | 经济含义 |
|------|:---:|----------|
| **alpha** | 0.3 | 产业基本面回归强度。控制 Agent F 对"价格偏离公允价值"的反应灵敏度。alpha 越大，产业资本越积极地向公允价值回归。0.3 表示温和的产业回归压力（碳酸锂价格波动大，alpha 不宜过大以免信号过强）。 |
| **beta** | 0.5 | 投机趋势外推强度。控制 Agent C 对"价格动量"的追涨杀跌力度。beta 越大，趋势投机越激进。0.5 大于 alpha，反映碳酸锂市场动量交易活跃。 |
| **gamma** | 2 | 策略切换灵敏度（Logit 温度参数）。控制两类 Agent 根据历史滚动收益调整市场占比的剧烈程度。gamma 越大，赢家策略越迅速垄断市场（接近硬切换）；gamma 越小，市场占比越稳定。gamma=2 为中等灵敏度。 |
| **W** | 20 | 滚动收益窗口（交易日）。Agent 评估自身历史表现的时窗长度。W=20 约一个交易月，反映"近一个月策略表现"决定未来权重。 |

**派生参数（代码层）**：`ROLLING_TRAIN=180`（滚动训练窗口，约9个月，用于分位标准化）；`POS_HIGH_Q=0.95`/`POS_MED_Q=0.90`/`NEUTRAL_Q=0.40`（仓位分级与平仓的分位阈值）。

### 1.3 完整迭代数学方程组（LaTeX）

HAM 每日迭代流程：**输入数据 → 主体 A/F 预期更新 → 主体 B/C 预期更新 → 计算分歧 D(T) → 生成交易信号**。

#### 步骤0：输入数据（T 时刻可观测历史）

设 $P(t)$ 为 T 日收盘价，$P_{fund}(t)$ 为 T 日供需公允价值，$P(t-1)$ 为前一日收盘价。三者均为 T 日收盘后可观测的历史数据。

#### 步骤1：需求函数（主体预期更新）

$$D_f(t) = \alpha \cdot \bigl(P_{fund}(t) - P(t)\bigr)$$

$$D_c(t) = \beta \cdot \bigl(P(t) - P(t-1)\bigr)$$

- $D_f(t)$：产业基本面回归需求。价格低于公允价值（$P_{fund} > P$）时为正（产业资本买入压力）。
- $D_c(t)$：投机趋势动量需求。价格上涨（$P > P_{t-1}$）时为正（投机者追涨）。

#### 步骤2：滚动窗口收益（主体历史表现评估）

$$\text{profit}_f(t) = \sum_{i=t-W}^{t-1} D_f(i) \cdot \bigl(P(i) - P(i-1)\bigr)$$

$$\text{profit}_c(t) = \sum_{i=t-W}^{t-1} D_c(i) \cdot \bigl(P(i) - P(i-1)\bigr)$$

- 窗口 $[t-W, t)$ 仅含 T 及更早数据，$\max$ 价格索引为 $t$，**不触及 $P(t+1)$**（无前视关键）。
- $\text{profit}$ 度量"过去 W 日内该主体策略的累计损益"——表现好的主体获得更多市场占比。

#### 步骤3：Logit 策略切换权重（市场占比动态调整）

$$n_f(t) = \frac{\exp(\gamma \cdot \text{profit}_f(t))}{\exp(\gamma \cdot \text{profit}_f(t)) + \exp(\gamma \cdot \text{profit}_c(t))}$$

$$n_c(t) = \frac{\exp(\gamma \cdot \text{profit}_c(t))}{\exp(\gamma \cdot \text{profit}_f(t)) + \exp(\gamma \cdot \text{profit}_c(t))}$$

- $n_f + n_c = 1$，表示两类 Agent 的市场占比。
- $\text{profit}$ 高的主体 $n$ 趋近 1（垄断），低的趋近 0。
- 代码用 log-sum-exp trick 防数值溢出，$\gamma \cdot \text{profit}$ 截断至 $\pm 500$。

#### 步骤4：系统分歧度 D(T)（核心信号）

对 $D_c$ 与 $D_f$ 做滚动标准化（180 日 rank，映射至 $[-1, 1]$）：

$$D_c^{norm}(t) = 2 \cdot \text{rank}_{pct}\bigl(D_c(t); \text{window } [t-180, t]\bigr) - 1$$

$$D_f^{norm}(t) = 2 \cdot \text{rank}_{pct}\bigl(D_f(t); \text{window } [t-180, t]\bigr) - 1$$

**系统分歧度**：

$$\text{disagreement}(t) = D_c^{norm}(t) - D_f^{norm}(t)$$

$$\text{deviation}(t) = \bigl|\text{disagreement}(t)\bigr|$$

- $D_c^{norm} > D_f^{norm}$（动量主导）→ disagreement > 0 → **做多方向**（追涨）。
- $D_c^{norm} < D_f^{norm}$（回归主导）→ disagreement < 0 → **做空方向**（产业定价）。
- $|\text{deviation}|$ 越大 = 两类力量背离越剧烈 = 系统越失衡。

#### 步骤5：生成交易信号

$$\text{signal\_dir}(t) = \begin{cases} +1 & \text{disagreement}(t) > 0 \text{（动量主导，做多）} \\ -1 & \text{disagreement}(t) < 0 \text{（回归主导，做空）} \\ 0 & \text{disagreement}(t) = 0 \end{cases}$$

仓位分级（按 deviation 的滚动分位）：

$$\text{position\_size}(t) = \begin{cases} 1.0 & \text{main\_trigger} \wedge \text{deviation} > q_{95}\text{（满仓）} \\ 0.6 & \text{main\_trigger} \wedge \text{deviation} \leq q_{95}\text{（60%仓）} \\ 0.0 & \text{其他（空仓）} \end{cases}$$

开仓条件（双信号共振）：

$$\text{open\_signal}(t) = \text{main\_trigger}(t) \wedge \text{aux\_confirm}(t)$$

其中 $\text{main\_trigger} = \mathbb{1}[\text{deviation} > q_{90}]$，$\text{aux\_confirm} = \text{vol\_anomaly} \vee \text{td\_flip}$。

#### 每步变量来源汇总

| 变量 | 来源 | 时序 |
|------|------|------|
| $P(t)$, $P_{fund}(t)$, $P(t-1)$ | T 日及更早收盘价/基本面价 | T 日收盘后可观测 |
| $D_f(t)$, $D_c(t)$ | 由上述计算 | T 日 |
| $\text{profit}_f(t)$, $\text{profit}_c(t)$ | 窗口 $[t-20, t)$ 累乘 | 仅 T 及更早 |
| $n_f(t)$, $n_c(t)$ | Logit(γ×profit) | T 日 |
| $D_c^{norm}$, $D_f^{norm}$ | 180 日滚动 rank | 仅 T 及更早 |
| disagreement, deviation | norm 之差 | T 日 |
| 信号/仓位 | deviation 分位 | T 日 |

**结论：所有输入均为 T 日及更早数据，无任何 T+1 及之后信息。**

---

## 模块2：时序逻辑专项审计

### 2.1 逐行时序梳理

HAM 信号链的 6 步，逐步确认时序：

| 步骤 | 操作 | 数据范围 | 时序判定 |
|------|------|----------|----------|
| 1 需求函数 | $D_f = \alpha(P_{fund}-P)$, $D_c = \beta(P-P_{t-1})$ | T 日 + T-1 日 | ✓ 仅 T 及更早 |
| 2 滚动收益 | $\text{profit} = \sum_{i=t-W}^{t-1} D \cdot \Delta P$ | 窗口 $[t-20, t)$，max 价格索引 t | ✓ 不含 $P(t+1)$ |
| 3 Logit 权重 | $n = \exp(\gamma \cdot \text{profit}) / \sum$ | 由 profit 计算 | ✓ |
| 4 滚动标准化 | rank/quantile over $[t-180, t]$ | 180 日窗口 | ✓ min_periods 防前视 |
| 5 分歧度 | deviation = |norm_c - norm_f| | ✓ |
| 6 信号仓位 | deviation vs 滚动分位阈值 | 180 日窗口 | ✓ |

**关键修复（exp401 前视修复）**：步骤2的窗口 $[t-W, t)$ 严格到 `price_change[t-1] = P(t-1) - P(t-2)`，**不含 `price_change[t]`**，彻底消除 1 日未来价格泄露。这是 exp401 修复的核心，本审计确认修复有效。

### 2.2 Shift-equivariance 校验原理与结果

**原理**：信号函数 $S(x[0..n])$ 满足 shift-equivariance（平移等变性）当且仅当：

$$S(x[0..k])[k] = S(x[0..n])[k] \quad \forall n > k$$

即目标日 k 的信号只依赖 $x[0..k]$，不受 $x[k+1..n]$ 未来数据影响。若存在未来函数，追加未来数据会改变历史信号。

**测试方法**：截断到目标日及之前（删除所有未来数据），重算完整信号链，与全量信号对比目标日。逐字段比对 disagreement/deviation/signal_dir/main_trigger/open_signal/position_size。

**结果（8 个目标日，跨 2024-2026）**：

| 日期 | disagreement(全) | disagreement(截断) | deviation | 方向 | 主触发 | 开仓 | 仓位 | 结果 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 2024-04-01 | 0.899225 | 0.899225 | 0.899225 | +1 | True | True | 1.0 | ✓ |
| 2024-07-01 | 0.455556 | 0.455556 | 0.455556 | +1 | False | False | 0.0 | ✓ |
| 2024-10-15 | 0.161111 | 0.161111 | 0.161111 | +1 | False | False | 0.0 | ✓ |
| 2025-02-01 | nan | nan | nan | — | — | — | 0.0 | ✓ |
| 2025-05-20 | -0.211111 | -0.211111 | 0.211111 | -1 | False | False | 0.0 | ✓ |
| 2025-11-20 | -0.288889 | -0.288889 | 0.288889 | -1 | False | False | 0.0 | ✓ |
| 2026-02-01 | nan | nan | nan | — | — | — | 0.0 | ✓ |
| 2026-07-01 | 0.577778 | 0.577778 | 0.577778 | +1 | False | False | 0.0 | ✓ |

**结论：8/8 全部通过，5 个非 NaN 日逐位精确一致（偏差 0）。信号链严格因果，无未来函数。**

### 2.3 两类风险区分（关键，不混淆）

| 风险类别 | 性质 | 本审计是否检验 | 结论 |
|----------|------|:---:|------|
| **① 代码层面未来函数 bug** | 实现错误：T 日信号偷用了 T+1 数据 | ✓ 直接检验 | **未检测到未来函数**（shift-equivariance 全通过） |
| **② 参数历史拟合/因子漂移** | 模型理论风险：参数在样本内选定，因子可能不泛化 | ✗ 不检验 | 存在风险，exp416 跨品种已印证（锌上年化从 91% 衰减到 14%） |

**两类风险严格区分**：
- **代码 bug** 是"信号算错了"（用了不该用的未来数据），是确定性错误，shift-equivariance 能直接捕捉。本审计证明**代码无此 bug**。
- **参数历史拟合** 是"信号算对了但参数可能过拟合"，是概率性风险，shift-equivariance 无法捕捉（因为截断测试用的是同一套参数）。这属于**模型理论假设失效风险**，需外样本验证（exp416 模块4）。
- **不能混淆**：代码无未来函数 ≠ 模型无过拟合。exp416 跨品种衰减正是参数历史拟合的表现，但这是模型风险而非代码 bug。

---

## 模块3：模型假设、理论局限性清单

HAM 建模自带的先天假设，以及假设不成立的场景：

### 3.1 两类交易者划分假设

**假设**：市场参与者可清晰划分为"基本面 Agent"与"投机趋势 Agent"两类，且两类力量的占比 $n_f/n_c$ 有意义。

**不成立场景**：
- 现实交易中，同一机构可能同时是产业资本和投机者（如产业套保盘也做趋势）。
- 市场还有第三类参与者（量化高频、被动指数、套利盘），HAM 完全忽略。
- 不同时段两类主体的相对影响力变化，HAM 用静态 Logit 无法捕捉结构性变化。

### 3.2 参数静态不变假设

**假设**：alpha=0.3, beta=0.5, gamma=2, W=20 在所有市场状态下恒定有效。

**不成立场景**：
- 碳酸锂市场波动率、流动性、参与者结构随时期变化，固定参数可能在某些阶段失效。
- gamma=2 的 Logit 灵敏度在极端行情下可能不够（应更敏感）或过度（应更钝化）。
- **exp416 已印证**：HAM 在锌（参数不变）上年化从 91% 衰减到 14%，说明参数高度依赖碳酸锂市场特征。

### 3.3 "预期分歧大，后续价格会修复"的核心假设

**假设**：当两类 Agent 分歧大（deviation 高）时，价格会向强势一方修复，顺势交易（追 disagreement 方向）能获利。

**不成立场景**：
- **强趋势行情**：disagreement 反映动量主导（投机追涨），但强趋势延续时，追涨可能追在顶部，反转时亏损。
- **均值回归行情**：disagreement 反映产业回归主导，但若市场不回归（如基本面定价错误），做空会持续亏损。
- **exp416 已印证**：HAM 在极端跳空日顺势盈利（上行跳空 +0.1159），但趋势逆向段（exp415 2025Q4 -32.6%）跑输——核心假设在强趋势段失效。

### 3.4 模型忽略的现实市场要素

| 被忽略要素 | 影响 |
|------------|------|
| **流动性** | HAM 假设信号触发即可成交，忽略流动性枯竭时的滑点/无法成交 |
| **政策** | 监管政策（如碳酸锂行业政策、期货限仓）不在模型内，可能瞬间改变市场结构 |
| **保证金/强平** | 忽略保证金追缴、强制平仓的连锁效应，极端行情下可能被迫减仓 |
| **黑天鹅事件** | 地缘政治、自然灾害、突发政策等不可预测事件不在模型内 |
| **交易成本动态** | 假设固定 0.15%/边成本，实际滑点随流动性变化 |
| **微观结构** | 忽略订单簿深度、买卖价差、大单冲击等微观交易机制 |
| **跨市场联动** | 忽略外盘（LME/COMEX）、汇率、宏观利率等跨市场因素 |

### 3.5 历史回测中假设容易失效的场景

结合 exp415/exp416 回测证据：

1. **强单边趋势段**（exp415 2025Q4，超额 -32.6%）：HAM 多空切换跑输纯趋势，核心假设"分歧大→修复"失效。
2. **因子漂移段**（exp415 后半段超额 14.0%→6.5%）：参数静态假设在长周期下失效。
3. **跨品种场景**（exp416 锌，年化衰减 84%）：参数历史拟合导致跨品种泛化弱。
4. **极端跳空段**（exp415 B2）：HAM 无显式止损，跳空时 4 条信号平仓规则失效，核心假设"信号可及时平仓"失效。

---

## 模块4：演示样例（手工完整演算）

### 4.1 目标日与输入数据

选取 **2024-04-01**（exp415 单元测试的多头满仓样例日）。HAM 原生参数：alpha=0.3, beta=0.5, gamma=2, W=20, 滚动窗口=180。

输入数据（T 日及 T-1 日，T 日收盘后可观测）：

| 变量 | 值 | 来源 |
|------|:---:|------|
| $P(t)$ | 112600.00 | 2024-04-01 收盘价 |
| $P_{fund}(t)$ | 112600.00 | 2024-04-01 供需公允价值（当日恰好等于收盘价） |
| $P(t-1)$ | 107050.00 | 2024-03-29 收盘价 |

### 4.2 步骤1：需求函数

$$D_f(t) = \alpha \cdot (P_{fund} - P) = 0.3 \times (112600.00 - 112600.00) = \mathbf{0.0000}$$

$$D_c(t) = \beta \cdot (P - P_{t-1}) = 0.5 \times (112600.00 - 107050.00) = \mathbf{2775.0000}$$

**解读**：当日价格恰好等于公允价值（$P = P_{fund}$），产业回归压力为零；但价格从 107050 涨到 112600（涨 5550），投机趋势动量强烈（$D_c = 2775$）。

### 4.3 步骤2：滚动窗口收益

$$\text{profit}_f(t) = \sum_{i=t-20}^{t-1} D_f(i) \cdot \Delta P(i) = \mathbf{0.0000}$$

$$\text{profit}_c(t) = \sum_{i=t-20}^{t-1} D_c(i) \cdot \Delta P(i) = \mathbf{97{,}887{,}500.00}$$

窗口 $[t-20, t)$ 即 2024-03-04 ~ 2024-03-29（20 日），$\Delta P$ 范围 $[-1850, -1450]$。投机主体近 20 日累计损益巨大（9788万），远超产业主体（0）。

### 4.4 步骤3：Logit 策略切换权重

$$\gamma \cdot \text{profit}_f = 2 \times 0 = 0.0000 \quad \to \quad \text{exp}_f = e^{0 - 500} \approx 0.0000$$

$$\gamma \cdot \text{profit}_c = 2 \times 97887500 = 195{,}775{,}000 \quad \to \quad \text{clip 至 } 500 \quad \to \quad \text{exp}_c = e^{500-500} = 1.0000$$

$$n_f(t) = \frac{0}{0 + 1} = \mathbf{0.000000}$$

$$n_c(t) = \frac{1}{0 + 1} = \mathbf{1.000000}$$

**解读**：投机主体（Agent C）近 20 日表现极好（profit 巨大），Logit 权重把市场占比几乎全部分配给投机主体（$n_c = 1$）。产业主体表现差（profit=0），占比趋零。

### 4.5 步骤4：系统分歧度 D(T)

滚动标准化（180 日 rank 映射至 $[-1, 1]$）：

$$D_c^{norm}(t) = 0.906977 \quad D_f^{norm}(t) = 0.007752$$

$$\text{disagreement}(t) = D_c^{norm} - D_f^{norm} = 0.906977 - 0.007752 = \mathbf{0.899225}$$

$$\text{deviation}(t) = |0.899225| = \mathbf{0.899225}$$

**解读**：分歧度 0.899225，动量主导（disagreement > 0），产业与投机力量严重背离。

### 4.6 步骤5：辅助信号

$$\text{vol\_abs5}(t) = 0.047282 \quad \text{td\_flip}(t) = \text{True}$$

辅助确认 $\text{aux\_confirm} = \text{vol\_anomaly} \vee \text{td\_flip} = \text{False} \vee \text{True} = \mathbf{\text{True}}$

**解读**：5日波动率异动未触发，但 Total_Demand 符号发生翻转（td_flip=True），辅助确认成立。

### 4.7 步骤6：HAM 动态信号 + 仓位分级

滚动分位阈值（180 日）：

$$q_{90}(\text{main}) = 0.818526 \quad q_{95}(\text{high}) = 0.892339 \quad q_{40}(\text{neutral}) = 0.338604$$

$$\text{main\_trigger} = \mathbb{1}[\text{deviation} > q_{90}] = \mathbb{1}[0.899225 > 0.818526] = \mathbf{\text{True}}$$

$$\text{signal\_dir} = \mathbb{1}[\text{disagreement} > 0] = \mathbf{+1 \text{（多头）}}$$

$$\text{position\_size} = \mathbb{1}[\text{main\_trigger} \wedge \text{deviation} > q_{95}] = \mathbb{1}[0.899225 > 0.892339] = \mathbf{1.0 \text{（满仓）}}$$

$$\text{open\_signal} = \text{main\_trigger} \wedge \text{aux\_confirm} = \text{True} \wedge \text{True} = \mathbf{\text{True}}$$

### 4.8 最终交易信号

| 输出 | 值 |
|------|:---:|
| 方向 signal_dir | **+1（多头）** |
| 仓位 position_size | **1.0（满仓）** |
| 开仓信号 open_signal | **True（开仓）** |

**结论**：2024-04-01，HAM 发出**多头满仓开仓信号**。与 exp415 单元测试结果完全一致，验证手工演算链正确。

---

## 报告必答问题

### Q1：HAM 模拟的两类主体现实中是否真实存在？模型是简化抽象还是真实复刻？

**答**：两类主体（产业基本面 Agent、投机趋势 Agent）在现实市场中**有对应的交易者群体，但模型是简化抽象，不是真实复刻**。
- 产业基本面 Agent 对应产业链产业资本、产业买家、长线配置盘；投机趋势 Agent 对应 CTA、动量交易盘。这些群体真实存在。
- 但 HAM 用严格的数学函数 $D_f = \alpha(P_{fund}-P)$ 和 $D_c = \beta(P-P_{t-1})$ 描述其行为，是**对现实行为的理想化抽象**。现实中同一机构可能兼具两种特征，市场还有量化高频、被动指数、套利盘等第三类参与者。
- HAM 的价值不在复刻交易者，而在用两种对抗力量刻画"产业定价压力"与"投机追涨压力"的背离——这个背离（disagreement）才是策略核心。

### Q2：明确区分代码 bug 风险 vs 模型理论假设失效风险

| 风险 | 性质 | 本审计结论 |
|------|------|-----------|
| **代码层面未来函数 bug** | 实现错误：T 日信号偷用 T+1 数据 | **未检测到**（shift-equivariance 8 日全通过，5 非NaN日逐位一致） |
| **模型理论假设失效** | 参数历史拟合、因子漂移、假设不成立 | **存在风险**（exp416 跨品种：锌年化衰减 84%，印证参数依赖碳酸锂行情） |

两者严格区分：代码无未来函数 ≠ 模型无过拟合。代码 bug 是确定性的"算错"，模型风险是概率性的"算对但参数可能不泛化"。

### Q3：模型成立的前提条件与失效场景

**成立前提**：
1. 市场存在"产业定价压力"与"投机追涨压力"两种对抗力量（HAM 抽象假设）。
2. 两类力量分歧大时，价格向强势方修复（核心交易假设）。
3. 参数 alpha/beta/gamma/W 在目标市场相对稳定有效。
4. 市场流动性充足，信号可及时成交（无极端跳空失效）。
5. 无重大政策/黑天鹅颠覆市场结构。

**失效场景**：
1. **强单边趋势段**（exp415 2025Q4 -32.6%）：多空切换跑输纯趋势，核心假设"分歧大→修复"失效。
2. **因子漂移段**（exp415 后半段超额 14.0%→6.5%）：静态参数在长周期下逐渐失效。
3. **跨品种场景**（exp416 锌衰减 84%）：参数历史拟合导致跨品种泛化弱。
4. **极端跳空段**（exp415 B2）：无显式止损，4 条信号平仓规则在跳空下失效。
5. **流动性枯竭/政策突变**：模型忽略流动性与政策，极端行情下信号无法执行或被政策颠覆。

---

## 文件清单

| 用途 | 路径 |
|------|------|
| 手工演算脚本 | `model_ham/exp417_ham_model_theory/exp417_manual_calc.py` |
| 时序审计脚本 | `model_ham/exp417_ham_model_theory/exp417_temporal_audit.py` |
| 主报告 | `reports/exp417_ham_model_theory_audit.md` |
| 演算步骤数据 | `model_ham/exp417_ham_model_theory/data/exp417_manual_calculation_steps.csv` |
| 演算输入窗口 | `model_ham/exp417_ham_model_theory/data/exp417_manual_calc_input_window.csv` |
| Shift-equivariance 校验 | `model_ham/exp417_ham_model_theory/data/exp417_shift_equivariance_test.csv` |
| 交接文档 | `model_ham/HANDOVER_round18_exp417_ham_model_theory.md` |

---

## 复现命令

```bash
cd /home/ubuntu/lithium-engine
# 模块4 手工演算 (2024-04-01)
/usr/bin/python3 model_ham/exp417_ham_model_theory/exp417_manual_calc.py
# 模块2 时序审计 (shift-equivariance)
/usr/bin/python3 model_ham/exp417_ham_model_theory/exp417_temporal_audit.py
```

**坑点**：Pyright 报 exp4xx import 误报（sys.path 动态导入），全部忽略。

---

*生成时间：2026-09-10 | exp417 HAM 模型底层建模原理审计 | 代码无未来函数(shift-equivariance 8日全通过)，模型有参数历史拟合风险(exp416跨品种印证)；手工演算 2024-04-01 多头满仓信号验证完整；两类风险严格区分*
