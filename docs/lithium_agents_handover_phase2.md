# 碳酸锂多主体博弈 — Phase 2 交接文档

> 2026-07-30 · Phase 2 完成后的交接文档
> 供后续对话继续开发 Phase 3+ 使用

---

## 📋 当前状态

**已完成（Phase 1 + Phase 2）**：
1. ✅ Phase 1：数据库7张表 + 7个Agent初始化 + API端点
2. ✅ Phase 2：Agent每日更新脚本（规则引擎），cron自动执行
3. ✅ `agents` 表添加了 `pnl_unreal` 列
4. ✅ Cron 任务 `50fa27a86e43`：工作日18:30 自动更新

**未完成（待做）**：
1. ⏳ Phase 3：Battlefield 前端真实数据渲染（最关键！）
2. ⏳ Phase 4：LLM 批量审核
3. ⏳ Phase 5：主页联动

---

## 🗄️ 数据库结构

```
DB路径: /home/ubuntu/lithium_calendar/lithium.db

agents              — 7条，实时更新（含新加的 pnl_unreal 列）
agent_history       — 每日快照
agent_decisions     — LLM/规则决策
agent_events        — 市场事件
agent_view_log      — 历史观点日志
agent_custom_data   — 用户手动注入
agent_action_log    — 完整决策流水
```

---

## 🎯 下一步：Phase 3 — Battlefield 前端真实数据渲染

### 目标
把 `http://124.221.113.37:8766/` 的 Battlefield 页面改成读取真实 agents 数据。

### 当前页面状态
- 主页 `static/index.html` 中有一个 Battlefield 入口
- 旧版博弈页面在 `static/lithium_legacy/market_battlefield.html`（静态占位符，不读取 agents）
- API `/api/battlefield` 已经返回 agents + history + decisions + custom_data

### 需要做的事
1. 新建 `static/battlefield_agents.html` — 基于 agents 数据的博弈页面
2. 渲染 7 个 agent 卡片（view_score、position、view_text、risk_params）
3. 做持仓对比可视化（多空力量对比条形图）
4. 做历史观点甘特图（基于 agent_view_log，参考锌复盘甘特图实现）
5. 主页 BattleField 入口链接到 `battlefield_agents.html` 而不是旧版

### 数据源
```javascript
// 读取实时数据
fetch('/api/battlefield').then(r => r.json()).then(data => {
    data.agents;        // Agent 实时状态
    data.history;       // 历史快照
    data.decisions;     // 决策记录
});

// 读取单个agent历史
fetch('/api/battlefield/history?agent_id=smelter&days=90');

// 读取甘特图数据
fetch('/api/battlefield/history');
```

### 参考
- 锌复盘甘特图：`~/.hermes/scripts/zinc_logic_review_update.sh` 生成的 HTML
- 设计文档：`/home/ubuntu/lithium_calendar/docs/lithium_dashboard_enhancement_design.md` 第3节

---

## 🔧 重启/验证命令

```bash
# 重启服务
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar

# 验证服务
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf status

# 查看API
curl -s http://127.0.0.1:8766/api/battlefield | python3 -m json.tool | head -50

# 测试每日更新脚本
/home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.sh
```

---

## ⚠️ 注意事项

1. **列名陷阱**：`agent_action_log` 表的列名是 `reasoning`（不是 `reason`）
2. **蓝图冲突**：agent路由已注入 `lithium_api` 蓝图，不要再新建蓝图
3. **gunicorn reload**：修改Python代码后需重启 supervisord 才能生效
4. **数据敏感性**：所有页面仍需反复制保护，不显示"合计"行
5. **旧前端兼容**：`market_battlefield.html` 仍使用旧版数据结构，保留不动

---

## 📁 关键文件

| 文件 | 说明 |
|------|------|
| `/home/ubuntu/lithium_calendar/app.py` | 主应用 |
| `/home/ubuntu/lithium_calendar/lithium_agents.py` | Agent框架核心 |
| `/home/ubuntu/lithium_calendar/lithium_api.py` | API路由 |
| `/home/ubuntu/lithium_calendar/lithium.db` | SQLite数据库 |
| `~/.hermes/scripts/lithium_agents/agent_daily_update.py` | Phase 2 每日更新脚本 |
| `~/.hermes/scripts/lithium_agents/agent_daily_update.sh` | shell wrapper |
