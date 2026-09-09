# lithium-engine — 碳酸锂多主体博弈系统（后端 + 博弈引擎）

基于**规则引擎 + 博弈因子**的 7 主体碳酸锂多主体博弈模拟系统。每日 18:30 cron 运行，输出各主体观点评分、仓位变动、博弈修正、7 维逻辑评分和信号互动。

> 前端静态看板见姊妹仓库 [`lithium-dashboard`](https://github.com/algo23-yunqingtian/lithium-dashboard)（GitHub Pages）。

---

## ⚠️ HAM 因子实验归档说明（2026-09-09）

**HAM（异质 Agent 模型）因子实验已完整归档，预测分支正式终止。**

- 详见 `model_ham/ham_experiment_archive/`（含完整失败复盘 `ham_failure_review.md`、历史代码、exp401-403/exp301-309 全部报告与中间产物）
- **原因**：经三轮严格验证（前视泄露修复 + 滚动窗口 + Bonferroni 多重检验校正），HAM 衍生因子（`n_c` / `Total_Demand` / `n_f-n_c`）均无真实预测力；旧因子预测力的唯一来源是 1 日前视泄露，修复后归零
- **后续实验不再调用 HAM 任何函数、变量**；工作重心转向挖掘新的基本面因子
- **GMM K=4 市场状态聚类保留**，但仅用于定性市场复盘，不做预测
- 工作重心后续转向基本面因子探索（库存/基差/期限结构/成交量持仓）

---

## 系统全貌

| 维度 | 说明 |
|------|------|
| 主体数量 | 7（冶炼厂 / 矿商 / 贸易商 / 机构 / 套利商 / 散户 / 政策） |
| 输出 | 观点评分、仓位变动、博弈修正、7 维逻辑评分、信号互动 |
| 运行频率 | 工作日 18:30 cron |
| 数据库 | SQLite，35 张表（schema 见 `sql/schema.sql`） |
| 数据源 | SMM 日/周/月度价格、期货价、月差、库存、现货、仓单、席位 |

### 7 主体速查

| 主体 | clamp 区间 | 信息敏感度 | 行为敏感度 | 核心逻辑 |
|------|-----------|-----------|-----------|----------|
| smelter 冶炼厂 | (-60, +80) | 0.3 | 0.2 | 价涨增产→看空，价跌减产→看多 |
| resource 矿商 | (-50, +70) | 0.3 | 0.2 | 价格贴成本→减产→看多 |
| merchant 贸易商 | (-50, +60) | 0.5 | 0.5 | 月差结构→持有/抛售现货 |
| institution 机构 | (-70, +70) | 0.7 | 0.6 | 趋势跟随 / 动量 |
| arbitrage 套利商 | (-20, +20) | 0.6 | 0.4 | 月差均值回归 |
| retail 散户 | (-60, +60) | 0.9 | 0.9 | 情绪驱动 / 追涨杀跌 |
| policy 政策 | (-60, +60) | 0.2 | 0.1 | 政策关键词 + 价格区间 |

### 公共参数

| 参数 | 值 |
|------|-----|
| 惯性钳制 | ±20/日 |
| 止损 | 8% → 全平 |
| 止盈 | 15% → 减半 |
| 仓位上限 | 30%/次 |
| 成本线 | 125,000 元/吨 |
| 政策锚 | 160,000（干预）/ 130,000（托底） |
| 博弈修正上限 | ±10 |

---

## 目录结构

```
lithium-engine/
├── README.md                  # 本文件
├── AGENTS.md                  # 新 agent / 新人入职指南
├── app.py                     # Flask 主入口
├── lithium_api.py             # 实时数据 API
├── lithium_agents.py          # Agent 框架
├── lithium_backtest.py        # 回测引擎
├── lithium_dashboard_api.py   # 看板 API
├── lithium_global_api.py      # 全球数据 API
├── spread_analysis_api.py     # 月差分析 API
├── ...                        # 其余后端模块
├── engine/                    # 博弈引擎核心
│   ├── agent_daily_update.py     # 每日更新主流程
│   ├── agent_daily_update.sh     # cron 包装脚本
│   ├── game_factors.py           # 博弈因子
│   ├── signal_interactions.py    # 信号驱动互动
│   ├── fundamental_indices.py    # 基本面指数
│   └── agent_psychology_calc.py  # Agent 心理计算
├── model_lite/                # 简化模型参数 + 假设
│   ├── params.json             # 7 主体定义 + 4 因子 + 约束
│   └── ASSUMPTIONS.md          # 模型假设文档
├── scripts/                   # 工具脚本（回填 / 增量 / 抓取）
│   ├── backfill_spot.py
│   ├── backfill_agent_history.py
│   ├── incremental_update.py
│   └── ...
├── sql/
│   └── schema.sql             # 35 张表 DDL
├── data/                      # 数据库（.gitignore，不入库）
│   └── .gitkeep
├── docs/                      # 设计 + 协作文档
│   ├── ARCHITECTURE.md
│   ├── COLLAB_GUIDE.md
│   ├── DB_SCHEMA.md
│   ├── DATA_SOURCES.md
│   ├── AGENT_LOGIC.md
│   ├── game_matrix_manual.md     # 交易者使用说明书
│   ├── HANDOVER_ARCHIVE.md       # 完整归档（16章）
│   └── ...
├── requirements.txt
└── .gitignore
```

---

## 快速开始

### 1. 准备数据库

```bash
sqlite3 data/lithium.db < sql/schema.sql
```

> ⚠️ `*.db` 被 `.gitignore` 排除，仓库不携带数据。本地需从生产环境复制或自行建表后回填。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 跑一次 Agent 每日更新

```bash
python engine/agent_daily_update.py
```

### 4. 启动 Flask 服务

```bash
python app.py
```

---

## 数据现状（2026-09-08）

| 数据源 | 表 | 行数 | 频率 |
|--------|------|------|------|
| SMM 日度价 | lithium_daily_prices | 32,122 | 日 |
| SMM 周度 | lithium_weekly | ~7,500 | 周 |
| SMM 月度 | lithium_monthly | ~480 | 月 |
| 期货价格 | prices | 760 | 日 |
| 合约价格 | contract_prices | 8,677 | 日 |
| 月差 | spreads | 7,672 | 日 |
| 库存 | inventory_history | 67 | 周 |
| 基本面指数 | fundamental_indices | 728 | 日 |
| Agent 历史 | agent_history | 226 | 日 |
| 博弈因子 | agent_game_factors | 168 | 日 |
| 逻辑评分 | logic_scores | ~1,500 | 日 |
| 互动记录 | agent_interactions | 751 | 日 |

**已知数据缺口（仅 2 个真缺口）**：期货研报多空家数、碳酸锂研报评级（均需东财研报 API 或手动注入）。

---

## 定时任务

| 时间 | 任务 | 脚本 |
|------|------|------|
| 工作日 18:00 | GitHub Pages 数据推送 | `lithium_gh_static/push_to_github.sh` |
| 工作日 18:30 | Agent 每日更新 | `engine/agent_daily_update.sh` |
| 工作日 18:00 | 分钟数据采集 | `scripts/cron_minute_collector.py` |

---

## 简化模型（model_lite）

简化版博弈推演工具，把 7 主体模型压缩为「价格-成本-库存」三因子驱动，用于快速推演和参数调试。

- **在线推演**：<https://algo23-yunqingtian.github.io/lithium-dashboard/pages/model_lite.html>
- **参数定义**：[`model_lite/params.json`](model_lite/params.json)
- **模型假设**：[`model_lite/ASSUMPTIONS.md`](model_lite/ASSUMPTIONS.md)

> 完整版（含博弈因子矩阵、信号互动、7 维逻辑评分）见 `engine/` 目录。

---

## 量化模型实验分支（model_ham）

`model_ham/` 目录下的因子/聚类实验分支，经多轮滚动窗口验证后形成两条明确结论：

| 分支 | 实验编号 | 结论 | 状态 |
|------|---------|------|------|
| **HAM 预测** | exp401-exp403 | HAM 因子**无定量预测能力**；且原版含 1 日未来价格前视漏洞（已修复验证）。滚动样本外准确率≈50%，RankIC 不显著 | 🔴 **分支终止**，不作预测信号 |
| **GMM 聚类** | exp404 | K=4 聚类稳健，用于市场状态归类、行情复盘；滚动窗口训练无全量拟合 | 🟢 **保留**，仅定性复盘 |

- **HAM 预测分支终止**：Brock-Hommes 双主体因子（n_c / n_f-n_c / Total_Demand）在碳酸锂上三轮验证均无外推预测力，已关闭迭代。详见 `model_ham/exp4_lookahead_fix/HANDOVER_round4_lookahead_fix.md`。
- **GMM 聚类保留**：保留 4 状态市场阶段归类，用于定性复盘（当前状态、历史转移频率、持续天数），**不做涨跌预测**。详见 `model_ham/exp404_cluster_analysis/HANDOVER_round5_cluster_analysis.md`。
- **方法论留存**：单次静态切分的"幸运切点"问题 + 注释与代码不一致陷阱，见 `model_ham/exp4_lookahead_fix/reports/exp403_methodology_lesson.md`。

---

## 协作

详见 [`docs/COLLAB_GUIDE.md`](docs/COLLAB_GUIDE.md) 与 [`AGENTS.md`](AGENTS.md)。

- **线上看板**：<https://algo23-yunqingtian.github.io/lithium-dashboard/>
- **博弈页面**：<http://124.221.113.37:8766/battlefield_agents.html>
- **前端仓库**：<https://github.com/algo23-yunqingtian/lithium-dashboard>
