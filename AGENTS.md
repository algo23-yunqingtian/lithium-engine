# AGENTS.md — 新人 / 新 agent 入职指南

欢迎加入碳酸锂多主体博弈系统。本文件是第一天必读：先理解全貌，再动手。

---

## Day 1：读这些，理解系统

1. **`README.md`** — 系统全貌、7 主体速查、目录结构、快速开始
2. **`docs/AGENT_LOGIC.md`** — 7 主体逻辑 + 阈值速查表（核心）
3. **`docs/ARCHITECTURE.md`** — 系统架构（数据流、模块边界）
4. **`docs/game_matrix_manual.md`** — 交易者使用说明书（业务视角）

> 想深究历史与决策缘由：`docs/HANDOVER_ARCHIVE.md`（763 行，16 章完整归档）。

---

## 三个目录 / 仓库分工

| 目录 | 角色 |
|------|------|
| 本仓库 `lithium-engine` | 后端（Flask + SQLite，35 表）+ 博弈引擎 |
| `lithium_gh_static/` → `lithium-dashboard` 仓库 | 前端静态站（GitHub Pages） |
| `~/.hermes/scripts/lithium_agents/` | 博弈引擎运行副本（cron 从这里跑） |

---

## 数据铁律（务必遵守）

1. **数据库文件绝不入库**：`*.db` 已被 `.gitignore` 排除。生产 DB（`lithium.db`）数据敏感，仓库只携带 `sql/schema.sql`（纯 DDL）。
2. **schema 变更走版本化**：改动表结构必须在 `sql/` 下新增迁移脚本（按 `V00N_xxx.sql` 命名），并同步更新 `docs/DB_SCHEMA.md`。
3. **回填脚本必须支持 `--dry-run`**：先空跑验证影响范围，再正式执行。
4. **三层文件隔离**：
   - Python 库/config：纯客观数据读取、清洗、数学计算，**禁止写入产业逻辑**
   - `docs/frame` 类框架文档：品种专属产业链、核心矛盾、交易逻辑打分，支持人工编辑
   - 临时调研数据：仅写临时目录，**不污染通用文件**
5. **查缺口先查本地真相源**：确认数据缺口前，先查本地 DB 已有指标，不要急着找外部源。历史教训：曾报告 7 个缺口，实际 5 个是假缺口（本地已有）。

---

## 改 Agent 逻辑的标准流程

```
1. 提交 Issue（type:agent + P1/P2）
2. 修改 engine/xxx.py
3. 跑回测验证：python lithium_backtest.py
4. PR review（必须附回测结果）
5. 合并后更新 docs/game_matrix_manual.md
```

## 接数据源的标准流程

```
1. 提交 Issue（type:data + P1/P2）
2. 调研数据源可用性（知几 / akshare / 手动注入）
3. 写接入脚本（scripts/xxx.py），支持 --dry-run
4. dry-run 验证
5. PR review
6. 合并后更新 docs/DATA_SOURCES.md
```

---

## 分支与合并

| 分支 | 用途 | 合并规则 |
|------|------|----------|
| `main` | 生产 | 只接受 PR，主脑 approve + 需测试结果 |
| `develop` | 日常开发 | feature/fix → PR → develop（≥1 人 review） |
| `feature/xxx` | 新功能 | PR → develop |
| `fix/xxx` | 修复 | PR → develop |
| `docs/xxx` | 文档 | PR → main（可直接合并） |

### Issue 标签

```
type:bug / type:feature / type:docs / type:data / type:agent / type:ui
P0:critical / P1:high / P2:medium / P3:low
needs:research / needs:design / good:first
```

---

## 协作安全（重要）

- **绝不把 token / 密钥写进 shell 命令或打印出来**，用 Python 读 `.env`。
- **push 前先 `git pull --no-rebase --no-edit`**，避免 non-fast-forward 静默失败。
- 自动推送脚本必须用 `subprocess.run` + 检查返回码，**绝不 `Popen` + `DEVNULL` 吞掉错误**（曾因此堆积 214 commit）。
- `git push` 没有 `--max-time` 参数，延长超时用 `GIT_CURL_OPT="--max-time 180 --retry 3"`，并用 API 对比远程 SHA 二次确认。

---

## 新人路线

```
Day 1:   README.md → 理解系统全貌
Day 2:   docs/AGENT_LOGIC.md → 理解 7 主体逻辑
Day 3:   docs/COLLAB_GUIDE.md → 理解协作流程
Day 4-5: 认领 good:first issue，走一遍完整流程
Week 2:  认领 type:docs 或 type:data 任务
Week 3+: 认领 type:feature 或 type:agent 任务
```
