# 碳酸锂多主体博弈系统 — 交易者使用说明书（草稿·第一至三章）

> 适用范围：本说明书与实现代码逐条对应，代码基准为
> `agent_daily_update.py`（Agent 打分逻辑 + 7 维逻辑评分）与
> `game_factors.py`（博弈因子计算）。文中所有变量名、阈值、公式、注释均照实引用源码，
> 未包含代码中不存在的机制。
>
> 数据源（全部来自本地 SQLite `lithium.db`，`LITHIUM_DB` 环境变量可覆盖，默认 `/home/ubuntu/lithium_calendar/lithium.db`）：
> - `prices` 表：LC0 主力日K（close/volume/position/settle）
> - `contract_prices` 表：各合约收盘价（LC0、LC2608、LC2609、LC2610、LC2611、LC2612、LC2701）
> - `spreads` 表：月差结构（near_contract / far_contract / spread）
> - `inventory_history` 表：库存数据（inventory / change）
> - `notes` 表：小作文情绪（sentiment 取值为"利多"/"利空"）
> - `fundamental_indices` 表：基本面六维滚动 250 日 z-score，方向已归一化（正值=利多，负值=利空；Phase 6, 2026-08-01 引入）

---

## 第一章 7 个主体解读

### 0. 公共机制：每个 Agent 的一天怎么运转

每日 18:30（cron 定时）运行 `run_daily_update()`，对 7 个 agent 依次执行：

1. **读取市场数据**：最新价、日涨跌、5 日涨跌、合约价、月差、库存、小作文、基本面 z-score。
2. **规则引擎打分**：`compute_agent_score(agent_id, market, logic_scores)` 返回基础分 `new_score` 与原因文本。
3. **惯性钳制**：分数相对昨日最多跳变 ±20（`score_diff > 20 → new_score = old_score + 20`，反之亦然），防止评分大幅跳变。
4. **仓位换算**：`pos_change = int(new_score / 100 * max_pos * 0.3)`，即评分每 +100 分，对应 `max_position`（来自 `risk_params.max_position`，缺省 100）的 30% 仓位变动；新仓位 `new_pos = clamp(old_pos + pos_change, -max_pos, max_pos)`。
5. **止损止盈**（`risk_params` 缺省：`stop_loss_pct = 0.08`，`take_profit_pct = 0.15`）：
   - 未实现盈亏：多头 `pnl_unreal = (close - avg_cost) * pos / avg_cost`；空头 `pnl_unreal = (avg_cost - close) * abs(pos) / avg_cost`。
   - 亏损超过 8% → **全部平仓**；盈利超过 15% → **减仓一半**（`pos_change = -int(old_pos * 0.5)`）。
6. **博弈因子修正**（详见第二章）：覆盖 `view_score` 为 `corrected_score`。
7. **落库**：`agents` / `agent_history` / `agent_action_log` / `agent_view_log` / `agent_game_factors` / `logic_scores` / `agent_interactions` / `agent_events`。
8. **标记待审核**：评分变化 > 20 或触发止损止盈，或博弈层出现极化/修正过大时，`needs_review = 1`。

**持仓方向**：每个 agent 的 `position` 字段为正=多头、负=空头，方向由历史评分累积决定——持续正分累积多头仓位，持续负分累积空头仓位。**资金量**：来自 `risk_params.capital`，缺省 5000，博弈因子用它做资本加权。

---

### 1. smelter — 冶炼厂

- **代表什么**：碳酸锂冶炼/加工企业，供给侧实体。资金量取 `risk_params.capital`（缺省 5000）。持仓方向由其评分驱动，长期偏多（见下）。
- **交易逻辑**（代码注释：利润改善→增产信号→看空；亏损→减产预期→看多）：
  - **做多（看多）信号**：
    - 价格下跌 + 减仓（`daily_chg < 0 and oi_trend < 0`）→ `score += 25`，减仓下跌，看空情绪蔓延，冶炼厂减产意愿强。
    - 去库 + 上涨（`inv_trend < -2 and daily_chg > 0`）→ `score += 20`，基本面改善。
    - 窄幅震荡（`abs(daily_chg) < 1`）→ `score += 5`，供给刚性强。
  - **做空（看空）信号**：
    - 价格上涨 + 增仓（`daily_chg > 0 and oi_trend > 0`）→ `score -= 30`，量价齐升多头强势 → 冶炼厂增产意愿强 → 看空。
    - 累库 + 下跌（`inv_trend > 2 and daily_chg < 0`）→ `score -= 20`，基本面承压。
  - **加减仓**：分数经公共公式换算仓位（±30% 上限/次）。
- **博弈矩阵特征**：`AGENT_CLAMP` 区间 `(-60, 80)`——注释明确"冶炼厂长期偏多头（供给端）"。信息敏感度 `info_sensitivity = 0.3`、行为敏感度 `beh_sensitivity = 0.2`，均偏低，属于"信息钝感、不轻易跟风"的实体派。基本面加权 `FUNDAMENTAL_WEIGHTS = {"supply": -0.3, "profit": 0.4, "inventory": -0.2}`（供给偏紧→加分，利润高→减分，库存高→减分）。
- **最相关信号**：量价关系（日涨跌 + 持仓量变化 `oi_trend`）、库存趋势 `inv_trend`、供给与利润基本面 z-score；贡献逻辑维度 `supply_risk`、`cost_support`。

---

### 2. resource — 矿商

- **代表什么**：上游矿山/资源方，成本与供给的定价锚。资金缺省 5000。持仓方向由评分驱动，长期偏多。
- **交易逻辑**（代码注释：成本支撑 + 产能利用；碳酸锂成本线约在 120000–130000 区间，代码取 `cost_line = 125000`）：
  - **做多（看多）信号**（价格接近成本线 → 减产预期）：
    - `dist_from_cost = (close_price / cost_line - 1) * 100`；`dist_from_cost < 5` → `score += 30`（价格贴近成本线）；`dist_from_cost < 10` → `score += 10`（成本线附近有支撑）。
    - 去库（`inv_trend < -3`）→ `score += 10`，矿山挺价。
  - **做空（看空）信号**：
    - `dist_from_cost > 20` → `score -= 20`，价格远超成本线，矿山利润丰厚。
    - 累库（`inv_trend > 3`）→ `score -= 10`，累库压力矿山出货。
  - **加减仓**：同上公共公式。
- **博弈矩阵特征**：`AGENT_CLAMP = (-50, 70)`——"矿商长期偏多（成本支撑）"。信息/行为敏感度均 0.3 / 0.2（与 smelter 同为最"钝"的一档）。基本面加权与 smelter 相同 `{"supply": -0.3, "profit": 0.4, "inventory": -0.2}`。
- **最相关信号**：价格相对成本线距离（核心）、库存趋势、供给/利润基本面；贡献逻辑维度 `supply_risk`、`cost_support`。

---

### 3. merchant — 贸易商

- **代表什么**：中间贸易商，赚取基差/月差与价差，库存缓冲垫。资金缺省 5000。
- **交易逻辑**（代码注释：基差/月差结构 → 采购/抛售决策；向后市场 contango → 持有现货有利 → 做多；向前市场 backwardation → 现货溢价高 → 抛售）：
  - **做多（持有现货）**：
    - `spread_2609_2610 < -500` → `score += 25`（代码注释：远月贴水，现货偏强，持有现货）。
    - 小作文利多（`news_sentiment > 0.3`）→ `score += 15`。
    - 大跌后（`daily_chg < -2`）→ `score += 10`，逢低补货。
  - **做空（抛售）**：
    - `spread_2609_2610 > 300` → `score -= 25`（远月升水，持货成本高 → 抛售）。
    - 小作文利空（`news_sentiment < -0.3`）→ `score -= 15`。
    - 大涨后（`daily_chg > 2`）→ `score -= 10`，获利了结。
  - **加减仓**：同上公共公式。
- **博弈矩阵特征**：`AGENT_CLAMP = (-50, 60)`。信息/行为敏感度均为 0.5，中档——贸易商既看信息又跟行为。基本面加权 `{"basis": 0.3, "inventory": 0.2, "profit": 0.2}`，对基差最敏感。
- **最相关信号**：月差结构（2609-2610）、小作文情绪、日内涨跌（逆势操作：大涨了结、大跌补货）；贡献逻辑维度 `demand_drop`、`inventory`、`basis_trade`。

---

### 4. institution — 纯投机构

- **代表什么**：趋势跟随型投资机构，动量策略，资金量大（`capital` 字段决定其在博弈修正中的权重）。持仓方向跟随趋势。
- **交易逻辑**（代码注释：动量 + 量仓 + 趋势跟随）：
  - **追多**：`daily_chg > 1.5 and oi_trend > 0` → `score += 40`（量价齐升，趋势多头）。
  - **追空**：`daily_chg < -1.5 and oi_trend > 0` → `score -= 40`（放量下跌，空头主导）。
  - **谨慎偏多**：
    - `daily_chg > 1 and oi_trend < 0` → `score += 5`（增涨减仓，空头回补，趋势不确定）。
    - `daily_chg < -1 and oi_trend < 0` → `score += 10`（减仓下跌，多头止损，可能接近底部）。
  - **5 日动量**：`five_day_chg > 5` → `score += 15`；`five_day_chg < -5` → `score -= 15`。
  - **加减仓**：同上公共公式。
- **博弈矩阵特征**：`AGENT_CLAMP = (-70, 70)`，双向幅度最大，追涨杀跌最狠。信息敏感度 0.7、行为敏感度 0.6（仅次于散户）——机构虽带头交易，但也容易被对手信息与同向行为放大。基本面加权仅 `{"composite": 0.5}`，只看综合景气。
- **最相关信号**：量价齐升/放量下跌、5 日动量、综合基本面 z-score；贡献逻辑维度 `demand_drop`。

---

### 5. arbitrage — 套利商

- **代表什么**：月差/跨期套利者，赚取价差回归的钱，评分天然中性。资金缺省 5000。
- **交易逻辑**（代码注释：月差结构均值回归）：
  - `spread_2609_2610 < -800` → `score += 10`（远月深度贴水，正向套利空间，买远卖近）。
  - `spread_2609_2610 > 500` → `score -= 10`（远月大幅升水，反向套利空间）。
  - `abs(daily_chg) > 3` → `score += 5`（单日大幅波动，波动率放大，套利窗口打开）。
  - **加减仓**：同上公共公式，但幅度天然小（评分区间窄）。
- **博弈矩阵特征**：`AGENT_CLAMP = (-20, 20)`——注释"套利商中性倾向"，评分永远在小范围震荡，是市场稳定器。信息敏感度 0.6、行为敏感度 0.4。基本面加权 `{"basis": 0.4, "profit": 0.2}`。
- **最相关信号**：月差结构（2609-2610 深度贴水/升水）、波动率、基差与利润基本面；贡献逻辑维度 `inventory`、`basis_trade`。

---

### 6. retail — 散户

- **代表什么**：散户群体，追涨杀跌、情绪驱动，全市场最容易被带节奏的主体。资金缺省 5000。
- **交易逻辑**：
  - **追多**：`daily_chg > 2` → `score += 25`；小作文利多 `news_sentiment > 0.4` → `score += 15`；放量上涨（`volume_today > 250000 and daily_chg > 0`）→ `score += 10`（散户活跃）。
  - **追空**：`daily_chg < -2` → `score -= 25`；小作文利空 `news_sentiment < -0.4` → `score -= 15`；放量下跌（`volume_today > 250000 and daily_chg < 0`）→ `score -= 10`（恐慌蔓延）。
  - **加减仓**：同上公共公式。
- **博弈矩阵特征**：`AGENT_CLAMP = (-60, 60)`。**信息敏感度 0.9、行为敏感度 0.9，均为全场最高**——散户最容易被"别人怎么看"和"别人在做什么"影响，是博弈放大器的核心燃料。基本面加权 `{"sentiment": 0.4, "composite": 0.2}`。
- **最相关信号**：日内大涨大跌（阈值 ±2%）、小作文情绪（阈值 ±0.4，比贸易商更挑剔）、成交量阈值 25 万手、情绪/综合基本面；贡献逻辑维度 `sentiment`（唯一贡献者）。

---

### 7. policy — 政策博弈者

- **代表什么**：政策与宏观事件博弈者，盯政策词、管价格区间。资金缺省 5000。
- **交易逻辑**（基于 `notes` 表小作文中的政策关键词统计）：
  - 关键词表 `policy_words = ["两会", "发改委", "工信部", "新能源", "储能", "监管", "限价", "收储", "抛储", "出口", "碳中和", "双碳"]`，对每条 note 的 `content + title`（小写化）做包含匹配，统计 `policy_hits` 与 `policy_sentiment`（利多 +1 / 利空 -1）。
  - `policy_hits > 0` 时：`policy_sentiment > 0` → `score += 15`（政策面偏多）；`policy_sentiment < 0` → `score -= 15`（政策面偏空）；否则 `score += 5`（政策面中性）。
  - 价格干预带：`close_price > 160000` → `score -= 20`（价格偏高，政策干预风险）；`close_price < 130000` → `score += 10`（价格偏低，政策托底预期）。
  - **加减仓**：同上公共公式。
- **博弈矩阵特征**：`AGENT_CLAMP = (-60, 60)`。**信息敏感度 0.2、行为敏感度 0.1，全场最低**——政策主体几乎不受市场信息与跟风影响，是最独立的一个。**不参与基本面六维加权**（代码注释：policy 保持原逻辑，不参与基本面加权）。
- **最相关信号**：政策关键词命中次数与方向、绝对价格区间（13 万/16 万两个锚点）；贡献逻辑维度 `macro_policy`（唯一贡献者）。

---

## 第二章 博弈因子详解

博弈因子模块 `game_factors.py`（v1.0）在规则引擎基础分之上做三层次 Agent 间影响修正。模块注释明确：**修正幅度控制在 ±10 以内，保持 base_score 的主导地位**。

整体流程（`apply_game_factors` 入参）：`base_scores_dict`（规则引擎基础分）、`deltas_dict`（仓位变动）、`texts_dict`（原因文本）、`agents_dict`（含 risk_params 元数据）、`capital_dict`（资金量，缺省 5000）、`market`、`today`、`conn`、`agent_ids`。

**基础统计量**：
- `n_bull = sum(1 for s in base_scores_dict.values() if s > 0)` — 看多 agent 数
- `n_bear = sum(1 for s in base_scores_dict.values() if s < 0)` — 看空 agent 数
- `total_cap = max(sum(capital_dict.values()), 1)` — 总资金（防除零）

### 2.1 base_score — 基础评分

- **含义**：规则引擎 `compute_agent_score` 输出的原始分数（已含惯性 ±20 钳制、各 agent 自身 clamp 区间、基本面六维加权），代表该主体"自己看到的市场"得出的多空倾向，-100 ~ +100。
- **谁产生**：第一章各 agent 的规则逻辑；正分=看多，负分=看空，0=中性/无信号。
- **作用**：是博弈修正的锚点——修正只在其上做小幅调整，并最终以 `base_score` 原值存入 `agent_game_factors.base_score`，同时 `agent_history` 保存修正前分数。

### 2.2 info_transfer — 信息传导（Layer 1）

- **机制**：主体间"观点方向分歧"带来的不确定性修正。当我的方向与对手方向相反时，我的信心被削弱——因为市场上存在与我方向相反、且有资金实力的力量。
- **计算**（对每个 agent `aid`）：
  1. 自身 `own_score == 0` → 修正为 0，跳过。
  2. 汇总所有**方向与自己相反**的对手资本：`opposing_cap += capital_dict[other]`（条件：`(own_score>0 and other_score<0) or (own_score<0 and other_score>0)`）。
  3. `ratio = opposing_cap / total_cap`。
  4. `info_factors[aid] = round(-ratio * info_sensitivities[aid] * 8, 2)`。
- **方向**：恒为负值（或 0）——"与自身方向相反 → 修正 toward zero（减少绝对值）"，即对手资金占比越大、自身敏感度越高，评分越被拉向 0。
- **敏感度表** `info_sensitivities`：`smelter 0.3, resource 0.3, merchant 0.5, institution 0.7, arbitrage 0.6, retail 0.9, policy 0.2`。散户 0.9 最容易被"分歧信息"动摇；政策 0.2 几乎无视。
- **交易含义**：多空对峙且对手资金雄厚时，所有主体评分向中性收敛——市场进入"信息博弈期"，趋势信号变弱。

### 2.3 behavioral_impact — 行为冲击（Layer 2）

- **机制**：主体间"同向仓位变动"的跟风增强。别人在往我的方向加仓，说明我的判断得到验证，评分被轻微增强。
- **计算**（对每个 agent `aid`）：
  1. 自身 `own_delta == 0` → 修正为 0，跳过。
  2. 遍历其他 agent，若**仓位变动方向与自身相同**（同多或同空）：
     - `max_pos = json.loads(risk_params)["max_position"]`（解析失败取默认 100）；
     - `momentum = abs(other_delta) / max_pos`（对方仓位变动的"力度"，0~1）；
     - `influence += (capital_dict[other] / total_cap) * momentum`（资金占比 × 力度）。
  3. `beh_factors[aid] = round(influence * beh_sensitivities[aid] * 5, 2)`。
- **方向**：恒为正值（或 0）——同向行为只会增强，不会削弱。
- **敏感度表** `beh_sensitivities`：`smelter 0.2, resource 0.2, merchant 0.5, institution 0.6, arbitrage 0.4, retail 0.9, policy 0.1`。散户 0.9 最跟风；政策 0.1 基本不跟。
- **交易含义**：当大资金主体（capital 占比高）带头加仓、且散户在场时，跟风效应会自我强化——这就是趋势末段"情绪加速"的来源；反过来，大资金减仓时跟风盘放大下跌。

### 2.4 total_correction — 总修正

- **计算**：`total_f = round(info_f + beh_f, 2)`，即信息传导（通常为负）与行为冲击（通常为正）的净值。
- **含义**：博弈层面对该主体评分的净修正幅度。模块注释承诺"修正幅度控制在 ±10 以内"——若某主体信息分歧压制（负）大于同向强化（正），总修正为负，评分被压低；反之被抬高。
- **落库**：`agent_game_factors.total_correction` 字段；`agent_view_log.daily_reasoning` 的 JSON 中亦含 `info_transfer` / `behavioral_impact` / `corrected_score`。

### 2.5 corrected_score — 修正后评分

- **计算**：`corrected = _clamp(base + total_f, -100, 100)`。
- **含义**：博弈修正后的最终观点分，**覆盖写回 `agents.view_score`**（`UPDATE agents SET view_score=?, needs_review=? ...`），即页面/下游看到的主体评分就是它。
- **触发审核**：`needs_review = polarization_event is not None or abs(corrected - base) > 10`——极化发生，或博弈修正使分数偏移超过 10 分，即标记待人工审核。
- **原文保留**：`base_score` 与 `base_text` 以 JSON 形式写入 `agent_view_log.daily_reasoning`，供事后比对修正前后差异。

### 2.6 极化博弈检测（Layer 3）

- **目的**：多空极度分歧时的反转信号（代码注释：多空极度分歧时的反转信号）。
- **触发条件**（同时满足）：
  1. `n_bull > 0 and n_bear > 0` — 多空双方都存在；
  2. `ratio = max(n_bull, n_bear) / min(n_bull, n_bear)`，**`ratio > 3`** — 多空人数比超过 3:1；
  3. `avg_abs = sum(abs(s)) / total_agents`，**`avg_abs > 30`** — 全体主体平均分歧强度超过 30 分。
- **反转因子**：触发后，对每个 agent：`s > 0 → info_factors[aid] -= 5`（看多的减分），`s < 0 → info_factors[aid] += 5`（看空的加分）——即**多数派被压制、少数派被抬升**，通过 Layer 1 通道注入反转信号。
- **事件记录**：`polarization_event = {"bull": n_bull, "bear": n_bear, "ratio": round(ratio,2), "avg_score": round(avg_abs,1)}`；写入 `factors_detail.polarization = True`，并强制 `needs_review = 1`，打印 `[⚠️ 极化博弈检测] 多空比 n_bull:n_bear (ratio x)`，建议人工审核。
- **交易含义**：当市场一边倒（如 6:1 且平均观点强度 > 30）时，系统提示"拥挤交易"风险，开始逆向修正——这是博弈层面的顶部/底部预警信号。

### 2.7 互动关系落库

`agent_interactions` 表记录主体间定向影响：
- `info_conflict`：方向冲突，`magnitude = round(opp_cap / total_cap * 100, 1)`（对手资金占比%）。
- `behavior_follow`：同向跟随，`magnitude = round(influence * 10, 1)`（Layer 2 influence 放大 10 倍）。
- 每条记录 `(date, from_agent, to_agent, influence_type, magnitude)`，主键 `(date, from_agent, to_agent, influence_type)`。

---

## 第三章 7 维逻辑评分

7 维逻辑评分在 `compute_agent_score` 内由 `_compute_logic_scores` 计算，维度名与中文名定义于 `LOGIC_DIMENSIONS`：

```python
LOGIC_DIMENSIONS = {
    "supply_risk": "供给风险", "demand_drop": "需求下滑", "inventory": "库存变化",
    "macro_policy": "宏观政策", "cost_support": "成本支撑", "sentiment": "情绪/小作文",
    "basis_trade": "基差月差",
}
```

**参与映射** `AGENT_LOGIC_MAP`（谁贡献哪个维度）：

```python
AGENT_LOGIC_MAP = {
    "supply_risk": ["smelter", "resource"],
    "demand_drop": ["merchant", "institution"],
    "inventory": ["merchant", "arbitrage"],
    "macro_policy": ["policy"],
    "cost_support": ["smelter", "resource"],
    "sentiment": ["retail"],
    "basis_trade": ["arbitrage", "merchant"],
}
```

**汇总方式**：每个 agent 只对其所属维度贡献 `logic_score`，累加进 `logic_scores` 字典（`logic_scores[name] = logic_scores.get(name, 0) + logic_score`）；Step 3.5 再按维度求当日 **avg / min / max / agent_count** 写入 `logic_scores` 表。因此读 DB 时看 `avg_score`（该维度当日平均强度）。

**共用的市场输入**（代码定义）：
- `daily_chg`（日涨跌%）、`five_day_chg`（5 日涨跌%）、`oi_trend`（持仓量变化%，`(oi_today / oi_prev - 1) * 100`，prev 取昨日，缺省前日）；
- `inv_trend`（库存趋势%，近 5 日数据：`(latest_inv / avg(inventory[3:5]) - 1) * 100`，数据不足 5 条时退化为首两日环比）；
- `news_sentiment = (利多条数 - 利空条数) / max(总条数, 1)`（-1 ~ +1）；
- `spread_2609_2610`（LC2609−LC2610 月差）、`spread_2611_2612`、`spread_2608_2609`；
- `cost_line = 125000`、`dist_from_cost = (close / cost_line - 1) * 100`；
- `fund`：`fundamental_indices` 最新一行的六维 z-score（supply/demand/inventory/profit/sentiment/basis/composite），方向已归一化（z>0=利多）。

---

### 3.1 supply_risk — 供给风险（smelter + resource）

- **数据**：日涨跌、持仓量变化、库存趋势、供给与利润 z-score。
- **计算**（参与主体叠加）：
  ```python
  if daily_chg > 1 and oi_trend > 0:   logic_score += 40   # 价涨+增仓 → 增产 → 风险高
  elif daily_chg > 0:                  logic_score += 15
  if inv_trend < -2:                   logic_score -= 15   # 去库 → 供给风险低
  if fund: logic_score += (-fund["supply"] + fund["profit"]) * 10
  ```
  基本面逻辑（代码注释）：供给偏紧（supply z>0）→ 供给风险低（减分）；利润高（profit z>0）→ 增产预期 → 风险高（加分）。
- **正/负含义**：正值=供给风险高（价格上涨+增仓，冶炼厂有增产动力，未来供给过剩风险）；负值=供给风险低（去库/供给偏紧）。
- **价格关系**：价格涨+增仓越猛，供给风险分越高——通常出现在上涨中后段，是"涨出来的供给"预警；价格跌+去库则压低该分。
- **典型场景**：碳酸锂连续放量上涨、持仓创新高时，`supply_risk` 抬升至高位，提示高位增产风险。

### 3.2 demand_drop — 需求下滑（merchant + institution）

- **数据**：日涨跌、持仓量变化、小作文情绪、需求与情绪 z-score。
- **计算**：
  ```python
  if daily_chg < -1 and oi_trend < 0:  logic_score = 40    # 跌+减仓 → 采购弱
  elif daily_chg < 0:                  logic_score = 15
  if news_sentiment < -0.3:            logic_score += 15
  if fund: logic_score += -fund["demand"] * 10 - fund["sentiment"] * 5
  ```
  基本面逻辑：需求/情绪好（z>0）→ 需求风险低（减分）。
- **正/负含义**：正值=需求下滑风险高（跌+减仓，下游采购意愿弱）；负值=需求风险低（需求基本面健康）。
- **价格关系**：下跌且伴随减仓时该分走高——"多头出逃型下跌"被识别为需求端问题；若跌但增仓（空头进攻），该维度不触发高分。
- **典型场景**：主力合约阴跌数日、持仓持续下降、负面小作文密集时，`demand_drop` 冲高。

### 3.3 inventory — 库存变化（merchant + arbitrage）

- **数据**：库存趋势 `inv_trend`、库存 z-score。
- **计算**：
  ```python
  if inv_trend > 2:  logic_score = 40    # 累库 → 风险高
  elif inv_trend > 0: logic_score = 15
  if fund: logic_score -= fund["inventory"] * 10   # z>0=库存偏低 → 累库风险低
  ```
- **正/负含义**：正值=累库风险高（库存环比加速累积，基本面承压）；负值=库存去化/库存偏低，利多。
- **价格关系**：库存趋势是中期基本面同步指标——累库通常压制价格，去库支撑价格；与 smelter 的"累库+跌→看空"互为印证。
- **典型场景**：交易所周度库存连续累增、仓单增加时，`inventory` 分走高，提示基本面转弱。

### 3.4 macro_policy — 宏观政策（policy，唯一贡献者）

- **数据**：`notes` 表小作文标题+内容中的政策关键词、绝对价格。
- **计算**：
  ```python
  policy_hits: 命中 policy_words 的次数；policy_pos: 利多+1/利空-1 累计
  logic_score = int(policy_pos * 10 + policy_hits * 5)
  if close_price > 160000: logic_score -= 20     # 政策干预风险
  elif close_price < 130000: logic_score += 10   # 政策托底预期
  ```
- **正/负含义**：正值=政策面偏多（利多政策词多，或价格低于 13 万触发托底预期）；负值=政策面偏空/干预风险（价格高于 16 万）。
- **价格关系**：该维度是"政策顶/政策底"的温度计——16 万以上越涨干预风险越大，13 万以下越跌托底预期越强。
- **典型场景**：市场热议"收储/抛储"时命中关键词计数上升；价格突破 16 万后该维度转负，提示政策降温风险。

### 3.5 cost_support — 成本支撑（smelter + resource）

- **数据**：价格相对成本线距离 `dist_from_cost`、利润 z-score。
- **计算**：
  ```python
  if -5 < dist_from_cost < 5:   logic_score = 40   # 贴近成本线 → 支撑强
  elif -10 < dist_from_cost < 10: logic_score = 20
  elif dist_from_cost > 20:     logic_score = -20  # 远离成本 → 支撑弱
  if fund: logic_score -= fund["profit"] * 10      # 利润高 → 离成本远 → 支撑弱
  ```
- **正/负含义**：正值=成本支撑强（价格在成本线 ±5% 内，减产预期提供底部）；负值=成本支撑弱（价格高于成本线 20% 以上，下方无支撑）。
- **价格关系**：价格跌向 12.5 万成本线时该分升高，是"跌不动了"的量化表达；价格远离成本线时支撑逻辑失效。
- **典型场景**：价格从 13 万跌至 12.6 万附近，`cost_support` 快速抬升至 40，提示接近减产底。

### 3.6 sentiment — 情绪/小作文（retail，唯一贡献者）

- **数据**：小作文情绪净占比 `news_sentiment`、成交量、情绪 z-score。
- **计算**：
  ```python
  logic_score = int(news_sentiment * 50)
  if volume_today > 250000 and daily_chg > 0: logic_score += 10   # 放量上涨散户活跃
  elif volume_today > 250000 and daily_chg < 0: logic_score -= 10 # 放量下跌恐慌
  if fund: logic_score += int(fund["sentiment"] * 10)
  ```
- **正/负含义**：正值=市场情绪偏多（利多小作文占优、放量上涨）；负值=情绪偏空（利空小作文、放量下跌恐慌）。
- **价格关系**：情绪是短期波动的加速器——`news_sentiment` 每 +1 贡献 50 分，放量则 ±10；情绪分极端化往往对应短期情绪顶/底（与第二章极化检测互相印证）。
- **典型场景**：网络"小作文"利多刷屏且当日放量上涨时，`sentiment` 冲高，但需警惕情绪拥挤反转。

### 3.7 basis_trade — 基差月差（arbitrage + merchant）

- **数据**：月差 `spread_2609_2610`、`spread_2611_2612`、基差 z-score。
- **计算**：
  ```python
  if spread_2609_2610 < -500:  logic_score = 20   # 代码注释：远月贴水 → contango → 持有现货有利
  elif spread_2609_2610 > 300: logic_score = -20  # 远月升水 → 持有成本高
  if spread_2611_2612 < -300:  logic_score += 10
  if fund: logic_score += int(fund["basis"] * 10) # 基差强(近强远弱) → 套利风险上升
  ```
- **正/负含义**：正值=近月相对强势/持有现货有利（代码注释口径），套利风险上升；负值=远月升水、持货成本高。
- **价格关系**：月差结构反映现货紧松——近强远弱（负月差扩大）通常伴随现货紧张、近月价格强势；远月升水扩大则反映供应宽松预期。
- **典型场景**：临近交割月现货紧缺、2609 对 2610 贴水超 500 点时，`basis_trade` 走高，提示近月强于远月的结构性机会。

---

## 附：执行流程与输出产物（速查）

`run_daily_update()` 步骤：
1. 读取市场数据（价格/合约价/月差/库存/小作文/基本面 z-score）；
2. 逐个 agent 运行规则引擎（含惯性 ±20、止损 8% 全平、止盈 15% 减半）；
3. 博弈因子 Layer 1/2/3 修正并落库；
4. 写入 `logic_scores` 汇总（avg/min/max/agent_count）；
5. 信号驱动互动 `apply_signal_interactions`（升贴水/库存/产量 → agent 阵营互动，`signal_interactions.py`）；
6. 异常事件检测：`abs(daily_chg) > 3` → 写入 `agent_events`（severity=3 若 >5%，否则 2；event_type="大波动"）；
7. commit 并输出摘要。

**交易者读哪些表**：
- `agents`：最新 `view_score`（=博弈修正后 corrected_score）、`position`（多空仓位）、`pnl_unreal`、`needs_review`；
- `agent_history`：每日原始 `view_score`（修正前）与仓位；
- `agent_game_factors`：`base_score / info_transfer / behavioral_impact / total_correction / corrected_score / factors_detail(polarization, n_bull, n_bear)`；
- `logic_scores`：7 维逻辑评分的日均/极值；
- `agent_interactions`：`info_conflict` 与 `behavior_follow` 两类主体间影响。

---

## 第四章 信号驱动互动（Signal-Driven Interactions）

> 实现文件：`signal_interactions.py`（2026-08-01 部署）
> 核心思想：**市场信号 → Agent 信号立场 → 阵营划分 → 互动生成**。让"分歧"不再只是分数差，而是不同 agent 对真实基本面信号的不同解读。

### 4.1 五类信号的定义与数据来源

| 信号 | 表/列 | 频率 | 方向逻辑 |
|---|---|---|---|
| premium 升贴水 | `lithium_daily_prices` col2低幅/col3高幅取中值 | 日度 | 中值+20日变化，正=利多（各占60%/40%） |
| inventory 库存 | `lithium_weekly` col2大样本库存总计 | 周度 | 去库=利多（取反） |
| output 产量 | `lithium_weekly` col12产量总计 | 周度 | 减产=利多（取反） |
| monthly_output 月度产量 | `lithium_monthly` col1 | 月度 | 减产=利多 |
| price_momentum 价格动量 | `prices` 表5日涨跌幅 | 日度 | 5日涨=利多 |

每个信号 → `(direction, strength)`：direction ∈ {-1,0,+1}，strength ∈ [0,1]。

### 4.2 主体信号敏感度（谁看什么）

- **smelter 冶炼厂**：升贴水0.7、库存0.6 → 盯升贴水
- **merchant 贸易商**：升贴水1.0、库存0.8 → 升贴水（产量偏好为负：产量多=货源足，利空）
- **institution 机构**：库存0.9、需求0.8 → 库存
- **arbitrage 套利商**：升贴水1.0 → 升贴水
- **retail 散户**：价格动量1.0 → 只盯价格
- **resource 矿商**：产量0.9 → 产量
- **policy 政策**：库存0.3 → 库存

### 4.3 四种互动类型

| 类型 | 触发 | 含义 |
|---|---|---|
| signal_resonance 信号共振 | 同阵营（对同一信号同向解读） | 基本面共识/抱团，幅度基准10 |
| signal_conflict 信号冲突 | 跨阵营（对同一信号相反解读） | 基本面维度分歧，幅度基准14 |
| behavior_follow 行为跟随 | 一个 agent 仓位变动被另一个跟随 | 羊群效应 |
| info_conflict 信息冲突 | 两个 agent 传递相反信息 | 观点对立 |

**知行背离放大**：`score_dir ≠ sig_dir` 的 agent 是"矛盾体"（心里想做多但信号说空，或反之），其参与互动幅度 ×1.3——内部矛盾 → 对外冲突更剧烈。

幅度公式：`mag = base × (0.4 + 0.6 × avg(双方主导信号强度)) × 知行背离放大`

### 4.4 读法

- 共振条数多 → 市场共识强、趋势可能持续；
- 冲突条数多 → 多空分歧大、价格震荡；
- 某个 agent 反复出现在冲突里 → 它最"纠结"，其立场变化往往是转折信号。

---

## 第五章 综合解读实战（以 2026-07-31 为例）

> 数据来自当日实际运行结果。

### Step 1：看 7 个 agent 最新状态 → 判断多空阵营分布

| Agent | 状态 | score | 心理锚点 |
|---|---|---|---|
| 冶炼厂 smelter | 降价抛售 | 36 | 144,747 |
| 贸易商 merchant | 被套冻结 | 46 | 149,088 |
| 机构 institution | 强趋势做空 | 51 | 145,008 |
| 套利商 arbitrage | 观望 | 31 | 145,262 |
| 散户 retail | 恐慌追跌 | 36 | 144,665 |
| 矿商 resource | 囤货惜售 | 44 | 142,386 |
| 政策 policy | 稳定观望 | 31 | 144,784 |

**解读**：机构强趋势做空 + 散户恐慌追跌 → 下跌动能仍在；贸易商被套冻结（15万以上拿货的套牢盘）→ 反弹到 149K 附近有解套抛压；矿商囤货惜售 + 冶炼厂降价抛售 → 上游出现分化（矿端挺价 vs 冶炼端被动出货）。整体偏空但已现"空头拥挤"迹象。

### Step 2：看 7 维逻辑评分 → 判断主导逻辑

- 当日信号：升贴水中值 -500（贴水收窄），库存周降 5.1%（强去库=利多），周产降 2.9%（减产=利多），价格 5 日 -6.43%（强利空）。
- 库存利多 vs 价格动量利空 → 基本面（去库/减产）与价格趋势（下跌）背离，这正是机构做空但矿商/冶炼厂惜售的原因。

### Step 3：看博弈因子极化度 → 判断分化程度

- `agent_game_factors.factors_detail` 里看 `polarization`、`n_bull`、`n_bear`；
- 多方阵营: [机构, 矿商, 政策] / 空方阵营: [冶炼厂, 贸易商, 套利商, 散户] → 4:3 偏空，但矿商在多方说明上游不认跌。

### Step 4：看信号互动 → 判断共振/冲突

- 当日生成 21 条信号互动；库存信号（去库）→ 机构/矿商共振做多逻辑，与价格动量（下跌）→ 散户/冶炼厂做空逻辑冲突；
- 冲突>共振 → 短期震荡概率大，方向看谁先"知行合一"。

### Step 5：综合结论

偏空但下方有支撑（去库+减产+矿商惜售），属于"下跌趋势中的基本面对抗"阶段。短线策略：不追空，等心理价位带下沿（矿商 138K / 散户 141K）附近观察企稳信号。

---

## 第六章 基本面综合指数（2026-08-01 新增）

> 实现文件：`fundamental_indices.py`，表 `fundamental_indices`。

### 6.1 六维指数

| 维度 | 含义 | 方向 |
|---|---|---|
| supply | 供应（产量/开工） | 正=利多（供应收紧） |
| demand | 需求（排产/表观消费） | 正=利多 |
| inventory | 库存 | 正=利多（去库） |
| profit | 利润（冶炼/矿端利润） | 正=利多 |
| sentiment | 情绪（小作文/新闻） | 正=利多 |
| basis | 基差/月差 | 正=利多 |

### 6.2 防透视标准化

每个指标用 **滚动250交易日窗口 z-score**（只用当日及之前数据），方向归一化后加权合成。**没有使用未来数据**——每个时点的指数是"当时能看到的信息"。

### 6.3 最新值（2026-07-31）

composite=+0.31（温和偏多）、demand=+2.51（强）、supply=-1.41（宽松）、basis=+0.47、inventory=+0.13、profit=+0.07、sentiment=+0.10。

**读法**：需求强但供应更宽松 → 净中性略偏多；如果 supply 持续走弱（收紧）而 demand 维持高位 → 做多信号增强。

### 6.4 与博弈系统的结合

`agent_daily_update.py` 已把六维指数融入各 agent 的 `compute_agent_score`：冶炼厂/矿商看 supply+profit，贸易商看 basis+inventory+profit，机构看 composite，套利商看 basis+profit，散户看 sentiment+composite。指数缺失时自动回退原逻辑（try/except）。
