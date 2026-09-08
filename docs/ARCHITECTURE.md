# 系统架构文档

> 给技术同事看的系统架构参考。覆盖数据流、模块边界、cron 时序、依赖关系。

---

## 1. 系统数据流

```
SMM 数据源（日/周/月度）
    │
    ▼
scripts/ 采集脚本（SMM → SQLite）
    │
    ▼
lithium.db（35 张表，核心数据库）
    ├── 价格数据表（prices / contract_prices / spreads / lithium_daily_prices ...）
    ├── Agent 核心表（agents / agent_history / agent_view_log / agent_action_log ...）
    ├── 博弈引擎表（fundamental_indices / logic_scores / market_signals）
    └── 辅助表（inventory_history / info_access / behavior_models ...）
    │
    ├── 外部 DB
    │   ├── lc_spot.db（732 行，现货价格 + 基差）
    │   └── lc_position.db（1791 行仓单 + 7540 行席位）
    │
    ▼
博弈引擎（~/.hermes/scripts/lithium_agents/）
    │  18:30 cron 触发 agent_daily_update
    │  ├── 读取最新价格 + 基本面指数
    │  ├── 7 主体各自计算 view_score → 决策调仓
    │  ├── 博弈因子修正（info_transfer / behavioral_impact）
    │  ├── 互动记录写入 agent_interactions
    │  └── 结果写回 lithium.db
    │
    ▼
前端 API 层（lithium_api.py / lithium_api_v2 / lithium_dashboard_api.py）
    │  Flask 路由提供 JSON API
    │  ├── /api/agents — 7 主体实时状态
    │  ├── /api/prices — 价格数据
    │  ├── /api/game_factors — 博弈因子
    │  ├── /api/fundamental — 基本面指数
    │  └── /api/interactions — 互动记录
    │
    ▼
GitHub Pages 前端（lithium_gh_static/ → lithium-dashboard 仓库）
    │  18:00 cron 推送静态资源
    │  ├── 纯 HTML + ECharts
    │  ├── JS 调用 API 获取数据
    │  └── 无服务器端依赖
    │
    ▼
用户浏览器访问
```

---

## 2. 模块边界

| 模块 | 目录 | 职责 | 输入 | 输出 |
|------|------|------|------|------|
| 数据采集 | `scripts/` | SMM/akshare → SQLite | SMM API / Excel | lithium.db 表数据 |
| 博弈引擎 | `~/.hermes/scripts/lithium_agents/` | 7 主体决策 + 博弈修正 | 价格 + 基本面 | agent_* 表更新 |
| 后端 API | `lithium_api*.py` | Flask JSON API | lithium.db 查询 | HTTP JSON 响应 |
| 前端 | `lithium_gh_static/` | 静态看板展示 | API JSON | HTML+ECharts 页面 |
| 回测 | `lithium_backtest.py` | 策略验证 | 历史 DB | 回测报告 |
| 数据库 | `lithium.db` + 外部 DB | 数据存储 | 采集脚本写入 | API/引擎查询 |

### 模块隔离原则

- **数据采集层**只负责写入 DB，不参与决策逻辑
- **博弈引擎**只读取 DB + 写回 agent_* 表，不直接对外提供 API
- **API 层**只读 DB，不修改数据
- **前端**零服务器依赖，只通过 API 获取数据

---

## 3. Cron 时序

| 时间 | 任务 | 说明 |
|------|------|------|
| 18:00 | `minute_collector` | 分钟级行情采集 |
| 18:00 | push GitHub Pages | 推送静态前端到 lithium-dashboard 仓库 |
| 18:30 | `agent_daily_update` | 7 主体每日博弈推演，更新 agent_* 表 |

### 时序设计原因

- 18:00 先完成数据采集和前端推送（此时当日交易已结束）
- 18:30 在最新数据基础上跑 Agent 博弈推演
- 前端在 Agent 更新前推送，保证用户先看到最新价格看板；Agent 结果次日推送到前端

---

## 4. 依赖关系图（文字版）

```
┌─────────────────────────────────────────────────────────┐
│                    外部数据源                             │
│  SMM API │ akshare │ 东财API(待接入) │ 手动Excel           │
└──────┬──────────┬───────────┬──────────┬────────────────┘
       │          │           │          │
       ▼          ▼           ▼          ▼
┌──────────────────────────────────────────┐
│        scripts/ 数据采集脚本                │
│  (SMM日频 / 周频 / 月频 / 期货 / 现货)      │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐    ┌──────────────┐
│              lithium.db (35表)            │◄──►│  lc_spot.db  │
│                                           │    │  (732行现货)  │
│  ┌─────────────┐  ┌──────────────┐       │    └──────────────┘
│  │ 价格数据表   │  │ Agent核心表   │       │    ┌──────────────┐
│  │ (10张)      │  │ (10张)       │       │◄──►│ lc_position  │
│  └──────┬──────┘  └──────┬───────┘       │    │  .db        │
│         │                │               │    │ (仓单+席位)  │
│  ┌──────┴──────┐  ┌──────┴───────┐       │    └──────────────┘
│  │ 博弈引擎表   │  │ 辅助表        │       │
│  │ (3张)       │  │ (12张)       │       │
│  └─────────────┘  └──────────────┘       │
└──────────────────┬───────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
┌──────────────────┐  ┌──────────────────┐
│  博弈引擎          │  │  Flask API 层     │
│  lithium_agents/  │  │  lithium_api*.py │
│  (读写 agent_*)   │  │  (只读 DB)        │
└────────┬─────────┘  └────────┬─────────┘
         │                      │
         │ 写回 agent_*表        │ JSON API
         │                      │
         ▼                      ▼
┌──────────────────┐  ┌──────────────────┐
│  agent_daily_    │  │  GitHub Pages     │
│  update (cron)   │  │  lithium_gh_static│
│  18:30           │  │  (cron 18:00)     │
└──────────────────┘  └──────────────────┘
```

---

## 5. 数据库文件布局

| 文件 | 路径 | 表数 | 说明 |
|------|------|------|------|
| `lithium.db` | 后端根目录 | 35 | 核心数据库，所有主表 |
| `lc_spot.db` | 后端根目录 | - | 现货价格 + 基差（732 行） |
| `lc_position.db` | 后端根目录 | - | 仓单（1791 行）+ 席位（7540 行） |
| `sql/schema.sql` | 仓库 | - | 纯 DDL，版本化管理 |

### 数据库访问规范

- 所有脚本通过 Python `sqlite3` 模块访问，`check_same_thread=False`
- 外部 DB 为只读引用，不做写入
- `lithium.db` 的写入仅来自：采集脚本 + 博弈引擎

---

## 6. 部署拓扑

```
服务器（Ubuntu）
├── lithium-engine/          ← 后端 + 引擎 + DB
│   ├── lithium.db
│   ├── lc_spot.db
│   ├── lc_position.db
│   ├── lithium_api.py       ← Flask API（端口 5080）
│   └── scripts/             ← 采集脚本
│
├── ~/.hermes/scripts/lithium_agents/  ← 引擎运行副本
│   └── (cron 从此目录执行)
│
└── lithium_gh_static/       ← 前端
    └── → push to lithium-dashboard GitHub 仓库
        └── GitHub Pages (https://xxx.github.io/lithium-dashboard/)
```

---

## 7. 关键约束

1. **DB 文件不入库**：`.db` 被 `.gitignore` 排除，仓库只携带 `sql/schema.sql`
2. **schema 变更走版本化**：`sql/V00N_xxx.sql` 迁移脚本 + 同步更新 `DB_SCHEMA.md`
3. **回填脚本支持 `--dry-run`**：先空跑验证影响范围
4. **push 前先 `git pull --no-rebase`**：避免 non-fast-forward 静默失败
5. **采集脚本用 `subprocess.run` + 检查返回码**：绝不 `Popen` + `DEVNULL` 吞错误
