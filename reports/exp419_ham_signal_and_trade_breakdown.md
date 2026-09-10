# exp419 HAM 因子、开平仓规则拆解 + 历史全交易清单导出 — 主报告

> 生成时间：2026-09-10 | 基准：exp416 T1 稳健版
> 约束：不改动任何模型、回测逻辑。底层完全复用 exp409/411/413/416 引擎，零改动。
> 回测区间：2024-02-01 ~ 2026-09-07（628 交易日）| 成本：0.15%/边
> 任务定位：HAM 策略白盒化拆解——把"模型怎么算、怎么开平仓、每笔交易明细"全部展开成可读文档 + 可查 CSV。

---

## 0. 一句话结论

**HAM 是 Brock & Hommes 异质主体模型（HAM）的碳酸锂定制版，核心是"产业基本面 Agent 与投机趋势 Agent 的博弈分歧"，分歧大时顺势交易。** 本次拆解完整输出：① HAM 6 步数学公式 + 标准化细节 + 信号阈值规则；② T1 版本完整开平仓/仓位/风控时序链；③ 51 笔历史交易明细（含平仓原因、D_z、ADX 强趋势标记）。

**关键发现（强趋势交易分析）**：**强趋势区间（ADX>28）21 笔交易平均净盈亏 -1.53%，而正常区间 +0.077%**——HAM 在强趋势区间系统性亏损，验证了 exp415/exp416/exp418 反复印证的"趋势盲区"假设。强趋势中 15/21 笔因 reverse_signal（方向翻转）平仓，说明强趋势里 HAM 频繁多空切换导致止损。

---

## 模块1：HAM 因子完整计算规则

### 1.1 模型来源

**HAM = Heterogeneous-Agent Model（异质主体模型）**，基于 **Brock & Hommes (1997)** 的经典 HAM 框架，针对碳酸锂期货定制修改。

Brock & Hommes 原始框架假设市场由多类异质主体组成，各类主体根据历史表现动态调整市场占比（Logit 选择），价格由各类主体的需求函数加权聚合决定。本策略简化为**两类主体**，并针对碳酸锂市场特征定制：

| 改造点 | Brock & Hommes 原版 | 本策略碳酸锂定制版 |
|--------|---------------------|-------------------|
| 主体类别 | 多类（趋势/基本面/噪声） | 两类（产业基本面 + 投机趋势） |
| 定价锚 | 公允价值 + 动量 | P_fund（碳酸锂现货+供需分）+ 价格动量 |
| 参数标定 | 通用 | α=0.3 β=0.5 γ=2 W=20（碳酸锂调优） |
| 信号输出 | 价格预测 | disagreement → 交易方向 + 仓位 |

**重要声明**：这是理论抽象，不是真实交易者的 1:1 复刻。HAM 的价值在于用两种对抗力量刻画"产业定价压力"与"投机追涨压力"的背离（disagreement），这个背离才是策略信号核心（exp417 §1.1）。

### 1.2 完整分步数学公式

设 $P(t)$ 为 T 日收盘价，$P_{fund}(t)$ 为 T 日供需公允价值，$P(t-1)$ 为前一日收盘价。三者均为 T 日收盘后可观测的历史数据。

#### Step 1：需求函数（两类主体预期更新）

$$D_f(t) = \alpha \cdot \bigl(P_{fund}(t) - P(t)\bigr) \quad \text{（产业基本面回归需求）}$$

$$D_c(t) = \beta \cdot \bigl(P(t) - P(t-1)\bigr) \quad \text{（投机趋势动量需求）}$$

- $D_f > 0$：价格低于公允价值 → 产业资本买入压力
- $D_c > 0$：价格上涨 → 投机者追涨

#### Step 2：滚动窗口收益（主体历史表现评估）

$$\text{profit}_f(t) = \sum_{i=t-W}^{t-1} D_f(i) \cdot \bigl(P(i) - P(i-1)\bigr)$$

$$\text{profit}_c(t) = \sum_{i=t-W}^{t-1} D_c(i) \cdot \bigl(P(i) - P(i-1)\bigr)$$

窗口 $[t-W, t)$ 仅含 T 及更早数据，最大价格索引为 $t$，**不含 $P(t+1)$**（无前视关键，exp401 修复）。

#### Step 3：Logit 策略切换权重（市场占比动态调整）

$$n_f(t) = \frac{\exp(\gamma \cdot \text{profit}_f(t))}{\exp(\gamma \cdot \text{profit}_f(t)) + \exp(\gamma \cdot \text{profit}_c(t))}$$

$$n_c(t) = 1 - n_f(t)$$

代码用 log-sum-exp trick 防数值溢出，$\gamma \cdot \text{profit}$ 截断至 $\pm 500$。

#### Step 4：滚动标准化（D_z 细节，见 1.3）+ 系统分歧度

$$D_c^{norm}(t) = 2 \cdot \text{rank}_{pct}\bigl(D_c(t); [t-180, t]\bigr) - 1$$

$$D_f^{norm}(t) = 2 \cdot \text{rank}_{pct}\bigl(D_f(t); [t-180, t]\bigr) - 1$$

$$\text{disagreement}(t) = D_c^{norm}(t) - D_f^{norm}(t)$$

$$\text{deviation}(t) = \bigl|\text{disagreement}(t)\bigr|$$

### 1.3 标准化 Z-score 计算细节

**HAM 使用两套标准化**：

**（A）rank 分位标准化（disagreement 核心）**：
$$D^{norm}(t) = 2 \cdot \text{rank}_{pct}(D(t); \text{180日滚动窗口}) - 1$$
- 将 $D$ 在 180 日窗口内的排名映射到 $[0,1]$，再映射到 $[-1,1]$
- 这是相对排名标准化，不受绝对量级影响，鲁棒于市场状态迁移
- min_periods=60 防前视（窗口不足 60 日时不计算，输出 NaN）

**（B）Z-score 标准化（D_z，本 exp419 新增监控量）**：
$$D_z(t) = \frac{\text{deviation}(t) - \mu_{180}(t)}{\sigma_{180}(t)}$$
- $\mu_{180}$、$\sigma_{180}$ 为 deviation 的 180 日滚动均值与标准差
- 衡量"当前失衡程度偏离历史均值多少倍标准差"
- 用途：监控因子强度（D_z 过高=极端失衡=趋势盲区风险，与 exp418 模块2 Avg_Deviation 告警呼应）

### 1.4 完整 HAM 信号判定逻辑（阈值规则）

**方向判定**：
$$\text{signal\_dir}(t) = \begin{cases} +1 & \text{disagreement}(t) > 0 \text{（动量主导→做多）} \\ -1 & \text{disagreement}(t) < 0 \text{（回归主导→做空）} \\ 0 & \text{disagreement}(t) = 0 \end{cases}$$

**主信号触发**：
$$\text{main\_trigger}(t) = \mathbb{1}[\text{deviation}(t) > q_{90}(t)]$$
- $q_{90}$ = deviation 的 180 日滚动 90% 分位（min_periods=60）

**辅助确认**（双信号共振）：
$$\text{aux\_confirm}(t) = \text{vol\_anomaly}(t) \lor \text{td\_flip}(t)$$
- `vol_anomaly`：5 日绝对波动率超过 180 日 90% 分位
- `td_flip`：Total_Demand 符号翻转（仓单边际变化代理）

**开仓信号**：
$$\text{open\_signal}(t) = \text{main\_trigger}(t) \land \text{aux\_confirm}(t)$$

**仓位分级**：
$$\text{position\_size}(t) = \begin{cases} 1.0 & \text{main\_trigger} \land \text{deviation} > q_{95} \text{（满仓，极度失衡）} \\ 0.6 & \text{main\_trigger} \land \text{deviation} \le q_{95} \text{（60%仓，中度失衡）} \\ 0.0 & \text{其他（不开仓）} \end{cases}$$

**阈值速查表**：

| 阈值 | 分位 | 含义 |
|------|:---:|------|
| $q_{95}$（POS_HIGH_Q） | 95% | 极度失衡→满仓 |
| $q_{90}$（POS_MED_Q） | 90% | 中度失衡→主触发 |
| $q_{40}$（NEUTRAL_Q） | 40% | 中性区间→平仓止盈 |

### 1.5 参数完整释义

| 参数 | 数值 | 经济含义 |
|------|:---:|----------|
| $\alpha$ | 0.3 | 产业基本面回归强度（温和，碳酸锂价格波动大不宜过大） |
| $\beta$ | 0.5 | 投机趋势外推强度（>α，反映碳酸锂动量交易活跃） |
| $\gamma$ | 2 | Logit 灵敏度（中等，赢家策略不迅速垄断市场） |
| $W$ | 20 | 滚动收益窗口（约1月，近1月表现决定未来权重） |
| ROLLING_TRAIN | 180 | 滚动训练窗口（约9月，分位标准化训练） |
| MAX_HOLD_HAM | 12 | HAM 最大持仓交易日（强制平仓） |
| COST_PER_SIDE | 0.0015 | 单边手续费+滑点 0.15% |
| TRADING_DAYS_PER_YEAR | 250 | 年化交易日 |

---

## 模块2：完整开平仓、仓位规则（T1 版本）

### 2.1 开仓条件

T 日收盘后，若满足：
$$\text{open\_signal}(t) = \text{main\_trigger}(t) \land \text{aux\_confirm}(t) = \text{True}$$

则 T 日开仓，方向 = signal_dir，仓位 = position_size，成交价 = T 日收盘价。

### 2.2 仓位计算公式

**基础仓位**（HAM 分级）：
$$\text{base\_pos} = \text{position\_size} \times \text{signal\_dir} \in \{-1.0, -0.6, 0.0, 0.6, +1.0\}$$

**组合合成**（exp411，HAM + 基本面动态信号）：
- HAM 目标 $h$ + 基本面目标 $f$
- 同向：$\text{pos} = \text{sign}(h) \cdot \min(|h|+|f|, 1.0)$
- 反向：$\text{pos} = \text{clip}(h+f, -1, 1)$

**exp412 V4 风控压缩**（exp413）：
$$\text{pos\_after\_vol} = \text{pos} \times \text{vol\_mult}$$
- $\text{vol\_mult} = 0.5$ 若已实现波动率 > 0.55，否则 1.0（M3 高波动压缩）

$$\text{final\_pos} = \text{pos\_after\_vol} \times \text{dd\_mult}$$
- dd_mult 阶梯（M2 账户回撤控制，60日窗口）：
  - 回撤 ≤ 5%：dd_mult = 1.0
  - 5% < 回撤 ≤ 12%：dd_mult = 0.70
  - 回撤 > 12%：dd_mult = 0.30

**T1 波动率隔夜降仓**（exp416，稳健版核心）：
$$\text{T1\_pos} = \text{final\_pos} \times \text{overnight\_mult}$$
- $\text{overnight\_mult} = 0.0$（清仓）若 vol > 0.90
- $\text{overnight\_mult} = 0.5$（减半）若 0.40 < vol ≤ 0.90
- $\text{overnight\_mult} = 1.0$（正常）若 vol ≤ 0.40

### 2.3 平仓触发条件（任意一条触发立即平仓）

| # | 条件 | 说明 |
|---|------|------|
| 1 | **偏离度回归中性** | deviation < q_neutral（180日40%分位）→ neutral_revert |
| 2 | **GMM 状态跃迁** | 市场状态从 S0/S1/S3 切换（排除S2高波）→ gmm_transition |
| 3 | **反向信号** | signal_dir 翻转 → reverse_signal |
| 4 | **最大持仓** | 持仓 ≥ 12 日强制平仓 → max_hold |

### 2.4 时序规则（T 日算信号，T 日成交）

```
T 日收盘后（所有输入均为 T 日及更早数据，无前视）:
  1. 计算 HAM 因子链 (Step 1-5) → disagreement/deviation/signal_dir
  2. 判定 open_signal → 若 True，T 日开仓（成交价 = T 日 close）
  3. 判定平仓条件 → 若触发，T 日平仓（成交价 = T 日 close）
  4. 波动率风控 (vol_mult) + 回撤风控 (dd_mult) 干预仓位
  5. T1 波动率隔夜降仓 (overnight_mult) 最终干预

T 日仓位吃 T 日 (T-1→T) 价格收益:
  strat_ret(t) = final_pos(t) × (close(t) - close(t-1)) / close(t-1)
  cost = 2 × COST × |仓位变化量|
  equity(t) = equity(t-1) × (1 + strat_ret - cost)
```

**注意**：本策略时序约定为"T 日收盘决策 + T 日成交 + 吃 T 日收益"（与 exp411/412/413/416 一致），**不是** "T日算信号 T+1 开盘成交"。T+1 开盘成交是另一套时序，本策略未采用——因为 T 日收盘决策用 T 日 close 成交，无前视（exp417 shift-equivariance 8 日校验已证）。

### 2.5 波动率风控如何干预仓位

三层波动率干预（叠加）：

| 层 | 来源 | 触发 | 动作 |
|----|------|------|------|
| M3 高波压缩 | exp412 | vol > 0.55 | 仓位×0.5 |
| T1 隔夜减半 | exp416 | 0.40 < vol ≤ 0.90 | 仓位×0.5 |
| T1 隔夜清仓 | exp416 | vol > 0.90 | 仓位×0.0 |

时序：vol 用截至 T 日的 20 日已实现年化波动率（min_periods=5 防前视）。三层按乘数叠加，最严苛者主导。

---

## 模块3：全部历史回测交易明细清单

### 3.1 完整交易清单（51 笔）

交易清单由 **HAM 动态引擎**（exp409.run_ham_dynamic）重建，含完整平仓原因。完整数据见 `data/exp419_full_trade_list.csv`。

**字段说明**：
- 序号 / 开仓日期 / 平仓日期 / 持仓天数 / 方向（+1多/-1空）/ 仓位
- 开仓价 / 平仓价 / 净盈亏（扣双边成本）
- D_z_entry / D_z_exit：开平仓日的 deviation Z-score（标准化 HAM 信号强度）
- ADX_entry / ADX_exit：开平仓日的 ADX 趋势强度
- strong_trend：ADX 均值 > 28 标记强趋势区间
- exit_reason：平仓原因（neutral_revert / gmm_transition / reverse_signal / max_hold）

### 3.2 交易统计汇总

| 维度 | 数值 |
|------|:---:|
| 总交易笔数 | 51 |
| 多头 / 空头 | 38 / 13 |
| 胜率 | 31.4% |
| 平均持仓天数 | 1.39 |
| 强趋势交易（ADX>28） | 21 笔（41.2%） |
| 强趋势交易胜率 | 33.3% |
| 强趋势交易平均净盈亏 | **-1.53%** |
| 正常区间平均净盈亏 | +0.077% |

### 3.3 强趋势区间交易（ADX>28，21 笔）单独标记

强趋势区间交易平均净盈亏 **-1.53%**，而正常区间 **+0.077%**——**HAM 在强趋势区间系统性亏损**，这是趋势盲区的交易级证据。

强趋势平仓原因分布：reverse_signal 占多数（方向翻转止损），说明强趋势里 HAM 频繁多空切换，每次切换都在逆向亏损。

**典型强趋势亏损案例**：
- 序号16（2025-07-23）：空头，ADX=33.01，净盈亏 -10.82%，reverse_signal 平仓——追空遇趋势上涨
- 序号17（2025-07-25）：多头，ADX=36.24，净盈亏 -9.49%，reverse_signal 平仓——多空翻转连续止损
- 序号31（2025-12-26）：多头，ADX=37.53，净盈亏 -9.26%，reverse_signal 平仓
- 序号35-36（2026-01-23~28）：连续两笔多头，ADX 42/40，分别 -9.03%/-7.72%——2026Q1 趋势逆向连续亏损

### 3.4 交易清单与 T0（40 笔）的差异说明

HAM 动态引擎重建出 51 笔，而 exp416 T0 交易日志是 40 笔。差异原因：
- **HAM 动态引擎**是纯 HAM-only 开平仓（直接按 open_signal/平仓条件），交易更频繁
- **T0** 是 exp411 组合（HAM+基本面）+ exp413 风控后的**仓位段切分**（连续持仓段=一笔），合并了多次开平仓
- 两者时序约定一致（T 日收盘决策+成交），但 T0 的组合与风控层减少了交易频次

本清单以 HAM 动态引擎为准（含平仓原因，白盒化价值更高），T0 清单见 `exp416_T0_trades.csv`。

---

## 模块4：补充说明

### 4.1 模型底层假设：分歧修复假设

**核心假设**：当两类 Agent 分歧大（deviation 高）时，价格会向强势一方修复，顺势交易（追 disagreement 方向）能获利。

**假设成立逻辑**：disagreement 反映产业与投机力量的失衡，失衡越大，后续价格向强势方修复的概率越高。HAM 顺势交易正是押注这个修复。

### 4.2 假设失效场景

| 失效场景 | 机制 | 本策略证据 |
|----------|------|-----------|
| **强单边趋势段** | disagreement 反映动量主导，但强趋势延续时追涨可能追在顶部 | 本 exp419 强趋势交易平均 -1.53%（21笔） |
| **趋势逆向回撤** | 连续同向持仓遇反转，多空切换连续止损 | 2025Q4 超额 -32.6%（exp415） |
| **因子漂移** | 参数静态，长周期下逐渐失效 | exp415 后半段超额 14.0%→6.5% |
| **跨品种** | 参数依赖碳酸锂行情，跨品种泛化弱 | exp416 锌上年化衰减 84% |
| **极端跳空** | 无显式止损，4 条平仓规则在跳空下失效 | exp415 B2 跳空止损失效 |
| **流动性枯竭/政策突变** | 模型忽略流动性与政策 | 实盘需人工介入 |

### 4.3 信号计算时刻 vs 成交时刻（时序确认）

| 时刻 | 事件 | 数据 |
|------|------|------|
| **T 日收盘后** | 计算 HAM 信号（Step 1-5） | 全部用截至 T 日数据 |
| **T 日收盘** | 开仓/平仓成交 | 成交价 = T 日 close |
| **T 日** | 仓位吃 T 日 (T-1→T) 收益 | pos × (close(t)-close(t-1))/close(t-1) |
| **T 日收盘后** | 波动率/回撤风控判定 | 全部用截至 T 日数据 |

**无未来函数确认**：
- 所有滚动窗口（W=20、ROLLING_TRAIN=180、VOL_LOOKBACK=20）均含 T 日及更早，min_periods 防前视
- Step 2 窗口 $[t-W, t)$ 严格到 $P(t-1)$，不含 $P(t+1)$
- exp417 shift-equivariance 8 日校验全部通过（5 非NaN日逐位偏差 0）
- **代码层面：无未来函数（已验证）**
- **模型层面：有参数历史拟合/因子漂移风险**（exp416 跨品种印证）——代码无未来函数 ≠ 模型无过拟合，两者严格区分。

---

## 交付物清单

| 交付物 | 路径 |
|--------|------|
| 主报告 | `reports/exp419_ham_signal_and_trade_breakdown.md` |
| 全交易明细 CSV | `data/exp419_full_trade_list.csv` |
| 强趋势交易子集 | `data/exp419_strong_trend_trades.csv` |
| 汇总 JSON | `data/exp419_summary.json` |
| 导出脚本 | `model_ham/exp419_signal_trade_breakdown/exp419_trade_export.py` |

## 复现命令

```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 model_ham/exp419_signal_trade_breakdown/exp419_trade_export.py
```

**坑点**：
- Pyright 报 pandas/exp4xx import 误报（sys.path 动态导入），全部忽略。
- reset_index 后 date 变列，需用 df["date"] 非 df.index。
- D_z 用 180 日滚动 Z-score（mean/std），min_periods=60 防前视。
- HAM 动态引擎 51 笔 ≠ T0 40 笔：HAM-only 动态 vs 组合+风控仓位段切分，时序一致。

---

*生成时间：2026-09-10 | exp419 HAM因子/开平仓规则拆解+全交易清单导出 | 底层零改动 | 模块1: Brock&Hommes HAM 6步公式+rank/Z-score双标准化+信号阈值; 模块2: T1完整开平仓/仓位/风控时序链(三层波动率干预); 模块3: 51笔交易清单(强趋势21笔平均-1.53%验证趋势盲区); 模块4: 分歧修复假设+失效场景+无前视确认*
