# 🔋 碳酸锂多主体博弈系统 — 交接文档 v3

> **创建日期**: 2026-07-31  
> **最后更新**: 2026-07-31  
> **部署地址**: http://124.221.113.37:8766/battlefield_agents.html  
> **当前状态**: 基础系统完成，数据严重不足，等待新数据接入后继续开发

---

## 一、项目概况

### 一句话说明

模拟碳酸锂市场上 **7种不同身份的Agent**（冶炼厂、矿商、贸易商、机构、散户、套利商、政策制定者），它们各自根据规则引擎每日做出多空判断和仓位决策，通过12个ECharts图表可视化展示"多方和空方谁更强"的博弈全景。

### 架构概览

```
┌──────────────────────────────────────────────┐
│  每日数据 (cron)                               │
│  ┌─────────────────────────────────────────┐  │
│  │  prices (LC0 K线) 732条 (2023-07~2026-07)│  │
│  │  contract_prices (5个月份) 946条         │  │
│  │  inventory_history 67条 (2026-04~07)     │  │
│  │  notes 5条 ⚠️严重不足                     │  │
│  │  spreads 300条 (从contract计算)          │  │
│  └─────────────────────────────────────────┘  │
│                    ↓                           │
│  ┌─────────────────────────────────────────┐  │
│  │  agent_daily_update.py (评分引擎)        │  │
│  │    7个Agent × 规则引擎 → view_score      │  │
│  │    惯性限制(±20) + 止损止盈              │  │
│  └─────────────────────────────────────────┘  │
│                    ↓                           │
│  ┌─────────────────────────────────────────┐  │
│  │  game_factors.py (博弈因子)              │  │
│  │    Layer 1: 信息传递 (分歧修正)          │  │
│  │    Layer 2: 行为影响 (跟随效应)          │  │
│  │    Layer 3: 极化检测 (反转信号)          │  │
│  └─────────────────────────────────────────┘  │
│                    ↓                           │
│  ┌─────────────────────────────────────────┐  │
│  │  Flask REST API + ECharts 前端           │  │
│  └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

---

## 二、当前数据状态（严重不足！）

### 已有数据

| 数据 | 表名 | 覆盖范围 | 条数 | 备注 |
|------|------|---------|------|------|
| LC0日K | `prices` | 2023-07~2026-07 | 732 | ✅ 最全 |
| 5个月份合约 | `contract_prices` | 2025-08~2026-07 | 946 | LC2608~LC2612 |
| 月差(5对) | `spreads` | 2026-05~2026-07 | 300 | 从contract计算 |
| 社会库存 | `inventory_history` | 2026-04~2026-07 | 67 | 仅社会总库存 |
| 小作文/情绪 | `notes` | 2026-05~2026-06 | **5** | ⚠️ 极缺！ |

### ❌ 缺失数据（按重要性排序）

| # | 缺失数据 | 影响Agent | 来源建议 |
|---|---------|-----------|---------|
| 1 | **现货价格** (电池级/工业级) | merchant, arbitrage, smelter | SMM/Mysteel/AsianMetal |
| 2 | **冶炼利润** (盐湖/矿石/一体化成本) | smelter 🔴 | SMM/百川盈福 |
| 3 | **小作文/新闻情绪（大量）** | retail, policy, merchant 🔴 | SMM/我的钢铁网 |
| 4 | **分环节库存** (工厂/贸易商/下游) | merchant, smelter | Mysteel/百川盈福 |
| 5 | **碳酸锂产量+产能利用率** | smelter, resource, policy | 安泰科/百川盈福 |
| 6 | **新能源车销量+电池装机** | policy | 中汽协/乘联会 |
| 7 | **进出口量** | policy, arbitrage | 海关总署 |
| 8 | **广期所持仓排名** | institution, retail | 广期所/东方财富 |
| 9 | **锂矿成本线** | resource | SMM |
| 10 | **副产品收益** | smelter | Mysteel |

> **核心结论**: 当前系统只能基于 **K线+月差** 做非常有限的评分。  
> 缺少现货、利润、情绪、库存等核心因子，7个Agent的评分**代表性和准确度极低**。

---

## 三、文件清单

### 核心文件

```
/home/ubuntu/lithium_calendar/
├── app.py                      # Flask 主应用
├── lithium_api.py              # REST API 端点
├── lithium_agents.py           # 前端页面渲染
├── lithium.db                  # ⭐ Flask 使用的数据库
├── static/
│   └── battlefield_agents.html # 博弈 Dashboard 页面
├── backfill_agent_history.py   # 历史回推脚本（已执行）
├── expand_spreads.py           # 月差扩充脚本（已执行）
├── supervisor.conf             # Supervisor 配置
└── logs/error.log              # 错误日志
```

### 后端脚本

```
/home/ubuntu/.hermes/scripts/lithium_agents/
├── agent_daily_update.py       # ⭐ 每日更新主脚本（评分引擎+博弈因子集成）
├── game_factors.py             # ⭐ 博弈因子模块（三层影响计算）
└── agent_daily_update.py.backup_20260730_game_factors  # 备份
```

### 文档

```
/home/ubuntu/lithium_calendar/docs/
├── handover_2026_07_30_v2.md   # 上一版交接文档
├── user_manual_v1.md           # 用户说明书（图表解读）
└── handover_v3.md              # 本文件
```

### 部署

```
Supervisor: lithium_calendar (port 8766)
Config: /home/ubuntu/lithium_calendar/supervisor.conf
Restart: /home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar
```

---

## 四、数据库表结构完整说明

### 数据源表

#### `prices` — LC0主力连续日K
| 列 | 类型 | 说明 |
|----|------|------|
| date | TEXT | 日期 |
| open/high/low/close | REAL | OHLC |
| volume | INTEGER | 成交量 |
| position | INTEGER | 持仓量(OI) |
| settle | REAL | 结算价 |

#### `contract_prices` — 各合约价格
| 列 | 类型 | 说明 |
|----|------|------|
| date | TEXT | 日期 |
| contract | TEXT | 合约代码(LC2608等) |
| open/high/low/close/settle | REAL | OHLC结算 |
| volume/position | INTEGER | 成交量持仓量 |

#### `spreads` — 月差
| 列 | 类型 | 说明 |
|----|------|------|
| date | TEXT | 日期 |
| near_contract/far_contract | TEXT | 近月/远月合约 |
| spread | REAL | 远月-近月 |

#### `inventory_history` — 社会库存
| 列 | 类型 | 说明 |
|----|------|------|
| date | TEXT | 日期 |
| inventory | INTEGER | 库存量(吨) |
| change | INTEGER | 周变化量 |
| source | TEXT | 数据来源 |

#### `notes` — 小作文/情绪
| 列 | 类型 | 说明 |
|----|------|------|
| date | TEXT | 日期 |
| title | TEXT | 标题 |
| content | TEXT | 内容 |
| sentiment | TEXT | 利多/利空/中性 |
| tags | TEXT | 标签 |

### Agent表

#### `agents` — Agent实时状态
| 列 | 类型 | 说明 |
|----|------|------|
| agent_id | TEXT | 唯一标识 |
| name/role | TEXT | 名称/角色 |
| capital | REAL | 资金量 |
| position | INTEGER | 持仓(正=多,负=空) |
| avg_cost | REAL | 均价 |
| view_score | INTEGER | 观点分(-100~100) |
| view_text | TEXT | 观点文字 |
| needs_review | INTEGER | 待审核标记 |
| risk_params | TEXT | 风控参数(JSON) |
| pnl_unreal | REAL | 未实现盈亏 |

#### `agent_history` — 每日快照
| 列 | 类型 | 说明 |
|----|------|------|
| agent_id/date | TEXT | 唯一键 |
| position | INTEGER | 持仓 |
| avg_cost | REAL | 均价 |
| view_score | INTEGER | 观点分 |
| view_text | TEXT | 观点文字 |
| pnl_unreal/close | REAL | 盈亏/收盘价 |

#### `agent_game_factors` — 博弈因子
| 列 | 类型 | 说明 |
|----|------|------|
| date/agent_id | TEXT | 唯一键 |
| base_score | INTEGER | 基础评分 |
| info_transfer | REAL | 信息传递修正 |
| behavioral_impact | REAL | 行为影响修正 |
| total_correction | REAL | 总修正 |
| corrected_score | INTEGER | 修正后评分 |
| factors_detail | TEXT | 详细JSON |

#### `agent_interactions` — Agent互动关系
| 列 | 类型 | 说明 |
|----|------|------|
| date/from_agent/to_agent/influence_type | TEXT | 唯一键 |
| magnitude | REAL | 强度 |

#### `logic_scores` — 7维逻辑评分
| 列 | 类型 | 说明 |
|----|------|------|
| date/logic_name | TEXT | 唯一键 |
| avg_score/min_score/max_score | REAL | 均值/最小/最大 |
| agent_count | INTEGER | 参与Agent数 |

### Agent逻辑评分映射（7维）
| 维度 | key | 主导Agent |
|------|-----|-----------|
| 供给风险 | supply_risk | smelter, resource |
| 需求下滑 | demand_drop | merchant, institution |
| 库存 | inventory | merchant, arbitrage |
| 宏观政策 | macro_policy | policy |
| 成本支撑 | cost_support | smelter, resource |
| 情绪 | sentiment | retail |
| 基差月差 | basis_trade | arbitrage, merchant |

---

## 五、7大Agent代码级实现

### 每个Agent的评分函数位于 `agent_daily_update.py` 的 `compute_agent_score()`

#### 1. 冶炼厂 (smelter)
```python
# 输入: daily_chg_pct, five_day_chg_pct, volume_today, inv_trend, spread
# 逻辑:
#   - 日涨 > 2% → +15
#   - 去库 → +20
#   - 窄幅震荡 → +5 (供给刚性)
# 范围: [-60, 80] 长期偏多
# 最大仓位: 200, 止损: 8%, 止盈: 15%
```

#### 2. 贸易商/加工商 (merchant) ⚠️ 薄弱
```python
# 输入: spread_2609_2610, news_sentiment, daily_chg_pct
# 逻辑:
#   - 远月贴水 > 500 → +25 (持有现货有利)
#   - 远月升水 > 300 → -25 (持货成本高)
#   - 小作文情绪 > 0.3 → +15
#   - 日涨 > 2% → -10 (大涨获利了结)
#   - 日跌 > 2% → +10 (大跌逢低补货)
# 范围: [-50, 60]
# 最大仓位: 150, 止损: 6%, 止盈: 12%
# 问题: 无库存因子, 无基差因子, 无季节性
```

#### 3. 纯投机构 (institution)
```python
# 输入: daily_chg_pct, five_day_chg_pct, oi_trend, volume
# 逻辑:
#   - 量价齐升(daily>+1.5%, OI↑) → +40
#   - 放量下跌(daily<-1.5%, OI↑) → -40
#   - 增涨减仓 → +5
#   - 减仓下跌 → +10 (可能见底)
#   - 5日动量 > 5% → +15
# 范围: [-70, 70]
# 最大仓位: 300, 止损: 10%, 止盈: 20%
```

#### 4. 套利商 (arbitrage)
```python
# 输入: spread_2609_2610, daily_chg_pct
# 逻辑:
#   - 远月深度贴水 < -800 → +10 (正向套利)
#   - 远月大幅升水 > 500 → -10
#   - 单日波动 > 3% → +5 (套利窗口)
# 范围: [-20, 20] 中性倾向
# 最大仓位: 100, 止损: 3%, 止盈: 8%
```

#### 5. 散户/游资 (retail)
```python
# 输入: daily_chg_pct, news_sentiment, volume_today
# 逻辑:
#   - 日涨 > 2% → +25 (追多)
#   - 日跌 > 2% → -25 (追空)
#   - 情绪 > 0.4 → +15
#   - 放量上涨(vol>25万) → +10
# 范围: [-60, 60]
# 最大仓位: 50, 止损: 15%, 止盈: 25% (最激进出入)
```

#### 6. 锂矿/资源商 (resource)
```python
# 输入: close_price, inv_trend
# 逻辑:
#   - 价格接近成本线(125000, ±5%) → +30 (减产预期)
#   - 价格远超成本线(+20%) → -20
#   - 去库 → +10
# 范围: [-50, 70] 长期偏多
# 最大仓位: 250, 止损: 5%, 止盈: 10%
# 问题: 成本线硬编码, 无产能利用率
```

#### 7. 政策/宏观 (policy) ⚠️ 最薄弱
```python
# 输入: notes, close_price
# 逻辑:
#   - 关键词匹配(两会/发改委/新能源等) → 看sentiment加权
#   - 价格 > 160000 → -20 (政策干预风险)
#   - 价格 < 130000 → +10 (政策托底预期)
# 范围: [-60, 60]
# 最大仓位: 500, 止损: 12%, 止盈: 20%
# 问题: 无销量数据, 无进出口, notes只有5条
```

### 博弈因子 (game_factors.py)

```python
# Layer 1: 信息传递 — 与大多数人反向 → 扣分
#   info_correction = -(反向资金占比 × 敏感度 × 8)
#   敏感度: retail=0.9, institution=0.7, arbitrage=0.6, merchant=0.5,
#           smelter=0.3, resource=0.3, policy=0.2

# Layer 2: 行为影响 — 与大多数人同向增仓 → 加分
#   beh_correction = (同向资金占比 × 动量 × 敏感度 × 5)

# Layer 3: 极化检测 — 多空比>3:1 且 分歧>30 → 反转信号
#   多数派减5分, 少数派加5分
```

---

## 六、API端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/battlefield` | GET | 仪表盘主数据 (agents + history + decisions) |
| `/api/logic-scores?days=30` | GET | 7维逻辑强度 |
| `/api/agent-interactions?days=30` | GET | 互动关系 |
| `/api/battlefield/game-factors` | GET | 博弈因子 |
| `/api/term-structure` | GET | 期限结构+月差 |
| `/api/prices` | GET | K线数据 |

---

## 七、回退/排障

| 问题 | 原因 | 解决 |
|------|------|------|
| 前端空白/图表不显示 | API 500 | `tail -50 logs/error.log` |
| 数据全空 | 表不存在 | 运行一次 `agent_daily_update.py` |
| 甘特图数据少 | API LIMIT 限制 | 已改 LIMIT 500 (2026-07-30) |
| spreads为空 | 未执行expand_spreads | 只执行一次 |
| notes只有5条 | 数据缺失 | 需要外部导入 |
| 库存只有67条 | 数据缺失 | 需要cron增量导入 |
| 逻辑热力图看不清 | 评分值太小/全0 | 数据源不足导致 |

---

## 八、快速命令参考

```bash
# 重启服务
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar

# 运行每日更新
/home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py

# 检查错误日志
tail -50 /home/ubuntu/lithium_calendar/logs/error.log

# 检查API
curl -s http://127.0.0.1:8766/api/logic-scores?days=30 | python -m json.tool

# 检查数据库状态
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/ubuntu/lithium_calendar/lithium.db')
for t in ['prices','contract_prices','inventory_history','spreads','notes','agent_history','agent_game_factors','logic_scores']:
    c = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t}: {c}')
conn.close()
"
```

---

## 九、待做事项（优先级排序）

### 🚨 紧急：数据接入（必须做）

1. **创建新的数据库表** — 现货、利润、产量、销量、进出口、持仓排名等
2. **编写数据导入脚本** — Excel → DB 的增量导入
3. **改造评分引擎** — 让Agent使用新数据源
4. **重新回推历史** — 数据接入后回推至少1年

### Phase 6 参数回测（P2）
- 不同止损阈值(3%/5%/8%) + 调仓频率的PnL模拟
- 需要足够数据后才能做
- 数据接入完成后立即执行

### Phase 8 主页联动（P3）
- 碳酸锂主页加博弈Dashboard跳转链接
- 5分钟搞定

### 可选优化
- Phase 7 LLM审核（ROI低，可跳过）
- Agent评分函数优化（特别是merchant和policy）
- 添加更多博弈维度

---

## 十、关键文件修改记录

| 日期 | 文件 | 修改 |
|------|------|------|
| 2026-07-30 | `lithium_api.py` | `agent_history` LIMIT 100→500 |
| 2026-07-30 | `agent_daily_update.py` | 集成博弈因子+逻辑评分写入 |
| 2026-07-30 | `game_factors.py` | 三层博弈因子完整实现 |
| 2026-07-30 | `backfill_agent_history.py` | 历史回推(2026-07-01~29) |
| 2026-07-30 | `expand_spreads.py` | 月差扩充(2026-05-06~29) |
| 2026-07-31 | `user_manual_v1.md` | 用户说明书(12个图表解读) |

---

## 十一、新对话中需要注意的事

### 给下一个AI的指引

```
1. 用户会在对话中提供多个Excel文件，包含碳酸锂相关的缺失数据
2. 需要先分析每个Excel包含什么数据，确定要新建哪些表
3. 编写数据导入脚本（Excel → SQLite）
4. 改造 agent_daily_update.py 的 compute_agent_score() 使用新数据源
5. 回推至少1年历史数据
6. 重新运行博弈因子计算
7. 最后继续完成 Phase 6 参数回测

数据库路径: /home/ubuntu/lithium_calendar/lithium.db
脚本路径: /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py
前端: /home/ubuntu/lithium_calendar/static/battlefield_agents.html
服务端口: 8766
```

### 评分引擎改造要点

当前 `compute_agent_score()` 中每个Agent的输入参数：
```python
# 当前可用输入
daily_chg_pct, five_day_chg_pct, volume_today, oi_trend,
spread_2609_2610, spread_2611_2612,
inv_trend, news_sentiment

# 改造后需要新增
spot_price_battery, spot_price_industrial,  # 现货
smelter_profit_salarite, smelter_profit_ore,  # 冶炼利润
mining_cost_line,  # 成本线
production_output, capacity_utilization,  # 产量/产能
ev_sales, battery_installation,  # 销量
import_volume, export_volume,  # 进出口
holding_rank_net_long,  # 持仓排名
factory_inventory, merchant_inventory, downstream_inventory  # 分环节库存
```

### 重要提醒

- **DB路径**: `/home/ubuntu/lithium_calendar/lithium.db` （不是 `lithium_v2_*.db`）
- **venv**: `/home/ubuntu/zinc_venv` (用 `zinc_venv/bin/python`)
- **表创建**: `CREATE TABLE IF NOT EXISTS`，SQLite不会自动ALTER
- **回推脚本**: `backfill_agent_history.py` 已写好，可直接用
- **不要重复执行** `expand_spreads.py`（会清空数据）
- **博弈因子模块**: `game_factors.py` 是独立模块，import即可
- **Agent状态连贯**: 回推时position/avg_cost/score要传递到下一天
- **pnl_unreal**: agents表需要这个列（Phase 1建表时遗漏了）

---

## 十二、数据源建议

| 数据类型 | 推荐来源 | 频率 |
|---------|---------|------|
| 现货价格 | SMM / Asian Metal | 日/周 |
| 冶炼利润 | SMM / Mysteel | 周 |
| 库存(分环节) | Mysteel / 百川盈福 | 周 |
| 产量 | 安泰科 / 百川盈福 | 月 |
| 销量 | 中汽协 / 乘联会 | 月 |
| 进出口 | 海关总署 | 月 |
| 持仓排名 | 广期所 / 东方财富 | 日 |
| 小作文/新闻 | SMM资讯 / 我的钢铁网 | 日 |
