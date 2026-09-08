# 数据库表结构说明（35 张表）

> `lithium.db` 共 35 张表，按功能分 5 组。外部 DB（`lc_spot.db` / `lc_position.db`）单独列出。

---

## 表分组总览

| 分组 | 表数 | 表名 |
|------|------|------|
| Agent 核心表 | 10 | agents, agent_history, agent_view_log, agent_action_log, agent_game_factors, agent_interactions, agent_psychology, agent_custom_data, agent_decisions, agent_events |
| 价格数据表 | 10 | prices, contract_prices, contract_daily_all, spreads, lithium_daily_prices, lithium_weekly, lithium_monthly, lithium_meta, minute_prices, spot_price |
| 博弈引擎表 | 3 | fundamental_indices, logic_scores, market_signals |
| 辅助表 | 12 | behavior_models, cards, info_access, info_categories, interpretations, inventory_history, notes, paichan_visits, participant_positions, participants, periods, simulation_log |

---

## 一、Agent 核心表（10 张）

### 1. agents — 主体当前状态

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID（主键） |
| name | TEXT | 主体名称 |
| role | TEXT | 角色（冶炼厂/矿商/贸易商/机构/套利商/散户/政策） |
| capital | REAL | 资金规模 |
| position | REAL | 当前持仓量（正=多头，负=空头） |
| avg_cost | REAL | 持仓均价 |
| view_score | REAL | 观点得分（-100~+100） |
| view_text | TEXT | 观点文字描述 |
| pnl_unreal | REAL | 未实现盈亏 |

- **行数**：7 行（7 主体各 1 行）
- **更新频率**：每日 1 次（18:30 cron）

### 2. agent_history — 主体历史快照

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID |
| date | TEXT | 日期 |
| position | REAL | 当日持仓 |
| avg_cost | REAL | 当日均价 |
| view_score | REAL | 当日观点得分 |
| view_text | TEXT | 当日观点文字 |
| pnl_unreal | REAL | 当日未实现盈亏 |
| close | REAL | 当日收盘价 |

- **行数**：408 行
- **更新频率**：每日追加

### 3. agent_view_log — 观点生成日志

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID |
| date | TEXT | 日期 |
| view_score | REAL | 观点得分 |
| view_text | TEXT | 观点文字 |
| daily_reasoning | TEXT | 每日推理过程 |
| key_indicators | TEXT | 关键指标列表（JSON） |
| daily_actions | TEXT | 当日行动描述 |

- **行数**：406 行
- **更新频率**：每日追加

### 4. agent_action_log — 交易行为日志

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID |
| date | TEXT | 日期 |
| prev_position | REAL | 前一日持仓 |
| new_position | REAL | 新持仓 |
| delta | REAL | 持仓变动量 |
| trigger | TEXT | 触发原因 |
| reasoning | TEXT | 决策推理 |
| close_price | REAL | 当日收盘价 |
| pnl_unreal | REAL | 未实现盈亏 |

- **行数**：408 行
- **更新频率**：每日追加

### 5. agent_game_factors — 博弈因子

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| agent_id | TEXT | 主体ID |
| base_score | REAL | 基础得分 |
| info_transfer | REAL | 信息传递修正 |
| behavioral_impact | REAL | 行为影响修正 |
| total_correction | REAL | 总修正量 |
| corrected_score | REAL | 修正后得分 |
| factors_detail | TEXT | 因子明细（JSON） |

- **行数**：350 行
- **更新频率**：每日追加

### 6. agent_interactions — 主体互动记录

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| from_agent | TEXT | 发起方主体ID |
| to_agent | TEXT | 接收方主体ID |
| influence_type | TEXT | 影响类型 |
| magnitude | REAL | 影响幅度 |

- **行数**：2099 行
- **更新频率**：每日追加

### 7. agent_psychology — 主体心理状态

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| agent_id | TEXT | 主体ID |
| state | TEXT | 心理状态 |
| anchor_price | REAL | 锚定价格 |
| buy_zone_low | REAL | 买入区间下限 |
| buy_zone_high | REAL | 买入区间上限 |
| trigger_desc | TEXT | 触发描述 |
| score | REAL | 心理得分 |
| state_color | TEXT | 状态颜色标签 |

- **行数**：336 行
- **更新频率**：每日追加

### 8. agent_custom_data — 主体自定义数据

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID |
| date | TEXT | 日期 |
| key | TEXT | 自定义键 |
| value | TEXT | 自定义值 |

- **行数**：2 行
- **更新频率**：按需

### 9. agent_decisions — 决策记录

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID |
| date | TEXT | 日期 |
| decision | TEXT | 决策内容 |
| context | TEXT | 决策上下文 |

- **行数**：0 行（预留）
- **更新频率**：按需

### 10. agent_events — 事件记录

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| agent_id | TEXT | 主体ID |
| event_type | TEXT | 事件类型 |
| description | TEXT | 事件描述 |
| impact | REAL | 影响值 |

- **行数**：17 行
- **更新频率**：事件驱动

---

## 二、价格数据表（10 张）

### 11. prices — 期货日 K 线

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| open | REAL | 开盘价 |
| high | REAL | 最高价 |
| low | REAL | 最低价 |
| close | REAL | 收盘价 |
| volume | REAL | 成交量 |
| position | REAL | 持仓量 |
| settle | REAL | 结算价 |

- **行数**：760 行
- **更新频率**：每日（18:00 采集）

### 12. contract_prices — 合约价格（多合约）

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| contract | TEXT | 合约代码 |
| open | REAL | 开盘价 |
| high | REAL | 最高价 |
| low | REAL | 最低价 |
| close | REAL | 收盘价 |
| volume | REAL | 成交量 |
| position | REAL | 持仓量 |
| settle | REAL | 结算价 |

- **行数**：8677 行
- **更新频率**：每日

### 13. contract_daily_all — 全合约日汇总

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| contract | TEXT | 合约代码 |
| (OHLCV字段) | REAL | 同 contract_prices |

- **行数**：8393 行
- **更新频率**：每日

### 14. spreads — 月差数据

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| near_contract | TEXT | 近月合约 |
| far_contract | TEXT | 远月合约 |
| spread | REAL | 价差 |

- **行数**：7945 行
- **更新频率**：每日

### 15. lithium_daily_prices — SMM 日度价格

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| col_idx | TEXT | 列索引（指标名） |
| value | REAL | 价格值 |

- **行数**：32122 行
- **更新频率**：每日

### 16. lithium_weekly — SMM 周度价格

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 周日期 |
| col_idx | TEXT | 列索引 |
| value | REAL | 价格值 |

- **行数**：3342 行
- **更新频率**：每周

### 17. lithium_monthly — SMM 月度价格

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 月日期 |
| col_idx | TEXT | 列索引 |
| value | REAL | 价格值 |

- **行数**：1356 行
- **更新频率**：每月

### 18. lithium_meta — SMM 数据元信息

| 列名 | 类型 | 用途 |
|------|------|------|
| sheet_name | TEXT | Sheet名称 |
| col_idx | TEXT | 列索引 |
| key | TEXT | 元数据键 |
| value | TEXT | 元数据值 |

- **行数**：260 行
- **更新频率**：随数据更新

### 19. minute_prices — 分钟级行情

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期时间 |
| (OHLCV字段) | REAL | K线字段 |

- **行数**：0 行（预留）
- **更新频率**：盘中（18:00 cron 采集，当前未启用）

### 20. spot_price — 现货价格（空表，实际在 lc_spot.db）

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| (价格字段) | REAL | 现货价格 |

- **行数**：0 行（本表为预留，实际数据在 `lc_spot.db`，732 行）
- **更新频率**：每日

---

## 三、博弈引擎表（3 张）

### 21. fundamental_indices — 基本面综合指数

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| supply | REAL | 供给指数 |
| demand | REAL | 需求指数 |
| inventory | REAL | 库存指数 |
| profit | REAL | 利润指数 |
| sentiment | REAL | 情绪指数 |
| basis | REAL | 基差指数 |
| composite | REAL | 综合指数 |
| n_dims | INTEGER | 参与维度数 |

- **行数**：754 行
- **更新频率**：每日

### 22. logic_scores — 逻辑评分

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| logic_name | TEXT | 逻辑名称 |
| avg_score | REAL | 平均评分 |
| min_score | REAL | 最低评分 |
| max_score | REAL | 最高评分 |
| agent_count | INTEGER | 参评主体数 |

- **行数**：350 行
- **更新频率**：每日

### 23. market_signals — 市场信号

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| signal | TEXT | 信号名称 |
| value | REAL | 信号值 |
| change | REAL | 变化量 |
| strength | REAL | 信号强度 |
| direction | TEXT | 方向（bullish/bearish/neutral） |
| detail | TEXT | 详细描述 |

- **行数**：300 行
- **更新频率**：每日

---

## 四、辅助表（12 张）

### 24. behavior_models — 行为模型

| 列名 | 类型 | 用途 |
|------|------|------|
| model_id | TEXT | 模型ID |
| agent_type | TEXT | 主体类型 |
| parameters | TEXT | 模型参数（JSON） |
| description | TEXT | 描述 |

- **行数**：8 行
- **更新频率**：按需

### 25. cards — 卡牌（事件卡）

| 列名 | 类型 | 用途 |
|------|------|------|
| card_id | TEXT | 卡牌ID |
| name | TEXT | 卡牌名称 |
| description | TEXT | 卡牌效果 |
| effect | TEXT | 具体影响值 |

- **行数**：9 行
- **更新频率**：按需

### 26. info_access — 信息获取权限

| 列名 | 类型 | 用途 |
|------|------|------|
| agent_id | TEXT | 主体ID |
| category | TEXT | 信息类别 |
| access_level | TEXT | 访问级别 |
| weight | REAL | 权重 |

- **行数**：80 行（7 主体 × ~11 类别）
- **更新频率**：按需

### 27. info_categories — 信息分类

| 列名 | 类型 | 用途 |
|------|------|------|
| category_id | TEXT | 类别ID |
| name | TEXT | 类别名称 |
| description | TEXT | 描述 |

- **行数**：10 行
- **更新频率**：按需

### 28. interpretations — 解读模板

| 列名 | 类型 | 用途 |
|------|------|------|
| interp_id | TEXT | 解读ID |
| signal | TEXT | 关联信号 |
| interpretation | TEXT | 解读内容 |
| direction | TEXT | 方向 |

- **行数**：40 行
- **更新频率**：按需

### 29. inventory_history — 库存历史

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| inventory | REAL | 库存量 |
| change | REAL | 环比变化 |
| source | TEXT | 数据来源 |

- **行数**：67 行
- **更新频率**：每周

### 30. notes — 备注记录

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| content | TEXT | 备注内容 |
| author | TEXT | 作者 |

- **行数**：5 行
- **更新频率**：按需

### 31. paichan_visits — 排产调研

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| (调研字段) | TEXT | 排产调研数据 |

- **行数**：0 行（预留）
- **更新频率**：按需

### 32. participant_positions — 参与者持仓

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| participant | TEXT | 参与者名称 |
| long_position | REAL | 多头持仓 |
| short_position | REAL | 空头持仓 |
| net_position | REAL | 净持仓 |

- **行数**：20 行
- **更新频率**：每日

### 33. participants — 参与者列表

| 列名 | 类型 | 用途 |
|------|------|------|
| participant_id | TEXT | 参与者ID |
| name | TEXT | 名称 |
| type | TEXT | 类型（期货公司/产业客户） |

- **行数**：8 行
- **更新频率**：按需

### 34. periods — 周期配置

| 列名 | 类型 | 用途 |
|------|------|------|
| period_id | TEXT | 周期ID |
| name | TEXT | 周期名称 |
| start_date | TEXT | 开始日期 |
| end_date | TEXT | 结束日期 |

- **行数**：0 行（预留）
- **更新频率**：按需

### 35. simulation_log — 模拟日志

| 列名 | 类型 | 用途 |
|------|------|------|
| date | TEXT | 日期 |
| sim_type | TEXT | 模拟类型 |
| result | TEXT | 模拟结果 |
| params | TEXT | 参数（JSON） |

- **行数**：0 行（预留）
- **更新频率**：按需

---

## 五、外部数据库

### lc_spot.db — 现货价格数据库

| 数据 | 行数 | 字段 |
|------|------|------|
| 现货价格 | 732 | date, price, change |
| 基差数据 | 732 | date, spot, futures, basis |

- **路径**：后端根目录
- **更新频率**：每日
- **访问模式**：只读

### lc_position.db — 持仓数据库

| 数据 | 行数 | 字段 |
|------|------|------|
| 仓单数据 | 1791 | date, warehouse, warrant_count, volume |
| 席位数据 | 7540 | date, broker, long, short, net |

- **路径**：后端根目录
- **更新频率**：每日
- **访问模式**：只读

---

## 行数统计速查

| 表 | 行数 | 频率 |
|----|------|------|
| agents | 7 | 日 |
| agent_history | 408 | 日追加 |
| agent_view_log | 406 | 日追加 |
| agent_action_log | 408 | 日追加 |
| agent_game_factors | 350 | 日追加 |
| agent_interactions | 2099 | 日追加 |
| agent_psychology | 336 | 日追加 |
| agent_custom_data | 2 | 按需 |
| agent_decisions | 0 | 预留 |
| agent_events | 17 | 事件驱动 |
| prices | 760 | 日 |
| contract_prices | 8677 | 日 |
| contract_daily_all | 8393 | 日 |
| spreads | 7945 | 日 |
| lithium_daily_prices | 32122 | 日 |
| lithium_weekly | 3342 | 周 |
| lithium_monthly | 1356 | 月 |
| lithium_meta | 260 | 随数据 |
| minute_prices | 0 | 预留 |
| spot_price | 0 | 预留(见lc_spot.db) |
| fundamental_indices | 754 | 日 |
| logic_scores | 350 | 日 |
| market_signals | 300 | 日 |
| behavior_models | 8 | 按需 |
| cards | 9 | 按需 |
| info_access | 80 | 按需 |
| info_categories | 10 | 按需 |
| interpretations | 40 | 按需 |
| inventory_history | 67 | 周 |
| notes | 5 | 按需 |
| paichan_visits | 0 | 预留 |
| participant_positions | 20 | 日 |
| participants | 8 | 按需 |
| periods | 0 | 预留 |
| simulation_log | 0 | 预留 |
| **lc_spot.db** | **732** | **日** |
| **lc_position.db 仓单** | **1791** | **日** |
| **lc_position.db 席位** | **7540** | **日** |
