# 数据源清单 + 接入指南

> 碳酸锂多主体博弈系统全部数据源清单、行数统计、缺口识别、接入流程。

---

## 一、数据源总览

| # | 数据源 | 存储位置 | 表名 | 行数 | 频率 | 状态 |
|---|--------|----------|------|------|------|------|
| 1 | SMM 日度价格 | lithium.db | lithium_daily_prices | 32,122 | 日 | ✅ 正常 |
| 2 | SMM 周度价格 | lithium.db | lithium_weekly | 3,342 | 周 | ✅ 正常 |
| 3 | SMM 月度价格 | lithium.db | lithium_monthly | 1,356 | 月 | ✅ 正常 |
| 4 | SMM 元信息 | lithium.db | lithium_meta | 260 | 随数据 | ✅ 正常 |
| 5 | 期货日 K 线 | lithium.db | prices | 760 | 日 | ✅ 正常 |
| 6 | 合约价格 | lithium.db | contract_prices | 8,677 | 日 | ✅ 正常 |
| 7 | 全合约日汇总 | lithium.db | contract_daily_all | 8,393 | 日 | ✅ 正常 |
| 8 | 月差数据 | lithium.db | spreads | 7,945 | 日 | ✅ 正常 |
| 9 | 库存历史 | lithium.db | inventory_history | 67 | 周 | ✅ 正常 |
| 10 | 现货价格 + 基差 | lc_spot.db | - | 732 | 日 | ✅ 正常 |
| 11 | 仓单数据 | lc_position.db | - | 1,791 | 日 | ✅ 正常 |
| 12 | 席位数据 | lc_position.db | - | 7,540 | 日 | ✅ 正常 |
| 13 | 基本面指数 | lithium.db | fundamental_indices | 754 | 日 | ✅ 正常 |
| 14 | 市场信号 | lithium.db | market_signals | 300 | 日 | ✅ 正常 |
| 15 | Agent 历史 | lithium.db | agent_history | 408 | 日 | ✅ 正常 |
| 16 | Agent 观点日志 | lithium.db | agent_view_log | 406 | 日 | ✅ 正常 |
| 17 | Agent 行为日志 | lithium.db | agent_action_log | 408 | 日 | ✅ 正常 |
| 18 | 博弈因子 | lithium.db | agent_game_factors | 350 | 日 | ✅ 正常 |
| 19 | 逻辑评分 | lithium.db | logic_scores | 350 | 日 | ✅ 正常 |
| 20 | 互动记录 | lithium.db | agent_interactions | 2,099 | 日 | ✅ 正常 |
| 21 | 心理状态 | lithium.db | agent_psychology | 336 | 日 | ✅ 正常 |

### 汇总统计

- **lithium.db**：35 张表，总行数 ~74,000+
- **lc_spot.db**：732 行（现货 + 基差）
- **lc_position.db**：9,331 行（仓单 1,791 + 席位 7,540）

---

## 二、数据缺口（2 个真缺口）

| # | 缺口数据 | 需求方 | 建议数据源 | 优先级 |
|---|----------|--------|------------|--------|
| 1 | 期货研报多空家数 | 机构 Agent | 东财 API（研报频道） | P2 |
| 2 | 研报评级（买入/中性/卖出） | 机构 Agent | 东财 API（研报评级接口） | P2 |

### 缺口影响分析

- **期货研报多空家数**：机构 Agent 的 `info_sensitivity=0.7` 依赖研报情绪判断，缺少此数据导致机构观点中"研报情绪"维度退化为估算值
- **研报评级**：影响机构对中长期趋势的判断权重，缺少则用价格动量替代（精度降低）

### 接入前确认

> ⚠️ 接入前务必先查本地 DB 确认是否已有类似数据。历史教训：曾报告 7 个缺口，实际 5 个是假缺口（本地已有）。

---

## 三、数据源分类详情

### 3.1 SMM 价格数据（日/周/月）

| 维度 | 日度 | 周度 | 月度 |
|------|------|------|------|
| 表名 | lithium_daily_prices | lithium_weekly | lithium_monthly |
| 行数 | 32,122 | 3,342 | 1,356 |
| 字段 | date/col_idx/value | date/col_idx/value | date/col_idx/value |
| 采集方式 | SMM API / Excel 导入 | 同左 | 同左 |
| 更新时间 | 18:00 | 周一 18:00 | 月初 18:00 |
| 采集脚本 | scripts/ | scripts/ | scripts/ |

- `col_idx` 为 SMM 指标列名（如"电池级碳酸锂"、"工业级碳酸锂"等）
- `lithium_meta` 存储指标元信息（sheet_name/col_idx → key/value 映射）

### 3.2 期货价格数据

| 数据 | 表名 | 行数 | 字段 | 来源 |
|------|------|------|------|------|
| 主力日 K | prices | 760 | date/OHLCV/position/settle | akshare / 交易所 |
| 合约价格 | contract_prices | 8,677 | date/contract/OHLCV/position/settle | akshare |
| 全合约汇总 | contract_daily_all | 8,393 | 同上 | akshare |
| 月差 | spreads | 7,945 | date/near/far/spread | 计算（近月-远月） |

- 月差由 `rebuild_spreads_all.py` 从合约价格计算生成
- 采集时间：18:00（`minute_collector` cron）

### 3.3 现货与基差（lc_spot.db）

| 数据 | 行数 | 字段 | 更新频率 |
|------|------|------|----------|
| 现货价格 | 732 | date/price/change | 日 |
| 基差 | 732 | date/spot/futures/basis | 日 |

- 独立 DB，与 `lithium.db` 分离
- API 层通过相对路径 `../lc_spot.db` 访问

### 3.4 仓单与席位（lc_position.db）

| 数据 | 行数 | 字段 | 更新频率 |
|------|------|------|----------|
| 仓单 | 1,791 | date/warehouse/warrant_count/volume | 日 |
| 席位 | 7,540 | date/broker/long/short/net | 日 |

- 来源：交易所公开数据
- 独立 DB，只读访问

### 3.5 基本面与博弈数据

| 数据 | 表名 | 行数 | 来源 |
|------|------|------|------|
| 基本面指数 | fundamental_indices | 754 | 引擎计算（8 维：supply/demand/inventory/profit/sentiment/basis/composite） |
| 市场信号 | market_signals | 300 | 引擎计算 |
| 逻辑评分 | logic_scores | 350 | 引擎计算（7 维逻辑） |
| 博弈因子 | agent_game_factors | 350 | 引擎计算 |
| 互动记录 | agent_interactions | 2,099 | 引擎计算 |

### 3.6 Agent 运行数据

| 数据 | 表名 | 行数 | 更新频率 |
|------|------|------|----------|
| 主体当前状态 | agents | 7 | 日 |
| 历史快照 | agent_history | 408 | 日追加 |
| 观点日志 | agent_view_log | 406 | 日追加 |
| 行为日志 | agent_action_log | 408 | 日追加 |
| 心理状态 | agent_psychology | 336 | 日追加 |
| 互动记录 | agent_interactions | 2,099 | 日追加 |
| 事件记录 | agent_events | 17 | 事件驱动 |

---

## 四、数据接入流程

```
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌────┐    ┌──────────┐
│ 1. Issue │───►│ 2. 调研  │───►│ 3. 脚本   │───►│4.dry-run│───►│5.PR│───►│6.更新文档 │
└─────────┘    └─────────┘    └──────────┘    └─────────┘    └────┘    └──────────┘
```

### Step 1: 提交 Issue

- 标签：`type:data` + `P1`/`P2`/`P3`
- 内容：数据名称、需求方（哪个 Agent 需要）、预期频率、目标表

### Step 2: 调研数据源可用性

- 查本地 DB 确认是否已有类似数据（**铁律：先查本地再找外部**）
- 评估候选数据源：SMM / akshare / 知几 / 东财 API / 手动 Excel
- 确认数据频率、字段映射、获取成本

### Step 3: 写接入脚本

- 路径：`scripts/import_xxx.py`
- 必须支持 `--dry-run` 参数（空跑验证影响范围）
- 必须支持增量更新（按日期去重）
- 写入前打印影响行数预览

### Step 4: dry-run 验证

```bash
python scripts/import_xxx.py --dry-run
# 检查输出：将影响 N 行，日期范围 YYYY-MM-DD ~ YYYY-MM-DD
```

- 确认数据无异常值（价格 0、负数等）
- 确认日期范围合理

### Step 5: PR review

- PR 标题：`[data] 接入 XXX 数据`
- 必须附带 dry-run 输出截图
- review 通过后合并到 `develop` 分支

### Step 6: 更新文档

- 更新本文件（`docs/DATA_SOURCES.md`）的数据源总览表
- 如有新表，同步更新 `docs/DB_SCHEMA.md` 和 `sql/schema.sql`

---

## 五、数据采集 Cron 时序

| 时间 | 任务 | 脚本 | 写入表 |
|------|------|------|--------|
| 18:00 | SMM 日度价格采集 | scripts/ | lithium_daily_prices |
| 18:00 | 期货日 K 采集 | minute_collector | prices |
| 18:00 | 前端推送 | (push script) | - |
| 18:30 | Agent 每日推演 | agent_daily_update | agent_* 表 |

### 周度/月度

- SMM 周度：每周一 18:00 自动采集
- SMM 月度：每月初 18:00 自动采集
- 库存历史：每周三采集（更新 inventory_history）

---

## 六、数据质量检查清单

| 检查项 | 方法 | 频率 |
|--------|------|------|
| 行数突变 | 对比前日行数，增量异常告警 | 日 |
| 价格异常值 | 价格 < 0 或 > 500,000 告警 | 日 |
| 日期连续性 | 检查是否有缺失交易日 | 日 |
| Agent 数据一致 | agents 表 position == agent_history 最新行 | 日 |
| 外部 DB 连通 | lc_spot.db / lc_position.db 可读 | 日 |
| 博弈因子完整 | agent_game_factors 每日 7 行 | 日 |
| 互动记录完整 | agent_interactions 每日有记录 | 日 |
