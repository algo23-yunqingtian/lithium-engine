# 碳酸锂网页改进 - 任务交接文档

> 2026-07-30 · 综合 7/27-7/30 多次对话汇总

---

## 📋 碳酸锂网页总清单

| 页面 | URL | 当前状态 |
|------|-----|---------|
| **碳酸锂日历主页** | http://124.221.113.37:8766/ | ✅ **已实时**（K线、小作文、周期标注、期限结构5日追溯） |
| **情绪仪表盘** | /lithium_legacy/sentiment_dashboard.html | ⚠️ 前端架子，内容空白 |
| **市场时间线** | /lithium_legacy/market_timeline.html | ⚠️ 静态笔记数据 |
| **交易逻辑扫描仪** | /lithium_legacy/logic_scanner.html | ⚠️ 前端架子，JS报错 |
| **市场博弈仪表盘** | /lithium_legacy/market_battlefield.html | ⚠️ 静态占位符 |
| **供需平衡 v2** | /lithium_legacy/lithium_balance_v2.html | ⚠️ 硬编码数据 |
| **战场地图 v2 Mockup** | /lithium_legacy/battlefield_v2_mockup.html | 📝 设计稿，无数据 |
| **LC K线独立版** | /lithium_legacy/lc_kline.html | ⚠️ 已被首页替代 |
| **全部页面导航** | /lithium_legacy/index.html | ✅ 静态说明页 |

**服务状态**：supervisord 管理，gunicorn 绑定 8766，进程 PID 2783281，开机自启已配。

**数据库**：`/home/ubuntu/lithium_calendar/lithium.db`（SQLite），现有表含 notes/prices/contract_prices/participants/behavior_models/interpretations/participant_positions/spreads/cards 等。

---

## ✅ 已完成的工作

1. **服务常驻化**：gunicorn + supervisord，崩溃自动重启，开机自启 crontab
2. **K线实时化**：主页集成 akshare 拉取 LC0 + LC2608~LC2612 日K
3. **期限结构增强**：`/api/term-structure?days=5` 返回5日合约曲线+月差+量仓变动，前端ECharts渲染
4. **反复制优化**：允许 input/textarea/select 正常选择，禁用全局右键/F12/拖拽/文本选择
5. **定时更新 cron**：`f3e74086ae57`（碳酸锂每日数据更新），`lc_daily_update.sh` wrapper，工作日18:00自动拉取数据
6. **拼写 bug 修复**：`iterrowsrows()` → `iterrows()`
7. **设计文档**：`/home/ubuntu/lithium_calendar/docs/lithium_dashboard_enhancement_design.md`（完整增强方案）

---

## ✅ 你的想法（已确认的改进方向）

### A. 交易逻辑甘特图
- 类似锌复盘页面的逻辑甘特时间轴
- 横轴：日期，纵轴：主导交易逻辑（供应紧张/需求下滑/宏观放水/资金操纵/小作文阵等）
- 颜色：多空强度
- 每日自动主导逻辑评分，用户可手动微调

### B. 情绪仪表盘相关品种联动
- **产业价格层**：碳酸锂现货（工业级/电池级）、沪锂LC0、镍期货、锂资源股/新能源整车股
- **宏观/需求层**：新能源车下游销量（乘联会/交强险）、储能装机、人民币/美元
- **情绪辅助层**：小作文情绪得分、头部咨询/电池厂报价追踪

### C. 时间线与产业/资金事件轴对齐
- 小作文事件轴与资金事件、产业事件对齐展示

### D. 多主体博弈实时化（核心设计：Token 最优架构）
- 90% 规则驱动 + 10% LLM 批量决策
- 每日收盘后 no_agent 脚本做规则更新（浮盈、止损止盈、仓位上限、价格接近目标价标记 needs_review）
- 仅事件触发时 1 次 LLM 批量处理所有需复核的 agent（约 1-2 次/日）
- 前端 `market_battlefield.html` 从 SQLite 读取状态渲染
- 详细表结构 + prompt 模板已写入设计文档

### E. 主动套利监测
- 正向市/反向市、鹰鸽市并库存套利的涨跌颜色标记
- 年节性月差均值/当前月差偏离：识别稀缺/贵布稀缺位置

---

## ⏳ 待完成 / 未开始

### 高优先级（⭐⭐⭐）
1. **多主体博弈状态层 + 日更新脚本**
   - 新增 SQLite 表：agents, agent_history, agent_events, agent_decisions
   - 新增后端 API：`POST /api/agents`（初始化）、`GET /api/battlefield`（查询）
   - 新建 no_agent cron 脚本 `agent_daily_update.py`
   - 修改 `market_battlefield.html` 从实时数据渲染（而非静态占位符）

### 高优先级（⭐⭐）
2. **碳酸锂交易逻辑甘特图页面**
   - 新建 `logic_gantt.html` 或增强 `logic_scanner.html`
   - 每日自动主导逻辑评分

3. **情绪仪表盘接入相关品种联动**
   - 接入锂资源股、新能源板块指数、现货报价等数据

### 中优先级（⭐）
4. **时间线与资金/产业事件轴对齐**
   - 在 `market_timeline.html` 中叠加事件轴

5. **主动套利监测增强**
   - 在期限结构卡片中增加正/反向市/库存套利颜色标记

6. **子页面整合**
   - 废弃/重定向被首页替代的 LC K线独立版

---

## 🔑 关键文件路径

| 文件 | 说明 |
|------|------|
| `/home/ubuntu/lithium_calendar/app.py` | 主应用，含 K线/期限结构等路由 |
| `/home/ubuntu/lithium_calendar/static/index.html` | 主页前端 |
| `/home/ubuntu/lithium_calendar/lithium_api.py` | 合约/价格 API |
| `/home/ubuntu/lithium_calendar/lithium.db` | SQLite 数据库 |
| `/home/ubuntu/lithium_calendar/supervisor.conf` | supervisord 配置 |
| `/home/ubuntu/lithium_calendar/run.sh` | 启动脚本 |
| `/home/ubuntu/lithium_calendar/docs/lithium_dashboard_enhancement_design.md` | 完整增强设计文档 |
| `/home/ubuntu/lithium_calendar/static/sentiment_dashboard.html` | 情绪仪表盘（静态） |
| `/home/ubuntu/lithium_calendar/static/market_battlefield.html` | 多主体博弈（静态） |
| `/home/ubuntu/lithium_calendar/static/logic_scanner.html` | 逻辑扫描仪（静态） |

---

## 💡 建议执行顺序

1. **多主体博弈**（最复杂，数据底座已有）→ 先做状态层 + 脚本
2. **交易逻辑甘特图** → 基于已有 notes 数据
3. **情绪仪表盘品种联动** → 接入 akshare 外部数据
4. **主动套利监测** → 在现有期限结构基础上 patch

## 📌 注意事项

- 数据敏感，所有页面仍需反复制保护
- 不显示"合计"行
- 多主体博弈方案已考虑 Token 优化：日均 LLM 调用仅 1-2 次
- 所有修改在 `/lithium_legacy/` 下保留旧页面，不删除
