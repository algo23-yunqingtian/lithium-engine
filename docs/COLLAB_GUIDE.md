# COLLAB_GUIDE.md — 协作指南

给所有参与本项目的同事。新人先读 [`AGENTS.md`](../AGENTS.md)，再读本文件。

---

## 角色分工

| 角色 | 职责 | 需要的权限 |
|------|------|-----------|
| **主脑** | 架构决策、数据源管理、系统部署 | 全部 |
| **数据工程师** | 数据源接入、DB 维护、回填脚本 | repo write + DB 访问 |
| **策略研究员** | Agent 逻辑调参、博弈因子设计、回测验证 | repo write + 文档编辑 |
| **前端开发** | 看板页面开发、数据可视化 | repo write（lithium-dashboard） |
| **协作者（只读）** | 查看文档、提 issue、评论 | repo read |

---

## 分支策略

```
main          → 生产分支，只接受 PR 合并
develop       → 开发分支，日常提交
feature/xxx   → 功能分支（feature/agent_psychology, feature/gantt_chart）
fix/xxx       → 修复分支（fix/spot_price_fill, fix/data_sync）
docs/xxx      → 文档分支（docs/manual_update）
```

**合并规则**：
- feature/fix 分支 → PR → develop（至少 1 人 review）
- develop → PR → main（主脑 approve，需要测试结果）
- docs 分支 → PR → main（可直接合并）

---

## Issue 标签体系

```
🔴 type:bug         → Bug 报告
🔵 type:feature     → 新功能
🟢 type:docs        → 文档
🟡 type:data        → 数据源相关
🟣 type:agent       → Agent 逻辑相关
⚪ type:ui          → 前端 UI

P0:critical         → 紧急（系统不可用）
P1:high             → 高优先级（核心功能）
P2:medium           → 中优先级
P3:low              → 低优先级

needs:research      → 需要调研
needs:design        → 需要设计评审
good:first          → 适合新人
```

---

## 数据协作规范

### 数据库管理

- 生产 DB（`lithium.db`）不入库，通过 `.gitignore` 排除
- schema 变更走 `sql/` 目录，按版本号命名（`V001_create_spot_price.sql`）
- 每次 schema 变更需更新 `docs/DB_SCHEMA.md`
- 数据回填脚本必须支持 `--dry-run` 参数

### 数据源接入流程

```
1. 提交 Issue（type:data + P1/P2）
2. 调研数据源可用性（知几 / akshare / 手动注入）
3. 写接入脚本（scripts/xxx.py），支持 --dry-run
4. dry-run 验证
5. PR review
6. 合并后更新 docs/DATA_SOURCES.md
```

### Agent 逻辑修改流程

```
1. 提交 Issue（type:agent + P1/P2）
2. 修改 engine/xxx.py
3. 跑回测验证：python lithium_backtest.py
4. PR review（必须附回测结果）
5. 合并后更新 docs/game_matrix_manual.md
```

---

## 协作安全铁律

1. **绝不把 token / 密码写进 shell 命令或打印出来**，用 Python 读 `.env`
2. **push 前先 `git pull --no-rebase --no-edit`**，避免 non-fast-forward 静默失败
3. 自动推送脚本必须用 `subprocess.run` + 检查返回码，**绝不 `Popen` + `DEVNULL` 吞掉错误**
4. `git push` 没有 `--max-time` 参数，延长超时用 `GIT_CURL_OPT="--max-time 180 --retry 3"`
5. 推送后用 API 对比远程 SHA 二次确认

---

## 协作工具

| 工具 | 用途 |
|------|------|
| GitHub Issues | 任务管理、Bug 报告、需求讨论 |
| GitHub PR Review | 代码审查、合并 |
| GitHub Discussions | 技术讨论、设计评审 |
| 飞书文档 | 实时协作（设计文档、会议纪要） |

---

## 新人入职流程

```
Day 1:   读 README.md → 理解系统全貌
Day 2:   读 docs/AGENT_LOGIC.md → 理解 7 主体逻辑
Day 3:   读 docs/COLLAB_GUIDE.md → 理解协作流程
Day 4-5: 认领 good:first issue，走一遍完整流程
Week 2:  认领 type:docs 或 type:data 任务
Week 3+: 认领 type:feature 或 type:agent 任务
```

---

## 关键网址

| 用途 | 地址 |
|------|------|
| 本仓库 | <https://github.com/algo23-yunqingtian/lithium-engine> |
| 前端仓库 | <https://github.com/algo23-yunqingtian/lithium-dashboard> |
| GitHub Pages 看板 | <https://algo23-yunqingtian.github.io/lithium-dashboard/> |
| 本地 Flask 服务 | <http://124.221.113.37:8766/lithium-gh/> |
| 博弈页面 | <http://124.221.113.37:8766/battlefield_agents.html> |
