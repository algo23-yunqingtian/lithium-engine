# Phase 4 & 5 交接文档

**日期**: 2026-07-30
**状态**: ✅ Phase 4 已完成并部署 | ✅ Phase 5 已完成并部署
**URL**: http://124.221.113.37:8766/battlefield_agents.html

---

## Phase 4 — 交易逻辑强度分析（已完成）

### 新增功能
在博弈 Dashboard 底部新增区块：
1. **LC0 日K线图**（OHLCV + 成交量）
2. **7维逻辑强度时序线**（供给风险/需求下滑/库存/政策/成本/情绪/基差）
3. **逻辑热力图**（日期 × 7维度）

### 修改文件
- `agent_daily_update.py` — `compute_agent_score()` 返回 `logic_scores` dict；`_compute_logic_scores()` 计算7维评分；`run_daily_update()` 汇总写入 `logic_scores` 表
- `lithium_api.py` — 新增 `logic_scores` 表；新增 API `/api/logic-scores`
- `battlefield_agents.html` — 新增3个 ECharts 图表（kline, logic-line, logic-heatmap）

### 数据
- `logic_scores` 表：每天7条记录（每个维度1条均值）
- 需每天运行 `agent_daily_update.py` 积累数据

---

## Phase 5 — 博弈互动矩阵（已完成）

### 新增功能
在逻辑强度区块下方新增"博弈互动矩阵"区块：
1. **Agent 互动热力图**（日期 × Agent，显示影响强度）
2. **影响力网络图**（力导向图，红色=信息冲突，绿色=行为跟随）

### 修改文件
- `game_factors.py` — `apply_game_factors()` 中新增互动关系记录逻辑
  - Layer 1: 信息传递冲突（方向相反的 agent 间）
  - Layer 2: 行为跟随效应（同方向仓位变动的 agent 间）
- `lithium_api.py` — 新增 `agent_interactions` 表；新增 API `/api/agent-interactions`
- `backfill_interactions_v2.py` — 从 `agent_action_log` 回推历史互动关系
- `battlefield_agents.html` — 新增2个 ECharts 图表（interaction-heatmap, interaction-network）

### 数据
- `agent_interactions` 表: (date, from_agent, to_agent, influence_type, magnitude)
- 已回推 7月30日 28 条互动记录
- 后续每天 `agent_daily_update.py` 自动记录

### 互动类型
- `info_conflict`: 信息冲突（看多看空方向相反）— 红色曲线
- `behavior_follow`: 行为跟随（同方向仓位变动）— 绿色直线

---

## 完整页面结构

```
┌─────────────────────────────────────────────────┐
│  摘要卡片（最新价/多方/空方/净多头寸/平均观点）    │
├─────────────────────────────────────────────────┤
│  7大Agent状态卡片（观点分/持仓/浮盈亏/止损）        │
├─────────────────────────────────────────────────┤
│  持仓对比饼图                                    │
│  多空力量对比柱图                                 │
│  观点评分分布柱图                                 │
├─────────────────────────────────────────────────┤
│  历史观点甘特图                                    │
├─────────────────────────────────────────────────┤
│  交易逻辑强度分析                                  │
│  ├── LC0 日K线                                   │
│  ├── 7维逻辑强度时序线                             │
│  └── 逻辑热力图                                   │
├─────────────────────────────────────────────────┤
│  博弈互动矩阵（Phase 5）                           │
│  ├── Agent互动热力图（日期×Agent）                  │
│  └── 影响力网络图                                  │
├─────────────────────────────────────────────────┤
│  博弈因子分析                                      │
│  ├── 极化检测                                      │
│  ├── 博弈热力图                                    │
│  └── 修正幅度趋势                                  │
└─────────────────────────────────────────────────┘
```

---

## API 端点

| 端点 | 说明 |
|------|------|
| `/api/battlefield` | Agent 实时状态 |
| `/api/battlefield/history?days=N` | Agent 历史 |
| `/api/battlefield/game-factors` | 博弈因子 |
| `/api/logic-scores?days=N` | 7维逻辑评分 |
| `/api/agent-interactions?days=N` | Agent 互动矩阵 |
| `/api/prices` | K线数据 |
| `/api/inventory` | 库存数据 |

---

## 待做（Phase 6-8）

### Phase 6 — 参数调优与回测（P2，需≥10天数据）
- 回测不同止损阈值/调仓频率的 PnL
- 自动推荐最优参数
- 回测用已有 K线数据即可

### Phase 7 — LLM 批量审核（P4，低ROI可跳过）
- 对 `needs_review` 标记的 agent 自动 LLM 审核
- 可跳过，ROI低

### Phase 8 — 主页联动（P3，0.5h）
- 在碳酸锂主页增加博弈 Dashboard 入口
- 底部导航增加跳转链接

---

## 注意事项

1. **两数据库**: `lithium.db` (Flask用) 和 `lithium_v2_20260730.db` (备用)。确保修改写入正确的库。
2. **notes 变量**: `_compute_logic_scores` 新增 `notes` 参数，需在 `compute_agent_score` 调用时传入
3. **print 格式**: `{int(score):4d}` 避免 float 格式化错误
4. **logic_scores/agent_interactions 表**: 在脚本中创建（`CREATE TABLE IF NOT EXISTS`）
5. **ECharts 隔离**: 每次 render 前 `dispose()` + `innerHTML=''`，防止跨页面 canvas 缓存
6. **backfill**: 回推脚本只跑一次，后续每天由 `agent_daily_update.py` 自动记录
