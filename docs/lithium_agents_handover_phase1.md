# 碳酸锂多主体博弈 — Phase 1 交接文档

> 2026-07-30 · Phase 1 完成后的交接文档
> 供后续对话继续开发 Phase 2+ 使用

---

## 📋 当前状态

**已完成（Phase 1）**：
1. ✅ 数据库7张表创建完成
2. ✅ 7个Agent初始化完成
3. ✅ 所有API端点可用（已全量测试通过）
4. ✅ 向后兼容：旧版 `/api/battlefield` 同时返回 participants 和 agents 数据

**未完成（待做）**：
1. ⏳ Phase 2：Agent 每日更新脚本（规则引擎）
2. ⏳ Phase 3：Battlefield 前端真实数据渲染
3. ⏳ Phase 4：LLM 批量审核
4. ⏳ Phase 5：主页联动

---

## 🗄️ 数据库结构

### 已有表（Phase 1已创建）

```sql
-- agents: 主体实时状态
agents(agent_id, name, role, capital, position, avg_cost,
       target_long, target_short, stop_loss, take_profit,
       view_score, view_text, needs_review, risk_params, updated_at)
-- 7条记录: smelter, merchant, institution, arbitrage, retail, resource, policy

-- agent_history: 每日快照
agent_history(id, agent_id, date, position, avg_cost, view_score,
              view_text, pnl_unreal, close)

-- agent_decisions: LLM/规则决策
agent_decisions(id, agent_id, date, trigger, action, delta_position,
                reason, model_used)

-- agent_events: 市场事件
agent_events(id, date, event_type, summary, severity)

-- agent_view_log: 历史观点日志
agent_view_log(id, agent_id, date, view_score, view_text,
               daily_reasoning, key_indicators, daily_actions)

-- agent_custom_data: 用户手动注入
agent_custom_data(id, agent_id, date, data_type, source, content,
                  score_impact, tags, created_at)

-- agent_action_log: 完整决策流水
agent_action_log(id, agent_id, date, prev_position, new_position, delta,
                 prev_view_score, new_view_score, trigger, reasoning,
                 close_price, pnl_unreal)
-- 注意：列名是 reasoning（不是 reason）
```

### 关键DB路径

```
/home/ubuntu/lithium_calendar/lithium.db
```

---

## 🔌 API 端点（已全部验证可用）

### 读取

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/battlefield` | GET | 主接口：返回 agents + history + decisions + custom_data + latest_price + 旧版participants |
| `/api/battlefield/history` | GET | 历史观点序列。参数: `?agent_id=smelter&days=90` |
| `/api/agents/<agent_id>` | GET | 单个agent详情。参数: `?days=30` |

### 写入

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agents/<agent_id>/update` | POST | 更新agent状态。JSON: `{position, avg_cost, view_score, view_text, trigger, reason, close_price, pnl_unreal}` |
| `/api/battlefield/custom` | POST | 注入非标数据。JSON: `{date, agent_id, data_type, source, content, score_impact, tags}` |
| `/api/agents/init` | POST | 重新初始化agent配置（幂等） |
| `/api/agents/review` | POST | 触发LLM批量审核。JSON: `{agent_ids: [...]}` |

### 测试用例（可直接复制验证）

```bash
# 更新冶炼厂状态
curl -s -X POST http://127.0.0.1:8766/api/agents/smelter/update \
  -H "Content-Type: application/json" \
  -d '{"position": 50, "avg_cost": 145000, "view_score": 65,
       "view_text": "冶炼利润改善，减产信号减弱",
       "trigger": "manual", "reason": "测试",
       "close_price": 147900, "pnl_unreal": 14500}'

# 注入非标数据
curl -s -X POST http://127.0.0.1:8766/api/battlefield/custom \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-07-30","agent_id":null,"data_type":"manual_signal",
       "source":"用户笔记","content":"某碳酸锂厂计划8月减产2000吨",
       "score_impact":15,"tags":"供应,减产"}'

# 查看历史
curl -s "http://127.0.0.1:8766/api/battlefield/history?agent_id=smelter&days=1"

# 查看单个agent
curl -s http://127.0.0.1:8766/api/agents/smelter
```

---

## 🏗️ 架构设计（已确认）

### 三层决策架构

```
┌─────────────────────────────────────────┐
│ Layer 1: 规则引擎（硬编码，no_agent跑）   │  ← 本次Phase 2要做
│  止损止盈、仓位上限、价格阈值触发          │
├─────────────────────────────────────────┤
│ Layer 2: 决策函数（YAML配置，改配置不动代码）│  ← Phase 2+
│  每个agent一个 config.yaml              │
├─────────────────────────────────────────┤
│ Layer 3: LLM 事件驱动审核（按需）         │  ← Phase 4
│  1次批量处理所有needs_review的agent       │
└─────────────────────────────────────────┘
```

### 7个Agent及其关注指标

| Agent | 核心指标 | 多空倾向 |
|-------|---------|---------|
| smelter | 冶炼利润、库存 | 利润差→减产→利多 |
| merchant | 基差、月差C/B | 基差→采购/抛售 |
| institution | 动量、量仓、情绪 | 趋势跟随 |
| arbitrage | 月差结构、回归 | 均值回归 |
| retail | 新闻、持仓排名、换手 | 追涨杀跌 |
| resource | 成本支撑、产能利用 | 成本底部 |
| policy | 宏观、需求、政策事件 | 政策驱动 |

### 数据源路由协议

```python
SOURCES = {
    'db://':         'query_db',          # 本地SQLite
    'api://news':    'fetch_smm_news',    # SMM新闻
    'api://zhiji':   'fetch_zhiji',       # 知几API
    'api://akshare': 'fetch_akshare',     # akshare
    # 后续可扩展: 'api://wind', 'api://smm_spot' 等
}
```

---

## 📁 关键文件

| 文件 | 说明 |
|------|------|
| `/home/ubuntu/lithium_calendar/app.py` | 主应用，已注册agent框架（第37-43行） |
| `/home/ubuntu/lithium_calendar/lithium_agents.py` | Agent框架核心（表初始化+默认配置） |
| `/home/ubuntu/lithium_calendar/lithium_api.py` | 已注入agent路由函数（约+150行） |
| `/home/ubuntu/lithium_calendar/lithium.db` | SQLite数据库 |
| `/home/ubuntu/lithium_calendar/supervisor.conf` | supervisord配置，进程名 `lithium_calendar` |
| `/home/ubuntu/lithium_calendar/static/index.html` | 主页前端 |
| `/home/ubuntu/lithium_calendar/static/lithium_legacy/market_battlefield.html` | 旧版博弈页面（静态占位符） |
| `/home/ubuntu/lithium_calendar/docs/lithium_task_handover.md` | 总任务清单 |
| `/home/ubuntu/lithium_calendar/docs/lithium_dashboard_enhancement_design.md` | 完整设计文档 |

---

## 🔧 重启/验证命令

```bash
# 重启服务
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar

# 查看启动日志
tail -20 /home/ubuntu/lithium_calendar/logs/lithium_calendar.log

# 查看错误日志
tail -20 /home/ubuntu/lithium_calendar/logs/error.log

# 验证服务状态
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf status
```

---

## 🎯 下一步：Phase 2 — 每日更新脚本

### 目标
创建 no_agent cron 脚本 `agent_daily_update.py`，每日18:30自动运行，驱动7个agent按各自规则更新状态。

### 执行流程
```
18:30 cron 触发
  ↓
Step 1: 读取最新数据（价格/库存/月差/小作文/合约价格）
  ↓
Step 2: 逐个agent运行规则引擎（串行，每个~2秒）
  ├── agent_smelter()     — 冶炼厂
  ├── agent_merchant()    — 贸易商
  ├── agent_institution() — 纯投机构
  ├── agent_arbitrage()   — 套利商
  ├── agent_retail()      — 散户/游资
  ├── agent_resource()    — 锂矿/资源商
  └── agent_policy()      — 政策/宏观博弈者
  ↓
Step 3: 检查止损止盈触发
  ↓
Step 4: 写入 agent_history + agent_view_log
  ↓
Step 5: 标记 needs_review → 后续LLM处理
```

### 每个agent脚本的框架模板

```python
# 核心函数签名
def run_agent(agent_id: str, latest_price: float, latest_inventory: int,
              term_structure: dict, notes: list, spreads: list) -> dict:
    """
    输入: 市场数据
    输出: {agent_id, position, avg_cost, view_score, view_text,
           needs_review, trigger, reason, pnl_unreal}
    
    内部:
    1. 读取agents表中该agent的当前状态和risk_params
    2. 读取该agent的config.yaml（如果有）
    3. 根据规则计算新的view_score和position
    4. 检查止损止盈
    5. 返回更新结果
    """
```

### 脚本存放位置
```
~/.hermes/scripts/lithium_agents/agent_daily_update.py
```

### Cron 配置方式
```yaml
# ~/.hermes/cron/ 下添加
script: lithium_agents/agent_daily_update.sh   # shell wrapper
no_agent: true                                  # 纯Python脚本，不调LLM
schedule: '30 18 * * 1-5'                       # 工作日18:30
deliver: local                                  # 输出保存到本地
```

### Shell wrapper（避免venv问题）
```bash
#!/bin/bash
exec /home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py "$@"
```

### 规则引擎核心逻辑（伪代码）
```python
def compute_score(agent, market_data):
    """
    通用评分函数：
    1. 提取该agent关心的指标
    2. 每个指标按weight加权
    3. score_func（sigmoid/分段/线性）映射到[-100, 100]
    4. 汇总 = sum(weight * indicator_score)
    5. 考虑惯性：不轻易大幅跳变
    """
```

### 数据源接入要点
- 价格：`prices` 表（akshare已写入）
- 库存：`inventory_history` 表
- 月差结构：`contract_prices` + `spreads` 表
- 小作文情绪：`notes` 表（sentiment字段）
- 合约价格：`contract_prices` 表

---

## ⚠️ 注意事项

1. **列名陷阱**：`agent_action_log` 表的列名是 `reasoning`（不是 `reason`）
2. **蓝图冲突**：agent路由已注入 `lithium_api` 蓝图（有 `/api` url_prefix），不要再新建蓝图
3. **gunicorn reload**：修改Python代码后需重启 supervisord 才能生效
4. **幂等性**：`init_default_agents()` 是幂等的，多次调用不会重复插入
5. **数据敏感性**：所有页面仍需反复制保护，不显示"合计"行
6. **旧前端兼容**：`market_battlefield.html` 仍使用旧版数据结构（participants/behavior_models），后续Phase 3需重写

---

## 📐 文件增量修改原则

- Phase 2 新建 `~/.hermes/scripts/lithium_agents/agent_daily_update.py`（不修改app.py/lithium_api.py）
- 如有必要修改agent默认参数 → 修改 `lithium_agents.py` 中的 `DEFAULT_AGENTS` 列表
- 前端修改 → 放在 `static/` 下，不改 `lithium_legacy/`
