# 信号驱动互动层（Signal-Driven Interactions）v1.0

> 2026-08-01 部署 · 在 Phase 4/5（交易逻辑强度分析 + 博弈互动矩阵）基础上新增
> 核心思路：**市场信号（升贴水/库存/产量/价格动量）→ Agent 信号立场 → 阵营划分 → 互动生成**

## 1. 为什么做这一层

原 Phase 5 博弈互动（info_conflict / behavior_follow）只吃 **agent 内部仓位/评分**：
- 评分差大 → 信息冲突
- 仓位同向变动 → 行为跟随

问题：升贴水、库存、产量这些**外部基本面信号完全没有参与**，agent 的"分歧"是机械的分数差，而不是基于对真实市场的不同解读。

本次升级让互动由**真实基本面信号驱动**，回答"agent 为什么互相对立/抱团"——因为不同 agent 关注的基本面维度不同（贸易商盯升贴水、机构盯库存、矿商盯产量、散户盯价格动量）。

## 2. 数据源（全部来自 lithium.db，纯数学计算，无 LLM）

| 信号 | 表/列 | 频率 | 当前状态 (2026-07-31) |
|---|---|---|---|
| 升贴水 | lithium_daily_prices col_idx 2(低幅)/3(高幅)，取中值 | 日度 | -500 元/吨（贴水收窄中） |
| 库存 | lithium_weekly col_idx 2（大样本库存总计） | 周度 | 107,877 吨，周均 -5,501 吨（-5.1% 强去库） |
| 产量 | lithium_weekly col_idx 12（产量总计） | 周度 | 22,841 吨/周，周均 -671 吨（-2.9% 减产） |
| 月度产量 | lithium_monthly col_idx 1 | 月度 | 115,320 吨/月（环比 +1.8%） |
| 价格动量 | prices 表 5 日涨跌幅 | 日度 | 137,760 元，5 日 -6.43% |

## 3. 核心模型

### 3.1 信号归一化

每个信号 → `(direction, strength)`：
- direction ∈ {-1, 0, +1}：+1 利多 / -1 利空 / 0 中性
- strength ∈ [0, 1]：信号强弱

| 信号 | 方向逻辑 | 强度归一化 |
|---|---|---|
| 升贴水 | 中值 + 20日变化：正=利多 | level_norm = cur/2500，chg_norm = Δ20d/1500，各占 60%/40% |
| 库存 | 去库=利多（取反） | 周均变化% / 3% |
| 产量 | 减产=利多（取反） | 周均变化% / 5% |
| 月度产量 | 减产=利多 | 环比% / 8% |
| 价格动量 | 5日上涨=利多 | 5日% / 5% |

综合信号 composite = Σ(direction × strength) / Σ(strength)（仅供展示，不直接用于分组）。

### 3.2 Agent 信号暴露矩阵（SIGNAL_EXPOSURE）

每个 agent 对四类信号的敏感度 (0~1) 与偏好方向：

| Agent | 升贴水 | 库存 | 产量 | 需求 | 主导信号 |
|---|---|---|---|---|---|
| smelter 冶炼厂 | 0.7 | 0.6 | 0.5 | 0.4 | 升贴水 |
| merchant 贸易商 | 1.0 | 0.8 | 0.3(-1) | 0.6 | 升贴水 |
| institution 机构 | 0.5 | 0.9 | 0.7 | 0.8 | 库存 |
| arbitrage 套利商 | 1.0 | 0.5 | 0.2 | 0.2 | 升贴水 |
| retail 散户 | 0.3 | 0.2 | 0.1 | 0.3 | 价格动量 (1.0) |
| resource 矿商 | 0.4 | 0.6 | 0.9 | 0.3 | 产量 |
| policy 政策 | 0.1 | 0.3 | 0.2 | 0.2 | 库存 |

> merchant 对产量的偏好方向为 -1（产量增加对贸易商反而是货源充足/议价空间，轻微利空）——保留了差异化设定。

### 3.3 阵营模型（v1.1）

```
每个 agent:
  sig_dir   = 主导信号当前方向 × agent 偏好方向  → 信号立场（看多/看空）
  score_dir = 当日 base_score 方向               → 规则引擎观点

阵营划分:
  多方阵营 (sig_dir=+1)  vs  空方阵营 (sig_dir=-1)

互动生成:
  同阵营之间 → signal_resonance (基本面共识/抱团)  幅度基准 10
  跨阵营之间 → signal_conflict  (基本面维度分歧)    幅度基准 14
  知行背离放大: score_dir ≠ sig_dir 的 agent 是"矛盾体"，其参与的互动幅度 ×1.3
                (内部矛盾 → 对外冲突更剧烈)
```

### 3.4 幅度公式

```
mag = base × (0.4 + 0.6 × avg(双方主导信号强度)) × 知行背离放大
clamp: [3, 40]
```

- 强信号（去库 strength=1.0）：冲突 ≈ 14、共振 ≈ 10
- 弱信号（strength=0.2）：冲突 ≈ 8、共振 ≈ 6

## 4. 存储与接口

### 4.1 新表 market_signals

```
(date, signal, value, change, strength, direction, detail)
signal ∈ {premium, inventory, output, monthly_output, price_momentum, composite}
```

### 4.2 agent_interactions 新增类型

- `signal_resonance`：蓝色 #58a6ff（基本面共识）
- `signal_conflict`：橙色 #d29922（基本面背离）
- 与原有 `info_conflict`（红 #f85149）、`behavior_follow`（绿 #3fb950）并列

### 4.3 API

- `GET /api/market-signals?days=N`：信号时序（series 数组，每个元素含各信号对象）
- `GET /api/agent-interactions?days=N`：新增 `signal_types` 字段（resonance/conflict 计数）

## 5. 部署位置

| 文件 | 位置 | 说明 |
|---|---|---|
| signal_interactions.py | ~/.hermes/scripts/lithium_agents/ | 核心模块（信号提取+阵营+互动） |
| agent_daily_update.py | 同上 | Step 3.6 调用，18:30 cron（周一至五） |
| lithium_api.py | /home/ubuntu/lithium_calendar/ | /market-signals 端点 |
| battlefield_agents.html | static/ | 信号驱动互动区块（卡片+2图） |

## 6. 验证结果（2026-07 回填）

- 回填 23 个交易日，生成 225 条信号互动
- 7/1 冲突 4 条 vs 7/30-31 冲突 12 条/日 → **冲突强度跟随"价格与基本面背离"程度**（7 月价格回落但去库加速）
- 7/31 阵营：多方=机构/政策/矿商（看库存去库+减产），空方=套利/贸易/散户/冶炼（看贴水+价格大跌）

## 7. 参数调优建议（Phase 6 候选）

- `MAGNITUDE_THRESHOLD`（现 3.0）：调高可过滤弱互动
- 幅度缩放系数（共振 10 / 冲突 14）：冲突比例过高可下调
- 知行背离放大 1.3：该参数控制"评分 vs 信号"矛盾的权重
- 敏感度矩阵：可人工微调（如 retail 是否过度敏感价格动量）
