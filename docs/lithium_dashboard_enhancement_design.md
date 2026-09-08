# 碳酸锂日报/看板增强设计方案

> 2026-07-29 · 基于用户当前系统状态进行的增强设计

---

## 1. 期限结构 / 跨期套利增强（已实现）

### 已完成
- `/api/term-structure?days=5` 返回最近 5 个交易日合约收盘价、成交量、持仓量
- 自动计算相邻合约月差（远月 - 近月）及后一日 vs 前一日变化
- 主页期限结构卡片渲染：
  - 5 天收盘价曲线叠加（实线为当天，虚线为往日）
  - 最新日成交量/持仓量双轴
  - 月差变化小表格（含相对前一日涨跌颜色标识）
  - 合约量仓绝对/百分比变动卡片

### 下一步可加
- 主动套利监测：正向市/反向市、鹰鸽市并库存套利的涨跌，颜色标记机会
- 年节性月差均值/当前月差偏离：识别稀缺/贵布稀缺位置
- 月差 volume/持仓变化的散点图：找出「量价分离」的合约

---

## 2. 子页面回顾与增强方向

### 当前页面清单
位于 `/lithium_legacy/` 下的子页面：

| 页面 | 形式 | 当前状态 | 原设计回忆/建议增强 |
|---|---|---|---|
| `sentiment_dashboard.html` | 情绪走势仪表盘 | 前端稽子 | 除了碳酸锂价格/持仓/库存，应加入相关品种联动：锂资源股/新能源车轴/镍/钴现货。还应包含沪深/港股新能源板块指数、碳酸锂现货报价。 |
| `market_timeline.html` | 时间线 | 已实装笔记 | 可加入资金事件/产业事件轴与小作文事件轴对齐。 |
| `logic_scanner.html` | 雷达图 | 前端稽子 | 可添加每日打分历史，形成「交易逻辑甘特图」（见下文）。 |
| `market_battlefield.html` | 多主体博弈 | 前端稽子 | 本文档第 3 节方案的实际落地页面。 |
| `lithium_balance_v2.html` | 供需平衡 | 已实装 | 可对比历史平衡表转折点与当前价格。 |
| `battlefield_v2_mockup.html` | 多合约 K 线+博弈 | 前端稽子 | 可作为主页多合约看板迭代。 |

### 重点：情绪仪表盘应加入的相关品种
建议分为三层：

1. **产业价格层**
   - 碳酸锂现货报价（工业级/电池级）
   - 沪锂主力连续合约 LC0
   - 镍期货（与三元电池相关）
   - 锂资源股/新能源车轴股（港股/沪深）

2. **宏观/需求层**
   - 新能源汽车下游销量（乘联会/保险交强险）
   - 储能安装量
   - 人民币/美元指数

3. **情绪辅助层**
   - 小作文定时抓取与情绪得分
   - 顶部咨询公司/电池厂提价追踪

### 重点：交易逻辑甘特图
锂盘目前缺少与锌 `zinc_review_version_B_deployed.html` 类似的逻辑甘特图。可在 `logic_scanner.html` 或新增 `logic_gantt.html` 中：
- 横轴：日期
- 纵轴：主导交易逻辑（供应紧张/需求下滑/宏观放水/资金操纵/小作文阵等）
- 颜色：多空强度
- 每日自动主导逻辑评分，用户可手动微调

---

## 3. 多主体博弈模拟：Token 最优架构

### 核心原则
**90% 规则驱动 + 10% LLM 决策** 。不要每秒都跑 LLM，只在「事件触发」时用一次 LLM 批量更新多主体。

### 状态层（SQLite 持久化）
在 `lithium.db` 新增表：

```sql
-- agents: 主体定义
CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  name TEXT, role TEXT, type TEXT,            -- 例：山泰/冶炼厂/纯投机构/套利商/散户
  capital REAL, risk_appetite REAL,           -- 风险偏好
  position INTEGER, avg_cost REAL,            -- 当前持仓
  target_long REAL, target_short REAL,        -- 心理预期价位
  stop_loss REAL, take_profit REAL,           -- 止损/止盈
  view_score INTEGER,                         -- -100~多头  / +100~空头
  view_text TEXT,                             -- 当前观点概述
  last_review TEXT, next_trigger TEXT         -- 上次 LLM评审/下次触发条件
);

-- agent_history: 每日快照
CREATE TABLE agent_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT, date TEXT, position INTEGER, avg_cost REAL,
  view_score INTEGER, view_text TEXT, pnl_unreal REAL
);

-- agent_events: 市场事件
CREATE TABLE agent_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT, event_type TEXT, summary TEXT, severity INTEGER
);

-- agent_decisions: LLM/规则决策
CREATE TABLE agent_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT, date TEXT, trigger TEXT,
  action TEXT, delta_position INTEGER, reason TEXT, model_used TEXT
);
```

### 更新层（规则 + 事件）

#### 低频规则更新（每日收盘后 no_agent 脚本）
1. 拉取最新日 K 线、库存、小作文、行情
2. 对每个 agent：
   - 更新浮盈亏 = (close - avg_cost) * position
   - 检查止损/止盈：触发则算 `delta_position`
   - 检查心理价位：价格靠近 target_long/target_short 时标记 `needs_review`
   - 检查位置上限：位置超出风控则减仓
3. 保存快照到 `agent_history`

#### 事件触发（可缺省为空）
- 价格涨跌 > 某 agent 风险阈值
- 量仓极端变化
- 重大新闻/小作文事件（用户手动标记）

### LLM 层（批量决策）
只在存在 `needs_review` agent 时调用，**1 次请求批量处理所有 agent**。

Prompt 输入：
```
市场快照：
- 今日收盘 X, 涨跌 Y, 量 Z, 仓 W
- 主导事件：[1-3 条]

Agent 状态（只传需要审核的 agent）：
- Agent A: 角色, 当前位置, 均价, 目标价, 止损, 止盈, 上次观点

请对每个 agent 返回：
1. 更新后的 view_score (整数)
2. 一句观点 view_text
3. 是否调整 target_long / target_short / stop_loss / take_profit
4. 操作动作：hold / add / reduce / reverse 及数量
5. 简短理由
```

### 输出层（前端）
`market_battlefield.html` 直接读取 `agent_history` + `agent_decisions` 实时渲染：
- 左侧：各主体持仓、成本、目标价、止损止盈、当前观点
- 中间：多空力量比例/情绪周期位置
- 右侧：策略建议/空多阵营近几日动作

### Token 与费用优化
1. 无 LLM 时纯脚本更新，几乎 0 token
2. 仅有事件才调 LLM，平均 1-2 次/日
3. 一次 LLM 批量处理 5-8 个 agent，比单独 8 次节省 80%
4. 视图文本生成可在日报 cron 中批量完成
5. 可选用更便宜模型（GLM-5-Air）做 LLM 决策，只在复盘时用贵模型

### 落地步骤
1. 添加 SQLite 表结构
2. 在 `app.py` 新增 `POST /api/agents` 初始化 / `GET /api/battlefield` 查询
3. 新建 no_agent cron `agent_daily_update.py` 跑规则更新
4. 修改 `market_battlefield.html` 从实时读取数据而非静态模拟
5. 可选：在主页左侧日历面板中增加「当日主体卡片」预览

---

## 4. 推荐优先级

1. ⭐ ⭐ ⭐ 期限结构已完成，继续优化套利监测
2. ⭐ ⭐ ⭐ 多主体博弈状态层 + 日更新脚本
3. ⭐ ⭐ 情绪仪表盘加入相关品种联动
4. ⭐ ⭐ 交易逻辑甘特图
5. ⭐ 时间线与产业/资金事件轴对齐
