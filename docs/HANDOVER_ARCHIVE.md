# 碳酸锂多主体博弈系统 — 完整归档

> 归档日期：2026-09-08
> 目标：上线 GitHub 与团队协作前的全量归档
> 涵盖：架构设计 · 函数假设 · 指标数据 · 缺口清单 · 会话历史脉络

---

## 一、系统总览

### 1.1 一句话定义

**基于规则引擎 + 博弈因子的 7 主体碳酸锂多主体博弈模拟系统**，每日定时运行，输出各主体观点评分、仓位变动、博弈修正、逻辑维度评分和信号互动，供交易决策参考。

### 1.2 网址与访问

| 用途 | 地址 |
|------|------|
| GitHub Pages 线上看板 | https://algo23-yunqingtian.github.io/lithium-dashboard/ |
| 本地 Flask 服务 | http://124.221.113.37:8766/lithium-gh/ |
| 博弈页面（本地） | http://124.221.113.37:8766/battlefield_agents.html |
| GitHub 仓库（静态站） | https://github.com/algo23-yunqingtian/lithium-dashboard |
| 本地仓库（后端） | /home/ubuntu/lithium_calendar/ |
| 静态站仓库 | /home/ubuntu/lithium_gh_static/ |

### 1.3 数据更新节奏

- **后端数据更新**：工作日 18:00 cron 导出 JSON → git push → GitHub Pages 自动部署
- **Agent 每日更新**：工作日 18:30 cron 运行 `agent_daily_update.py`
- **数据库**：SQLite `lithium.db`（35 张表）

---

## 二、文件目录结构

### 2.1 后端代码（/home/ubuntu/lithium_calendar/）

```
lithium_calendar/
├── app.py                          # Flask 主入口（1208 行）
├── lithium_api.py                  # 实时数据 API Blueprint（1148 行）
├── lithium_agents.py               # Agent 框架初始化 + 路由注入
├── lithium_a_share_inventory_api.py  # 锂电A股存货库存 API
├── lithium_dashboard_api.py        # 看板 API
├── lithium_global_api.py           # 全球看板 API
├── lithium_backtest.py             # 防透视回测引擎
├── seat_cross_analysis.py          # 席位交叉分析 API
├── spread_analysis_api.py          # 月差分析 API
├── zinc_chat.py                    # 锌对话助手（访客模式）
├── lithium.db                      # 主数据库（35 表）
├── lc_position.db                  # 持仓/仓单数据库
├── lithium_global.db               # 全球数据数据库
├── static/                         # 前端静态文件
│   ├── battlefield_agents.html     # 多主体博弈页面（核心）
│   ├── lithium_data_dashboard.html # 数据看板
│   ├── lithium_inventory_page.html # 库存页面
│   ├── lithium_time_dashboard.html # 时间看板
│   ├── lithium_global_dashboard.html
│   ├── lithium_aug_sept_review.html
│   ├── zinc_logic_review_latest.html
│   └── echarts.min.js
├── docs/                           # 设计文档（详见 2.3）
├── logs/                           # 运行日志
├── archived/                       # 【新增】归档目录（本文档所在）
├── backfill_2023_2024.py           # 历史数据回填
├── backfill_agent_history.py       # Agent 历史回填
├── backfill_interactions.py        # 互动数据回填
├── backfill_interactions_v2.py     # 互动回填 v2
├── backfill_spot.py                # 【新增】现货数据回填（lc_spot.db 20→732行）
├── cron_minute_collector.py        # 分钟数据定时采集
├── expand_spreads.py               # 月差扩展
├── fetch_financials_v2.py          # 财务数据抓取 v2
├── fetch_lithium_financials.py     # 锂财务数据抓取
├── fetch_smm_news.py               # SMM 新闻抓取
├── gen_data.py / gen_dashboard.py  # 数据生成
├── gen_dashboard_p2/p3/p4.py       # 看板生成（分批）
├── build_dashboard_p1.py           # 看板构建 P1
├── historical_backfill.py          # 历史回填
├── incremental_update.py           # 增量更新
├── lc_spread_review_generator.py   # 月差复盘生成
├── lc_spread_review_scorer.py      # 月差复盘打分
├── rebuild_spreads_all.py          # 月差全量重建
└── lithium_api_v2_20260730.py      # API v2 旧版（历史保留）
```

### 2.2 博弈引擎核心脚本（~/.hermes/scripts/lithium_agents/）

```
lithium_agents/
├── agent_daily_update.py           # 核心引擎（944 行）— Agent 打分 + 7 维逻辑 + 博弈因子调用
├── agent_daily_update.sh           # 定时脚本包装器
├── game_factors.py                 # 博弈因子（228 行）— 三层博弈修正
├── signal_interactions.py          # 信号驱动互动（500+ 行）— 五类信号 → 阵营互动
├── fundamental_indices.py          # 基本面六维指数 — 250 日滚动 z-score
├── agent_psychology_calc.py        # Agent 心理画像计算
├── lc_gap_audit.py                 # 数据缺口审计
├── lithium_excel_import.py         # Excel 数据导入
├── fix_refresh_interval.py         # 刷新间隔修复
└── agent_daily_update.py.backup_20260730_game_factors  # 博弈因子备份
```

### 2.3 设计文档（/home/ubuntu/lithium_calendar/docs/）

| 文件 | 内容 | 行数 |
|------|------|------|
| `lithium_game_factors_design.md` | 博弈因子体系设计 v1.0（三层因子 + 7 维逻辑） | 243 |
| `lithium_game_interaction_design.md` | 博弈互动设计方案（甘特图 + 三层互动模型） | 350 |
| `lithium_game_interaction_implementation.md` | 博弈互动实施清单（A/B/C 任务拆解） | 303 |
| `game_matrix_manual.md` | 交易者使用说明书（六章完整手册，代码级对应） | 493 |
| `task_freeze_8agents_20260801.md` | 8 主体重构封存交接文档 | ~80 |
| `trader_agent_design_20260801.md` | 贸易商 Agent 详细设计（5 层决策） | ~80 |
| `agent_behavior_model_compare_20260801.md` | 现有 7 Agent vs 新 8 主体对比 | ~80 |
| `backtest_no_lookahead_design.md` | 防透视回测体系设计 v0.1 | ~50 |
| `_draft_ch1_3.md` | 交易者说明书草稿（第一至三章） | ~350 |

### 2.4 静态站仓库（/home/ubuntu/lithium_gh_static/）

```
lithium_gh_static/
├── index.html                      # 首页
├── data.json                       # 主数据文件
├── api/                            # API 端点 JSON（16 个文件）
│   ├── battlefield.json            # 博弈状态
│   ├── game_factors.json           # 博弈因子
│   ├── agent_interactions.json     # 主体互动
│   ├── agent_psychology.json       # 心理画像
│   ├── logic_scores.json           # 逻辑评分
│   ├── market_signals.json         # 市场信号
│   ├── prices.json                 # 价格数据
│   ├── term_structure.json         # 期限结构
│   ├── spread_analysis.json        # 月差分析
│   ├── lc_analysis.json            # LC 分析
│   ├── lc_seat_flow.json           # 席位流向
│   ├── seat_cross.json             # 席位交叉
│   ├── periods.json / trading_dates.json / all_dates.json
│   └── status.json
├── pages/                          # 前端页面（16 个）
│   ├── battlefield_agents.html     # 多主体博弈（核心页面）
│   ├── lithium_data_dashboard.html
│   ├── lithium_inventory_page.html + .js
│   ├── lithium_time_dashboard.html + .js
│   ├── lithium_global_dashboard.html
│   ├── lc_position_analysis.html
│   ├── lc_seat_flow.html
│   ├── lc_spread_review_latest.html
│   ├── lc_spreads_analysis.html
│   ├── lithium_spreads_calendar.html
│   ├── spread_analysis.html
│   ├── spread_calendar.html
│   ├── lithium_aug_sept_review.html
│   ├── seat_cross_analysis.html
│   └── index.html
├── legacy/all_in_one.html          # 旧版单文件
├── export_data.py                  # 数据导出脚本
├── snapshot_api.py                 # API 快照脚本
├── push_to_github.sh               # 推送脚本
└── README.md                       # 仓库说明
```

---

## 三、架构设计详解

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端层（GitHub Pages）                      │
│  battlefield_agents.html → 博弈矩阵热力图 + 互动时间线 + 轮次卡片  │
│  lithium_data_dashboard.html → K线 + 基本面 + 库存                │
│  lithium_inventory_page.html → 库存分析                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ JSON API（/api/*）
┌──────────────────────────┴──────────────────────────────────────┐
│                     Flask 后端层（app.py）                         │
│  ├── lithium_api.py        → 实时数据 + 分钟级数据                │
│  ├── lithium_agents.py     → Agent 框架 + 路由注入               │
│  ├── seat_cross_analysis.py → 席位交叉                            │
│  └── lithium_a_share_inventory_api.py → A股存货                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SQLite（lithium.db，35 表）
┌──────────────────────────┴──────────────────────────────────────┐
│                     数据层（SQLite）                               │
│  prices / contract_prices / spreads / inventory_history          │
│  notes / fundamental_indices / agents / agent_history            │
│  agent_game_factors / logic_scores / agent_interactions          │
│  agent_events / agent_psychology / warehouse_receipt             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 每日 18:30 cron
┌──────────────────────────┴──────────────────────────────────────┐
│                     Agent 引擎层                                   │
│  agent_daily_update.py → 规则引擎 + 博弈因子 + 信号互动            │
│  ├── game_factors.py         → 三层博弈修正                       │
│  ├── signal_interactions.py  → 五类信号驱动互动                    │
│  └── fundamental_indices.py  → 六维基本面指数                     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 每日执行流程（run_daily_update）

```
1. 读取市场数据
   ├── prices 表 → 最新收盘价、日涨跌%、5日涨跌%
   ├── contract_prices 表 → 各合约价（LC0/LC2608/LC2609/...）
   ├── spreads 表 → 月差结构（2609-2610, 2611-2612, 2608-2609）
   ├── inventory_history 表 → 库存趋势（近5日环比%）
   ├── notes 表 → 小作文情绪（利多/利空净占比）
   └── fundamental_indices 表 → 六维z-score（supply/demand/inventory/profit/sentiment/basis/composite）

2. 规则引擎打分（每个 agent）
   ├── compute_agent_score() → base_score + reasons_text + delta_pos
   ├── 惯性钳制 ±20/日
   ├── AGENT_CLAMP 各 agent 专属区间
   └── _compute_logic_scores() → 7 维逻辑评分

3. 博弈因子修正（apply_game_factors）
   ├── Layer 1: 信息传递因子（info_transfer）
   ├── Layer 2: 行为影响因子（behavioral_impact）
   └── Layer 3: 极化博弈检测（polarization）
   └── corrected_score = base_score + total_correction（clamp -100~100）

4. 止损止盈检查
   ├── 亏损 > 8% → 全部平仓
   └── 盈利 > 15% → 减仓一半

5. 信号驱动互动（apply_signal_interactions）
   ├── 五类信号：升贴水/库存/产量/月度产量/价格动量
   ├── 四种互动：信号共振/信号冲突/行为跟随/信息冲突
   └── 知行背离放大 ×1.3

6. 异常事件检测
   └── |daily_chg| > 3% → 写入 agent_events

7. 落库（8 张表）
   ├── agents / agent_history / agent_action_log / agent_view_log
   ├── agent_game_factors / logic_scores / agent_interactions / agent_events

8. 标记待审核（needs_review=1）
   └── 评分变化>20 或 触发止损止盈 或 极化 或 修正>10
```

---

## 四、7 个主体详解（函数 + 假设 + 阈值）

### 4.1 smelter — 冶炼厂

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-60, +80) — 长期偏多 |
| info_sensitivity | 0.3 |
| beh_sensitivity | 0.2 |
| FUNDAMENTAL_WEIGHTS | {supply: -0.3, profit: +0.4, inventory: -0.2} |
| 资本 | risk_params.capital（缺省 5000） |

**交易逻辑假设**：反身性供给者——价涨增产（看空），价跌减产（看多）

**规则阈值**：
- `daily_chg > 0 and oi_trend > 0` → score -= 30（量价齐升→增产→看空）
- `daily_chg < 0 and oi_trend < 0` → score += 25（减仓下跌→减产→看多）
- `inv_trend < -2 and daily_chg > 0` → score += 20（去库+涨→基本面改善）
- `inv_trend > 2 and daily_chg < 0` → score -= 20（累库+跌→基本面承压）
- `abs(daily_chg) < 1` → score += 5（窄幅震荡→供给刚性）

**贡献逻辑维度**：supply_risk, cost_support

### 4.2 resource — 矿商

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-50, +70) — 长期偏多 |
| info_sensitivity | 0.3 |
| beh_sensitivity | 0.2 |
| FUNDAMENTAL_WEIGHTS | {supply: -0.3, profit: +0.4, inventory: -0.2} |
| 成本线 | cost_line = 125000 元/吨 |

**交易逻辑假设**：成本支撑定价锚——价格贴成本→减产预期→看多

**规则阈值**：
- `dist_from_cost < 5` → score += 30（贴近成本线）
- `dist_from_cost < 10` → score += 10（成本线附近有支撑）
- `dist_from_cost > 20` → score -= 20（远超成本→利润丰厚→看空）
- `inv_trend < -3` → score += 10（去库→矿山挺价）
- `inv_trend > 3` → score -= 10（累库→矿山出货）

### 4.3 merchant — 贸易商

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-50, +60) |
| info_sensitivity | 0.5 |
| beh_sensitivity | 0.5 |
| FUNDAMENTAL_WEIGHTS | {basis: 0.3, inventory: 0.2, profit: 0.2} |

**交易逻辑假设**：基差/月差交易者——向后市场持有现货（看多），向前市场抛售（看空）

**规则阈值**：
- `spread_2609_2610 < -500` → score += 25（远月贴水→持有现货）
- `spread_2609_2610 > 300` → score -= 25（远月升水→抛售）
- `news_sentiment > 0.3` → score += 15（小作文利多）
- `news_sentiment < -0.3` → score -= 15（小作文利空）
- `daily_chg < -2` → score += 10（大跌后逢低补货）
- `daily_chg > 2` → score -= 10（大涨后获利了结）

**贡献逻辑维度**：demand_drop, inventory, basis_trade

### 4.4 institution — 纯投机构

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-70, +70) — 双向幅度最大 |
| info_sensitivity | 0.7 |
| beh_sensitivity | 0.6 |
| FUNDAMENTAL_WEIGHTS | {composite: 0.5} |

**交易逻辑假设**：趋势跟随/动量策略——量价齐升追多，放量下跌追空

**规则阈值**：
- `daily_chg > 1.5 and oi_trend > 0` → score += 40（量价齐升→趋势多头）
- `daily_chg < -1.5 and oi_trend > 0` → score -= 40（放量下跌→空头主导）
- `daily_chg > 1 and oi_trend < 0` → score += 5（增涨减仓→空头回补）
- `daily_chg < -1 and oi_trend < 0` → score += 10（减仓下跌→多头止损→接近底部）
- `five_day_chg > 5` → score += 15（5日动量强）
- `five_day_chg < -5` → score -= 15（5日动量弱）

### 4.5 arbitrage — 套利商

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-20, +20) — 中性倾向，市场稳定器 |
| info_sensitivity | 0.6 |
| beh_sensitivity | 0.4 |
| FUNDAMENTAL_WEIGHTS | {basis: 0.4, profit: 0.2} |

**交易逻辑假设**：月差均值回归——深度贴水正向套利（买远卖近），深度升水反向套利

**规则阈值**：
- `spread_2609_2610 < -800` → score += 10（深度贴水→正向套利空间）
- `spread_2609_2610 > 500` → score -= 10（大幅升水→反向套利）
- `abs(daily_chg) > 3` → score += 5（大幅波动→套利窗口打开）

### 4.6 retail — 散户

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-60, +60) |
| info_sensitivity | 0.9 — 全场最高 |
| beh_sensitivity | 0.9 — 全场最高 |
| FUNDAMENTAL_WEIGHTS | {sentiment: 0.4, composite: 0.2} |

**交易逻辑假设**：情绪驱动——追涨杀跌，小作文和放量触发极端反应

**规则阈值**：
- `daily_chg > 2` → score += 25（大涨追多）
- `daily_chg < -2` → score -= 25（大跌追空）
- `news_sentiment > 0.4` → score += 15（小作文利多）
- `news_sentiment < -0.4` → score -= 15（小作文利空）
- `volume_today > 250000 and daily_chg > 0` → score += 10（放量上涨散户活跃）
- `volume_today > 250000 and daily_chg < 0` → score -= 10（放量下跌恐慌）

### 4.7 policy — 政策博弈者

| 属性 | 值 |
|------|------|
| AGENT_CLAMP | (-60, +60) |
| info_sensitivity | 0.2 — 全场最低 |
| beh_sensitivity | 0.1 — 全场最低 |
| FUNDAMENTAL_WEIGHTS | 不参与（注释：policy 保持原逻辑） |

**交易逻辑假设**：政策关键词 + 绝对价格区间——最独立的主体

**规则阈值**：
- 政策关键词表：`["两会", "发改委", "工信部", "新能源", "储能", "监管", "限价", "收储", "抛储", "出口", "碳中和", "双碳"]`
- `policy_hits > 0 and policy_sentiment > 0` → score += 15（政策面偏多）
- `policy_hits > 0 and policy_sentiment < 0` → score -= 15（政策面偏空）
- `close_price > 160000` → score -= 20（价格偏高→干预风险）
- `close_price < 130000` → score += 10（价格偏低→托底预期）

---

## 五、博弈因子体系（game_factors.py）

### 5.1 三层模型

| 层级 | 名称 | 修正方向 | 公式 |
|------|------|----------|------|
| Layer 1 | 信息传递 | 恒负（toward zero） | `-ratio × info_sensitivity × 8` |
| Layer 2 | 行为影响 | 恒正（增强） | `influence × beh_sensitivity × 5` |
| Layer 3 | 极化检测 | 反转因子 | 多数派-5，少数派+5 |

### 5.2 Layer 1：信息传递（info_transfer）

**核心逻辑**：与自身方向相反的对手资金占比越大，评分越被拉向 0。

```python
# 计算步骤
1. own_score == 0 → 修正 = 0（跳过）
2. opposing_cap = Σ capital_dict[other] where sign(other_score) ≠ sign(own_score)
3. ratio = opposing_cap / total_cap
4. info_factors[aid] = round(-ratio × info_sensitivities[aid] × 8, 2)
```

**各 agent 敏感度**：retail 0.9 > institution 0.7 > arbitrage 0.6 > merchant 0.5 > smelter 0.3 = resource 0.3 > policy 0.2

### 5.3 Layer 2：行为影响（behavioral_impact）

**核心逻辑**：同向仓位变动的跟随增强——别人往我方向加仓，我的判断被验证。

```python
# 计算步骤
1. own_delta == 0 → 修正 = 0（跳过）
2. 遍历其他 agent，若仓位变动方向与自身相同：
   momentum = |other_delta| / max_pos（0~1）
   influence += (capital_dict[other] / total_cap) × momentum
3. beh_factors[aid] = round(influence × beh_sensitivities[aid] × 5, 2)
```

**各 agent 敏感度**：retail 0.9 > institution 0.6 > merchant 0.5 > arbitrage 0.4 > smelter 0.2 = resource 0.2 > policy 0.1

### 5.4 Layer 3：极化博弈检测

**触发条件**（同时满足）：
1. `n_bull > 0 and n_bear > 0`
2. `ratio = max(n_bull, n_bear) / min(n_bull, n_bear) > 3`（多空比 > 3:1）
3. `avg_abs = sum(|score|) / total_agents > 30`

**效果**：看多的 agent info_factors -= 5，看空的 += 5（多数派被压制，少数派被抬升）

### 5.5 最终修正

```python
total_correction = info_transfer + behavioral_impact  # 含极化修正
corrected_score = clamp(base_score + total_correction, -100, 100)
# 修正幅度控制在 ±10 以内
```

---

## 六、7 维逻辑评分体系

### 6.1 维度定义与映射

| 维度 | 中文名 | 贡献主体 | 核心假设 |
|------|--------|----------|----------|
| supply_risk | 供给风险 | smelter + resource | 价涨+增仓→增产→未来供给过剩风险 |
| demand_drop | 需求下滑 | merchant + institution | 跌+减仓→下游采购弱 |
| inventory | 库存变化 | merchant + arbitrage | 累库→基本面承压 |
| macro_policy | 宏观政策 | policy | 政策关键词 + 绝对价格区间 |
| cost_support | 成本支撑 | smelter + resource | 价格贴近成本线→减产预期→底部 |
| sentiment | 情绪/小作文 | retail | 小作文净占比 + 放量 |
| basis_trade | 基差月差 | arbitrage + merchant | 月差结构→持有现货有利/不利 |

### 6.2 各维度计算逻辑（代码级）

**supply_risk**：
```python
if daily_chg > 1 and oi_trend > 0:  logic_score += 40   # 价涨+增仓
elif daily_chg > 0:                 logic_score += 15
if inv_trend < -2:                  logic_score -= 15   # 去库
if fund: logic_score += (-fund["supply"] + fund["profit"]) * 10
```

**demand_drop**：
```python
if daily_chg < -1 and oi_trend < 0: logic_score = 40    # 跌+减仓
elif daily_chg < 0:                 logic_score = 15
if news_sentiment < -0.3:           logic_score += 15
if fund: logic_score += -fund["demand"] * 10 - fund["sentiment"] * 5
```

**inventory**：
```python
if inv_trend > 2:  logic_score = 40    # 累库
elif inv_trend > 0: logic_score = 15
if fund: logic_score -= fund["inventory"] * 10
```

**macro_policy**：
```python
logic_score = int(policy_pos * 10 + policy_hits * 5)
if close_price > 160000: logic_score -= 20     # 政策干预风险
elif close_price < 130000: logic_score += 10   # 政策托底预期
```

**cost_support**：
```python
if -5 < dist_from_cost < 5:   logic_score = 40   # 贴近成本线
elif -10 < dist_from_cost < 10: logic_score = 20
elif dist_from_cost > 20:     logic_score = -20  # 远离成本
if fund: logic_score -= fund["profit"] * 10
```

**sentiment**：
```python
logic_score = int(news_sentiment * 50)
if volume_today > 250000 and daily_chg > 0: logic_score += 10
elif volume_today > 250000 and daily_chg < 0: logic_score -= 10
if fund: logic_score += int(fund["sentiment"] * 10)
```

**basis_trade**：
```python
if spread_2609_2610 < -500:  logic_score = 20   # 远月贴水
elif spread_2609_2610 > 300: logic_score = -20  # 远月升水
if spread_2611_2612 < -300:  logic_score += 10
if fund: logic_score += int(fund["basis"] * 10)
```

---

## 七、信号驱动互动（signal_interactions.py）

### 7.1 五类信号

| 信号 | 数据源 | 频率 | 方向逻辑 |
|------|--------|------|----------|
| premium 升贴水 | lithium_daily_prices col2/col3 取中值 | 日度 | 中值+20日变化，正=利多 |
| inventory 库存 | lithium_weekly col2 大样本库存总计 | 周度 | 去库=利多（取反） |
| output 产量 | lithium_weekly col12 产量总计 | 周度 | 减产=利多（取反） |
| monthly_output 月度产量 | lithium_monthly col1 | 月度 | 减产=利多 |
| price_momentum 价格动量 | prices 表 5 日涨跌幅 | 日度 | 5日涨=利多 |

### 7.2 各 agent 信号敏感度

| Agent | 升贴水 | 库存 | 产量 | 价格动量 |
|-------|--------|------|------|----------|
| smelter | 0.7 | 0.6 | - | - |
| merchant | 1.0 | 0.8 | 负（产量多=利空） | - |
| institution | - | 0.9 | 0.8(需求) | - |
| arbitrage | 1.0 | - | - | - |
| retail | - | - | - | 1.0 |
| resource | - | - | 0.9 | - |
| policy | - | 0.3 | - | - |

### 7.3 四种互动类型

| 类型 | 触发 | 幅度基准 |
|------|------|----------|
| signal_resonance | 同阵营同向解读 | 10 |
| signal_conflict | 跨阵营相反解读 | 14 |
| behavior_follow | 仓位变动跟随 | - |
| info_conflict | 观点对立 | - |

**知行背离放大**：`score_dir ≠ sig_dir` 的 agent 互动幅度 ×1.3

---

## 八、基本面六维指数（fundamental_indices.py）

### 8.1 六维定义

| 维度 | 含义 | 方向 |
|------|------|------|
| supply | 供应（产量/开工） | 正=利多（供应收紧） |
| demand | 需求（排产/表观消费） | 正=利多 |
| inventory | 库存 | 正=利多（去库） |
| profit | 利润（冶炼/矿端利润） | 正=利多 |
| sentiment | 情绪（小作文/新闻） | 正=利多 |
| basis | 基差/月差 | 正=利多 |

另有 composite 综合指数。

### 8.2 防透视标准化

每个指标用 **滚动 250 交易日窗口 z-score**（只用当日及之前数据），方向归一化后加权合成。

---

## 九、公共机制参数

| 参数 | 值 | 说明 |
|------|------|------|
| FUNDAMENTAL_SCALE | 30 | 基本面 z-score 乘数 |
| 惯性钳制 | ±20/日 | 分数相对昨日最多跳变 |
| pos_change 上限 | 30% | 评分每 +100 分 → 仓位变动 max_pos × 30% |
| stop_loss_pct | 0.08 | 亏损 > 8% → 全部平仓 |
| take_profit_pct | 0.15 | 盈利 > 15% → 减仓一半 |
| max_position | 100 | 各 agent 缺省最大仓位 |
| capital | 5000 | 各 agent 缺省资金量 |
| cost_line | 125000 | 碳酸锂成本线（元/吨） |
| 政策价格锚 | 160000 / 130000 | 干预风险 / 托底预期 |

---

## 十、数据库表结构（lithium.db，35 表）

### 10.1 核心数据表

| 表名 | 行数 | 说明 |
|------|------|------|
| prices | 32122 | LC0 主力日K（close/volume/position/settle） |
| contract_prices | 8404 | 各合约收盘价 |
| spreads | 7672 | 月差结构 |
| inventory_history | 67 | 库存数据 |
| fundamental_indices | 728 | 六维 z-score |
| notes | 5 | 小作文情绪 |
| spot_price | **0** | 现货价格（空表，缺口） |
| minute_prices | - | 分钟级数据 |

### 10.2 Agent 表

| 表名 | 行数 | 说明 |
|------|------|------|
| agents | 7 | 各 agent 最新状态（view_score/position/pnl/needs_review） |
| agent_history | 226 | 每日原始评分与仓位 |
| agent_view_log | 224 | 每日观点记录（含 key_indicators JSON） |
| agent_action_log | - | 每日动作记录 |
| agent_game_factors | 168 | 博弈因子详情（base/info/beh/total/corrected） |
| logic_scores | - | 7 维逻辑评分汇总（avg/min/max/agent_count） |
| agent_interactions | 751 | 主体间互动记录 |
| agent_events | 7 | 异常事件 |
| agent_psychology | - | 心理画像（state/anchor_price/buy_zone） |
| agent_custom_data | 2 | 手动注入数据 |
| behavior_models | 8 | 行为模型配置 |
| participants | 8 | 参与者配置 |

### 10.3 外部数据库

| 数据库 | 路径 | 说明 |
|--------|------|------|
| lc_position.db | /home/ubuntu/lc_futures_data/data/lc_position.db | 仓单(1791行)/席位(7540行) |
| lc_spot.db | /home/ubuntu/lc_futures_data/data/lc_spot.db | 现货+基差（732行，20230721~20260730） |

---

## 十一、数据缺口清单（2026-09-08 验证后修正）

### 11.1 已验证解决的缺口（假缺口）

| 原始缺口 | 验证结果 | 实际状态 |
|----------|----------|----------|
| 🔴 spot_price 表 0 行 | **已解决**：`lc_spot.db` 实际在 `/home/ubuntu/lc_futures_data/data/lc_spot.db`，原始只有 20 行（20260730~20260904），已从 `lithium_daily_prices col6`（SMM 电池级碳酸锂平均价，4760 行）回填为 **732 行**（20230721~20260730），含现货价+近月/主力合约价+基差 | ✅ 2026-09-08 完成 |
| 🔴 宜春云母矿开工率 | **假缺口**：`lithium_weekly col22` = SMM: 碳酸锂开工率: 锂云母产: 周度（%，305 周数据），知几搜"碳酸锂 开工率"只返回铝杆 | ✅ 已有 |
| 🟡 南美盐湖月产量 | **假缺口**：`lithium_monthly col7` = 中国海关: 碳酸锂进口量: 智利: 月度，`col8` = 阿根廷进口量 | ✅ 已有 |
| 🟢 废旧电池黑粉价格 | **假缺口**：本地已有 4 种黑粉利润数据（`lithium_daily_prices col13-18`），知几也有黑粉现货价（ID01525354 等） | ✅ 已有 |
| 🟡 察尔汗单矿产量 | **部分**：`lithium_monthly col4` = SMM 盐湖碳酸锂产量（月），知几 ID01707137 也有 | 🟡 部分 |

### 11.2 真缺口（需补）

| 缺口 | 原因 | 解法 |
|------|------|------|
| 🟡 期货研报多空家数 | 知几只有分析师指数行情（CM0000813027），无研报评级统计 | 需东财研报 API 或手动注入 |
| 🟡 碳酸锂研报评级 | 无公开聚合源 | 需东财研报 API |

### 11.3 本地已有数据全景（lithium_meta 元数据）

系统已有 SMM 数据远比想象中丰富：

- **日度价（22 个指标）**：期现价差(col1)、升贴水低/高幅(col2-3)、电池级最低价/最高价/平均价(col4-6)、CIF 中日韩价(col7)、电池级-工业级价差(col8)、氢氧化锂-碳酸锂价差(col9)、成交/出货/购货情绪因子(col10-12)、4 种黑粉生产利润(col13-18)、锂辉石/锂云母生产利润(col15-16)、进口利润(col19)、理论交割利润(col20-22)
- **周度（25 个指标）**：库存（冶炼厂/大样本/其他/下游/总计/库存天数/四地社库，col1-11）、产量（总计/盐湖/回收料/锂辉石/锂云母，col12-16）、开工率（总计/冶炼/回收料/锂辉石/盐湖/锂云母，col17-22）、动力电芯产量(col23-25)
- **月度（18 个指标）**：产量分类别（总计/锂辉石/锂云母/盐湖/回收，col1-5）、进口量（总计/智利/阿根廷，col6-8）、三元/铁锂/电芯产量(col9-14)、储能中标容量(col15-18)

### 11.2 功能缺口

| 缺口 | 状态 | 说明 |
|------|------|------|
| 8 主体重构 | 封存待确认 | 设计已完成（见 docs/task_freeze_8agents_20260801.md），等用户拍板 |
| 正极厂 agent | 未实现 | 需求侧：排产/采购/库存天数 |
| 期货基本面资金 | 未实现 | 研报多空比时序 |
| 权益类资金改造 | 未实现 | A股锂矿指数 + 研报看涨家数 |
| 防透视回测 | 设计完成 | 代码已写（lithium_backtest.py），未跑完验证 |
| 甘特图页面 | 设计完成 | 未建（lithium_logic_gantt.html 未创建） |

### 11.3 系统缺口

| 缺口 | 说明 |
|------|------|
| lc_futures_data 不在 data-harbor 包内 | 碳酸锂持仓采集系统完全独立 |
| 8 主体重构未开工 | 已封存设计，等确认 |
| GitHub 协作交接文档缺失 | 未写 AGENT_HANDOVER.md |

---

## 十二、会话历史脉络

### 12.1 关键会话（按时间）

| 日期 | 会话主题 | 核心产出 |
|------|----------|----------|
| 2026-07-30 | 博弈系统设计 | 三层博弈因子 + 7维逻辑 + 甘特图设计 |
| 2026-07-30 | 博弈互动实施 | 实施清单拆解（A/B/C 任务） |
| 2026-08-01 | 8 主体重构封存 | 设计对齐，等用户确认 |
| 2026-08-01 | 交易者说明书 | 六章完整手册（代码级对应） |
| 2026-08-01 | 防透视回测 | 回测引擎设计 + 代码 |
| 2026-08-01 | Agent 行为模型对比 | 7 Agent vs 新 8 主体 |
| 2026-08-01 | 基本面六维指数 | fundamental_indices.py 实现 |
| 2026-08-01 | 信号驱动互动 | signal_interactions.py 部署 |
| 2026-09-03 | 同花顺分类范式 | 48 份分类范式文档 |
| 2026-09-04 | 锌库存筛选验证 | 同花顺 4 步逻辑 + 知几对照 |
| 2026-09-07 | 白名单约束推荐 | 知几白名单体系（47/50 成功） |

### 12.2 架构演进时间线

```
2026-07 中旬：基础框架（7 agent + 规则引擎）
    ↓
2026-07-30：博弈因子 v1.0（三层修正）+ 7维逻辑评分
    ↓
2026-08-01：基本面六维指数 + 信号驱动互动 + 防透视回测
    ↓
2026-08-01：8 主体重构设计封存（等确认）
    ↓
2026-08-01：交易者说明书（六章完整手册）
    ↓
2026-09：同花顺分类范式 → 知几白名单约束 → framework-tree 看板
    ↓
2026-09-08：本次归档
```

---

## 十三、上线 GitHub 协作前的待办

### 13.1 P0（上线前必须做）

1. **写 AGENT_HANDOVER.md**：把本归档的精简版写入 git，给新 agent/协作者读
2. **修 spot_price 表**：接入现货价格数据源（基差核心输入）
3. **确认 8 主体重构方向**：开工还是维持 7 主体
4. **更新 README**：补充完整目录结构说明

### 13.2 P1（上线后尽快做）

1. **建甘特图页面**：lithium_logic_gantt.html（设计已完成）
2. **跑防透视回测**：验证博弈系统有效性
3. **补外部数据源**：宜春云母开工率、察尔汗产量、黑粉价格
4. **补期货研报数据**：手动注入或找数据源

### 13.3 P2（锦上添花）

1. **8 主体重构实施**：按封存设计开工
2. **LLM 审核联动**：博弈触发 → LLM 批量审核
3. **lc_futures_data 纳入统一打包**

---

## 十四、关键函数索引（速查）

| 函数 | 文件 | 作用 |
|------|------|------|
| `compute_agent_score(agent_id, market, logic_scores)` | agent_daily_update.py:201 | 规则引擎打分（各 agent 专属逻辑） |
| `_compute_logic_scores(agent_id, market, logic_scores, ...)` | agent_daily_update.py:530 | 7 维逻辑评分计算 |
| `run_daily_update()` | agent_daily_update.py:655 | 每日更新主流程 |
| `apply_game_factors(base_scores, deltas, texts, agents, capital, market, today, conn, agent_ids)` | game_factors.py:16 | 三层博弈因子修正 |
| `compute_signals(conn, today)` | signal_interactions.py:133 | 计算五类市场信号 |
| `apply_signal_interactions(conn, today, base_scores, agents, market)` | signal_interactions.py:286 | 信号驱动互动生成 |
| `dominant_signal(agent_id)` | signal_interactions.py:65 | 各 agent 主导信号 |
| `agent_signal_bias(agent_id, signals)` | signal_interactions.py:256 | 主体信号立场 |
| `roll_zscore(series, window=250)` | fundamental_indices.py:46 | 滚动 250 日 z-score |
| `get_db()` | lithium_api.py:25 | 数据库连接（WAL 模式） |
| `init_minute_tables()` | lithium_api.py:33 | 初始化分钟数据表 |

---

## 十五、环境变量与配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| LITHIUM_DB | /home/ubuntu/lithium_calendar/lithium.db | 主数据库路径 |
| zinc_venv 路径 | /home/ubuntu/zinc_venv/lib/python3.11/site-packages | akshare 依赖路径 |
| 服务端口 | 8766 | Flask 服务端口 |
| Supervisor 配置 | /etc/supervisor/dashboards.conf | 进程管理 |
| cron 更新 | 工作日 18:00 | GitHub Pages 数据推送 |
| Agent 更新 | 工作日 18:30 | agent_daily_update.py |

---

## 十六、关联资源

| 资源 | 地址 |
|------|------|
| framework-tree 看板 | https://algo23-yunqingtian.github.io/framework-tree/ |
| 镍看板 | https://github.com/algo23-yunqingtian/nickel-dashboard |
| 宏观看板 | https://github.com/algo23-yunqingtian/macro-dashboard |
| 知几 API | ~/.hermes/scripts/zhiji_api.py |
| 同花顺问财 | https://www.iwencai.com/chat |

---

*归档完成。新协作者/agent 读此文件即可完全理解系统全貌。*