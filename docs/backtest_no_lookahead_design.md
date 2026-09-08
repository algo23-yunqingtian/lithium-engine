# 防透视回测体系设计 v0.1

> 2026-08-01 · 实现：`/home/ubuntu/lithium_calendar/lithium_backtest.py`
> 目标：多主体博弈系统的策略验证**不偷看未来**，每个主体在决策日 T 只能使用"当时真实可得"的信息。

## 1. 为什么要防透视（Look-ahead Bias）

传统回测常见三类污染：
1. **信号计算污染**：用全样本均值/方差做标准化 → T 日信号里混入了未来数据；
2. **数据发布污染**：T 日直接用当日收盘后的周度/月度数据 → 现实中这些数据几天后才发布；
3. **参数选择污染**：用整个回测区间调参 → 参数隐式包含未来信息。

## 2. 本引擎的防透视机制

### 2.1 数据时钟（Data Clock）

每个主体在日期 T 只能访问 `可用数据时间戳 ≤ T - lag`：

| 数据 | 发布滞后 | 决策日可用 |
|---|---|---|
| 日度价格 | 1 交易日 | T-1 收盘价及之前 |
| 基本面指数 | 1 交易日 | T-1 及之前 |
| 周度库存/产量 | 3 交易日 | T-3 及之前 |
| 月度产量/需求 | 7 交易日 | T-7 及之前 |

实现：`available_signals(date_idx, lag)` 只取 `self.dates[idx - lag]` 及更早。

### 2.2 滚动窗口标准化（Rolling z-score）

`rolling_zscore(date_idx, field, window=250)`：
- 样本 = `[date_idx-250, date_idx-1]` 的历史观测；
- 当前值 = `date_idx-1` 的值（**不含当日**）；
- 样本数 < 20 时返回中性 0（数据不足不硬算）。

对比：`fundamental_indices.py` 的生产指数同样用 250 日滚动 z-score，因此回测引擎直接复用同一套"只含历史"的指数，两处口径一致。

### 2.3 Walk-forward 样本切分

- 训练窗口：2023-07 ~ 2024-12（用于标定 agent 敏感度参数）；
- 测试窗口：2025-01 ~ 2026-07（**冻结参数**跑样本外）；
- 命令行：`--train-start/--train-end/--test-start/--test-end` 自由切分。

### 2.4 主体状态机

每个 agent 在 T 日的决策基于：
- 六维基本面指数滚动 z-score（供应/需求/库存/利润/情绪/基差，各自敏感度不同）；
- 价格动量（retail 5日、institution 20日，只用 T-1 及之前收盘）；
- 输出：`(direction, strength)` → 目标仓位，每次最多调仓 20%。

> v0.1 状态机为简化版（线性信号合成），v0.2 将替换为 `agent_psychology_calc.py` 的完整状态机（锚点/价位带/状态迁移），保证"回测用模型 = 生产用模型"。

## 3. 已验证能力（2026-07-31 冒烟测试）

- 加载 734 个交易日价格 + 728 日基本面指数 ✅
- 44 日样本外回测跑通，7 个 agent 独立账户 ✅
- 防透视滚动 z-score 正确使用历史窗口 ✅
- 输出 `/home/ubuntu/analysis/output/backtest_result.json` ✅

## 4. 已知局限与 v0.2 路线图

| 问题 | 现状 | 计划 |
|---|---|---|
| 盈亏计算简化 | pos=0 时已实现盈亏未累计 | 用 `position` 表真实撮合逻辑 |
| 状态机与生产不一致 | 线性信号 vs agent_psychology 完整状态机 | 迁移完整状态机 |
| 无交易成本/滑点 | 未计 | 加 0.02% 手续费 + 1‰ 滑点 |
| 无仓位风控 | 可满仓 | 单 agent 最大 ±80%，组合在险 |
| 参数敏感度 | 人工标定 | walk-forward + 网格搜索，但严格限制在训练窗 |
| 情绪指数实时性 | 小作文信号滞后 | 对齐信号发布时间戳 |

## 5. 使用方式

```bash
# 快速冒烟
python /home/ubuntu/lithium_calendar/lithium_backtest.py --quick

# 完整样本外回测
python /home/ubuntu/lithium_calendar/lithium_backtest.py \
  --train-start 2023-07-01 --train-end 2024-12-31 \
  --test-start 2025-01-01 --test-end 2026-07-31

# 输出
/home/ubuntu/analysis/output/backtest_result.json
```
