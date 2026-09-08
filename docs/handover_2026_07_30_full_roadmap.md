# 碳酸锂多主体博弈 — 完整路线图与交接文档

> 2026-07-30 更新 · 供后续会话使用
> 项目地址: `http://124.221.113.37:8766/`
> 代码根目录: `/home/ubuntu/lithium_calendar/`

---

## 一、已完成内容

### ✅ Phase 1 — 数据库+Agent框架
- 7张核心表 + 7个Agent初始化（smelter/merchant/institution/arbitrage/retail/resource/policy）
- API端点 `/api/battlefield` 返回agents+history+decisions+custom_data+latest_price
- 向后兼容旧版participants数据

### ✅ Phase 2 — 每日更新脚本+Cron
- 规则引擎 `compute_agent_score()` 每日自动跑
- Cron任务: 工作日18:30自动执行（脚本: `~/.hermes/scripts/lithium_agents/agent_daily_update.py`）
- `agent_game_factors` 表已加入每日更新流程

### ✅ Phase 3 — 前端博弈可视化
- `battlefield_agents.html`: 7个agent卡片+4个图表（持仓/多空/评分/甘特图）
- ECharts跨页面隔离（dispose+clear方案）
- 30s自动刷新
- 反复制保护

### ✅ Phase 3.5 — 博弈因子体系v1.0
- `game_factors.py`: 3层博弈机制（信息传递/行为影响/极化检测）
- 敏感度配置（各agent独立参数）
- API `/api/battlefield/game-factors` 返回因子数据
- 前端新增：博弈因子热力图 + 极化检测警示条 + 修正幅度趋势图

### ✅ Phase 3.6 — 前端博弈可视化增强（本次新增）
- 博弈因子5列×7行热力图
- 三色极化警示条（红=极化/黄=较大修正/蓝=正常）
- 4线修正幅度趋势图（最大修正|信息|行为+观点分歧）

---

## 二、未完成路线图（按优先级排序）

### 🔴 Phase 4 — 交易逻辑甘特图（独立新页面）

**目标**: 从"agent视角"切换到"交易逻辑视角"，做7个逻辑维度的时序热力图。

**工作量**: 约3-4小时
**数据依赖**: 当前无logic_scores数据，需先在后端生成

#### 4.1 后端改造（~1小时）
1. 在 `compute_agent_score()` 中增加 `logic_scores` dict 输出
2. 每个agent映射到7个逻辑维度：
   | 逻辑ID | 名称 | 对应agent | 计算方式 |
   |--------|------|-----------|----------|
   | supply_risk | 供给风险 | smelter + resource | 冶炼利润×0.6 + 成本线×0.4 |
   | demand_drop | 需求下滑 | merchant + institution | 采购量×0.5 + 持仓变化×0.5 |
   | inventory | 库存变化 | merchant + arbitrage | 社库趋势×0.5 + 交易所库×0.5 |
   | macro_policy | 宏观政策 | policy | 政策事件×权重 |
   | cost_support | 成本支撑 | smelter + resource | 盐湖/矿石成本线×0.4 + 冶炼利润×0.6 |
   | sentiment | 情绪/小作文 | retail | 新闻情绪分直接映射 |
   | basis_trade | 基差月差 | arbitrage + merchant | 期限结构评分 |
3. 写入 `agent_view_log.key_indicators`（已有字段，存JSON）
4. 新增API `/api/logic_scores?days=N` 返回每日7逻辑评分时序
5. 汇总逻辑: 当日各agent对同一逻辑评分取平均

#### 4.2 前端新页面（~2-3小时）
新建 `/home/ubuntu/lithium_calendar/static/lithium_logic_gantt.html`
- 顶部: K线图（价格+成交量，ECharts）
- 中间: 7逻辑强度时序线（ECharts line chart，7条线不同颜色）
- 底部: 逻辑热力图（日期×7逻辑，heatmap）
- 交互: 悬停显示当日详细解读
- 参考: 锌复盘甘特图 `zinc_review_version_B_deployed.html`

**文件结构**:
```
static/lithium_logic_gantt.html  — 新页面（~300行HTML/JS）
app.py / lithium_api.py — 新增 /api/logic_scores（~30行Python）
agent_daily_update.py — compute_agent_score 增加 logic_scores（~20行）
```

---

### 🟡 Phase 5 — 博弈互动矩阵（Layer 2增强）

**目标**: 可视化agent之间的互动关系（谁影响了谁）。

**工作量**: 约2-3小时
**数据依赖**: 需积累至少5天数据，否则互动矩阵为空

#### 5.1 后端（~1小时）
1. 新增 `agent_interactions` 表：
   ```sql
   CREATE TABLE IF NOT EXISTS agent_interactions (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       date TEXT NOT NULL,
       source_agent TEXT NOT NULL,
       target_agent TEXT NOT NULL,
       score_impact INTEGER,
       direction TEXT,  -- bull/bear/neutral
       description TEXT
   );
   ```
2. 在 `apply_game_factors()` 中记录互动到 `agent_interactions` 表
3. 新增API `/api/interactions?days=N`

#### 5.2 前端（~1-2小时）
在 `battlefield_agents.html` 新增博弈矩阵热力图：
- X轴: source_agent（7个）
- Y轴: target_agent（7个）
- 值: score_impact（红→绿）
- 0值单元格不渲染

---

### 🟡 Phase 6 — 参数调优与回测

**目标**: 验证博弈因子效果，调整敏感度参数。

**工作量**: 1-2小时
**数据依赖**: 需积累至少10-15天数据

#### 6.1 分析内容
1. 对比 `base_score` vs `corrected_score` 的差异分布
2. 哪些agent受博弈影响最大（通常是retail/institution）
3. 极化检测触发频率（目标：月度1-2次，太高说明阈值太低）
4. 修正幅度与实际价格走势的关联

#### 6.2 调整方向
- 信息敏感度：0.1~0.9，可逐步调整
- 行为敏感度：0.1~0.9，可逐步调整
- 因子上限：当前±10，可放宽到±15
- 极化阈值：当前多空比>3x且平均分>30，可调

---

### 🟢 Phase 7 — LLM批量审核（之前计划，低ROI）

**目标**: 极化博弈触发时，调用LLM对agent观点进行人工/自动审核。

**工作量**: 约2小时
**优先级**: 低，评估为低ROI

**现状**:
- `needs_review` 字段已存在
- `/api/agents/review` 端点已就位
- 需要实现LLM批量Prompt和调用

**决策**: 可跳过，先积累数据验证因子效果后再决定是否值得做

---

### 🟢 Phase 8 — 主页联动 + 导航增强

**目标**: 将新页面整合到主页导航。

**工作量**: 约30分钟

1. `index.html` 添加"交易逻辑甘特图"入口
2. `index.html` 添加"博弈因子"入口或集成到现有入口

---

## 三、当前数据状态

| 表 | 行数 | 说明 |
|---|---|---|
| agents | 7 | 7个agent初始化完成 |
| agent_history | 16 | 约2天的快照数据 |
| agent_game_factors | 7 | 仅1天（2026-07-30） |
| agent_view_log | 14 | 约2天观点记录 |
| agent_action_log | 16 | 约2天决策流水 |
| prices | 732 | 历史价格数据 |
| notes | 5 | 小作文记录 |
| agent_decisions | 0 | 空（LLM审核未跑过） |
| agent_events | 0 | 空（极化未触发） |
| agent_interactions | — | 表尚未创建 |

**关键**: 博弈因子只有1天数据，逻辑甘特图需要先在compute_agent_score中生成logic_scores数据。

---

## 四、关键文件路径

| 类型 | 路径 |
|------|------|
| 主应用 | `/home/ubuntu/lithium_calendar/app.py` |
| API | `/home/ubuntu/lithium_calendar/lithium_api.py` |
| Agent核心 | `/home/ubuntu/lithium_calendar/lithium_agents.py` |
| 每日更新 | `~/.hermes/scripts/lithium_agents/agent_daily_update.py` |
| 博弈因子 | `~/.hermes/scripts/lithium_agents/game_factors.py` |
| Shell wrapper | `~/.hermes/scripts/lithium_agents/agent_daily_update.sh` |
| 前端 | `/home/ubuntu/lithium_calendar/static/battlefield_agents.html` |
| 主页 | `/home/ubuntu/lithium_calendar/static/index.html` |
| 数据库 | `/home/ubuntu/lithium_calendar/lithium.db` |
| 设计文档 | `/home/ubuntu/lithium_calendar/docs/lithium_game_factors_design.md` |
| 实施方案 | `/home/ubuntu/lithium_calendar/docs/lithium_game_interaction_implementation.md` |
| 交互设计 | `/home/ubuntu/lithium_calendar/docs/lithium_game_interaction_design.md` |

---

## 五、服务管理

```bash
# 重启
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar

# 查看状态
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf status

# 查看日志
tail -f /home/ubuntu/lithium_calendar/logs/error.log
tail -f /home/ubuntu/lithium_calendar/logs/lithium_calendar.log
```

---

## 六、已知问题

1. `agent_decisions` 表为空 — LLM审核未实际跑过（Phase 7）
2. `agent_events` 表为空 — 极化检测从未触发（数据量不足）
3. 所有agent的 `target_long/target_short/stop_loss/take_profit` 均为0 — 未设具体价位
4. Phase 7（LLM批量审核）仅存占位接口，未实现
5. `agent_interactions` 表尚未创建（Phase 5才建）
6. 目前无 `logic_scores` 数据（Phase 4需要先在后端生成）
7. Cron任务使用系统Python，脚本需 `.sh` wrapper（已解决）

---

## 七、建议执行顺序

```
优先级高 →
  Phase 4: 逻辑甘特图（3-4h）
  Phase 5: 博弈互动矩阵（2-3h）
  Phase 6: 参数调优（1-2h，等数据积累后）
  Phase 8: 主页联动（0.5h）
优先级低 →
  Phase 7: LLM审核（2h，低ROI，可跳过）
```

**建议**: 先做Phase 4（逻辑甘特图），因为这不需要额外数据积累，直接在现有评分基础上增加logic_scores映射即可，工作量适中且输出价值高。Phase 5需要互动数据积累（至少5天），Phase 6需要回测数据（至少10天）。
