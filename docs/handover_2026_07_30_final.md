# 碳酸锂多主体博弈项目 — 交接文档

**最后更新**: 2026-07-30 13:45
**部署地址**: http://124.221.113.37:8766/battlefield_agents.html

---

## 一、项目已完成（Phase 1-5.5）

### Phase 1-3.6 — 基础系统（已完成）
- 7大Agent：smelter(冶炼厂), resource(资源商), merchant(贸易商), institution(机构), arbitrage(套利商), retail(散户), policy(政策)
- 规则引擎：基于供需/技术面/政策面计算观点分和仓位
- 每日更新脚本 `agent_daily_update.py`
- K线图、持仓对比、多空力量对比、观点评分分布、历史观点甘特图

### Phase 4 — 交易逻辑强度分析（✅ 已完成）
**页面区块**: 甘特图下方

| 图表 | 说明 | 数据源 |
|------|------|--------|
| LC0 日K线 | OHLCV + 成交量，最近60天 | `/api/prices` |
| 7维逻辑强度时序线 | 供给风险/需求下滑/库存/政策/成本/情绪/基差 | `/api/logic-scores` |
| 逻辑热力图 | 日期 × 7维度 | `/api/logic-scores` |

### Phase 5 — 博弈互动矩阵（✅ 已完成）
**页面区块**: 逻辑强度区块下方

| 图表 | 说明 | 数据源 |
|------|------|--------|
| Agent互动热力图 | 日期 × Agent，颜色=影响强度 | `/api/agent-interactions` |
| 影响力网络图 | 力导向图，红=冲突绿=跟随 | `/api/agent-interactions` |

**互动类型:**
- `info_conflict`: 信息冲突（看多看空方向相反）→ 红色曲线
- `behavior_follow`: 行为跟随（同方向仓位变动）→ 绿色直线

---

## 二、待做（Phase 6-8）

### Phase 6 — 参数调优与回测（P2）
**前置条件**: ≥10天数据（目前只有1天：7月30日）
**可用数据**: K线数据有 732 条（2023-07-21 ~ 2026-07-29），可用于价格回测

**需做:**
1. 回测不同止损阈值（3%/5%/8%）和调仓频率（每日/隔日/信号触发）的 PnL
2. 用历史 K线数据 + 已有 agent 观点信号做模拟交易
3. 输出最优参数 + 收益曲线
4. 新增页面或嵌入现有页面展示

### Phase 7 — LLM 批量审核（P4，低ROI可跳过）
**需做:**
1. 对 `needs_review=1` 的 agent 自动 LLM 审核
2. 检查信号合理性、是否受噪声干扰
3. ROI低，建议跳过

### Phase 8 — 主页联动（P3，0.5h）
**需做:**
1. 在碳酸锂主页 `http://124.221.113.37:8766/` 增加博弈 Dashboard 入口
2. 底部导航增加跳转链接

---

## 三、数据状态

### 当前数据
| 表 | 记录数 | 日期范围 | 说明 |
|----|--------|----------|------|
| prices | 732 | 2023-07-21 ~ 2026-07-29 | K线，充足 |
| inventory_history | 67 | — | 库存 |
| agents | 7 | — | Agent配置 |
| agent_view_log | 21 | 仅 2026-07-30 | 每日评分 |
| agent_game_factors | 7 | 仅 2026-07-30 | 博弈修正 |
| logic_scores | 7 | 仅 2026-07-30 | 7维逻辑评分 |
| agent_interactions | 28 | 仅 2026-07-30 | 互动关系 |
| agent_action_log | 23 | 仅 2026-07-30 | 动作日志 |

**关键限制**: 只有7月30日当天的 agent 互动数据。Phase 6 回测可用 K线数据 + agent 信号，Phase 5/8 需要更多天互动数据。

### 数据积累方式
- 每天运行 `agent_daily_update.py` → 自动写入 `logic_scores` + `agent_interactions` + `agent_game_factors`
- K线数据由 akshare 抓取，每天积累
- 库存数据由 cron 增量导入

---

## 四、文件清单

### 核心文件
```
/home/ubuntu/lithium_calendar/
├── app.py                    # Flask 主应用
├── lithium_api.py            # REST API 端点
├── lithium_agents.py         # 前端主页面渲染
├── lithium.db                # Flask 使用的数据库 ⚠️
├── backfill_interactions_v2.py  # 回推互动数据（跑一次）
├── static/
│   └── battlefield_agents.html  # 博弈 Dashboard 页面（主前端）
├── data/                     # 数据目录
├── logs/
│   └── error.log             # 错误日志
└── docs/
    └── handover_2026_07_30_phase4_5.md  # 详细交接
```

### 后端脚本
```
/home/ubuntu/.hermes/scripts/lithium_agents/
├── agent_daily_update.py     # 每日更新主脚本 ⭐ 修改过
├── game_factors.py           # 博弈因子计算 + 互动记录 ⭐ 修改过
└── ... (其他辅助脚本)
```

### 部署
```
Supervisor: lithium_calendar (port 8766)
Config: /home/ubuntu/lithium_calendar/supervisor.conf
Restart: supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar
```

---

## 五、技术注意事项

### 数据库
- **Flask 使用**: `lithium.db`（`/home/ubuntu/lithium_calendar/lithium.db`）
- **备用**: `lithium_v2_20260730.db`（不要混淆！）
- 建表用 `CREATE TABLE IF NOT EXISTS`，不要在 Flask 初始化中创建（`init_minute_tables`）

### 代码修改要点
1. **`agent_daily_update.py`**:
   - `compute_agent_score()` 返回 `logic_scores` dict + 7维评分
   - `_compute_logic_scores()` 新增 `notes` 参数
   - `run_daily_update()` 汇总写入 `logic_scores` 表
   - print 格式用 `{int(score):4d}` 避免 float 报错

2. **`game_factors.py`**:
   - `apply_game_factors()` 中新增互动关系记录
   - 在写入 `agent_game_factors` 之前记录 `agent_interactions`

3. **`lithium_api.py`**:
   - API 读取 `lithium.db`（DB_PATH 常量）
   - 新增 `/api/logic-scores` 和 `/api/agent-interactions`

4. **`battlefield_agents.html`**:
   - 6个 ECharts 图表：position pie, force force, score bar, gantt, gameFactor bar, correction line
   - Phase 4 新增：kline, logic-line, logic-heatmap
   - Phase 5 新增：interaction-heatmap, interaction-network
   - 每次 render 前 `dispose()` + `innerHTML=''` 防止 canvas 缓存
   - window resize 事件已覆盖所有 10 个 chart 实例

### 回退
- 如果前端报错：检查浏览器控制台，ECharts 初始化失败通常是 DOM 元素不存在
- 如果 API 500：检查 `logs/error.log`，通常表不存在 → 运行一次 `agent_daily_update.py`
- 数据空：运行 `backfill_interactions_v2.py` 回推（只跑一次）

---

## 六、后续建议

1. **优先级**: Phase 6(回测) > Phase 8(主页联动) > Phase 7(LLM审核，可跳过)
2. **Phase 6 回测**: 可以先用现有 K线数据 + agent 观点信号做，不等互动数据
3. **自动化**: 考虑将 `agent_daily_update.py` 加入 cron，每天自动运行
4. **Phase 8**: 最简单，只需加链接，建议尽早完成

---

## 七、快速命令参考

```bash
# 重启服务
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar

# 运行每日更新
/home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py

# 检查错误日志
tail -50 /home/ubuntu/lithium_calendar/logs/error.log

# 检查 API
curl -s http://127.0.0.1:8766/api/logic-scores?days=30 | python -m json.tool
curl -s http://127.0.0.1:8766/api/agent-interactions?days=30 | python -m json.tool

# 回推互动数据（只跑一次）
python3 /home/ubuntu/lithium_calendar/backfill_interactions_v2.py
```
