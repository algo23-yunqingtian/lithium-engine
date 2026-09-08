# Phase 4 交接文档 - 交易逻辑强度分析

**日期**: 2026-07-30
**状态**: ✅ 已完成并部署
**URL**: http://124.221.113.37:8766/battlefield_agents.html（底部新增区块）

---

## 做了什么

### 新增功能：交易逻辑强度分析（K线图 + 逻辑时序线 + 热力图）

在博弈 Dashboard 的甘特图下方新增一个区块，包含：

1. **LC0 日K线图** — 最近60天OHLCV，含成交量柱状图
2. **7维逻辑强度时序线** — 7条线分别代表：供给风险、需求下滑、库存变化、宏观政策、成本支撑、情绪/小作文、基差月差
3. **逻辑热力图** — 日期 × 7维度热力图，展示强度分布

### 后端修改

#### 1. `agent_daily_update.py`
- `compute_agent_score()` 返回值新增 `logic_scores` dict（7维 -100~100）
- `_compute_logic_scores(agent_id, ..., notes)` — 按agent角色分配逻辑评分
  - smelter/resource/merchant → supply_risk, inventory, basis_trade, cost_support 为主
  - retail/sentiment → sentiment 为主
  - policy → macro_policy 为主
  - arbitrage → basis_trade 为主
  - institution → demand_drop, macro_policy 为主
- `run_daily_update()` — 汇总当日各维度的 avg/min/max 写入 `logic_scores` 表

#### 2. `lithium_api.py`
- 新增表 `logic_scores` (date, logic_name, avg_score, min_score, max_score, agent_count)
- 新增 API `/api/logic-scores?days=N` — 返回7维时序JSON

### 前端修改

#### 3. `battlefield_agents.html`
- 新增 3 个 ECharts 图表渲染函数
- 新增 `loadLogicScores()` + `loadKlineData()` 
- 图表 resize 适配
- 数据不足时显示友好提示

---

## 数据现状

- **logic_scores**: 仅 2026-07-30 单天数据（首次运行）
- **agent_game_factors**: 仅 2026-07-30 单天
- **prices (K线)**: 732条，2023-07-21 ~ 2026-07-29
- 图表需积累多天数据才能看出趋势

每天运行 `agent_daily_update.py` 即可自动积累数据。

---

## 下一步：Phase 5 — 博弈互动矩阵

### 目标
- 追踪 agent 之间的影响力（谁影响谁、影响多少）
- 可视化：互动热力图 + 影响力网络图
- 需要 ≥5 天历史互动数据

### 技术方案
- `agent_interactions` 表：(date, from_agent, to_agent, influence_type, magnitude)
- 在 `apply_game_factors()` 中记录每对 agent 的影响
- 前端新增 2 个图表：互动热力图 + 桑基图/力导向图
- 需补充历史互动数据（可从 agent_action_log 推断）

### 数据依赖
- 需 ≥5 天 `agent_game_factors` 历史数据（当前仅1天）
- 可用 `agent_action_log` 和 `agent_view_log` 中的 position/score 变化来推断互动

---

## 注意事项

1. **notes 变量**: `_compute_logic_scores` 新增 `notes` 参数，需在 `compute_agent_score` 调用时传入
2. **print 格式**: `{int(score):4d}` 避免 float 格式化错误
3. **logic_scores 表**: 在脚本中创建（`CREATE TABLE IF NOT EXISTS`），非 Flask 初始化
4. **API 回退**: 如果 logic_scores 表为空，API 会返回空 dates 数组
5. **ECharts 隔离**: 每次 render 前 `dispose()` + `innerHTML=''`，防止跨页面 canvas 缓存
