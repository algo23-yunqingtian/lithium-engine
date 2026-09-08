# 多主体博弈 8 Agent 重构 · 任务封存交接文档

> 封存日期：2026-08-01
> 状态：**设计已对齐，等待用户确认后开工**
> 重启入口：碳酸锂多主体博弈界面（http://124.221.113.37:8766/battlefield_agents.html）顶部 📌 黄色待办 Banner；或直接对本 session 说「继续多主体 8 主体重构」

---

## 一、任务目标

将现有 **7 agent** 的碳酸锂多主体博弈模拟系统重构为 **8 主体新架构**，并新增**研报双轨监控**模块（A 轨·券商系只能写多；B 轨·期货系多空都写）。
补齐现有系统两个缺口：**无研报机构分类**、**无 A 股锂矿指数数据源**。

## 二、8 主体定稿（用户已拍板，等最后确认）

| # | 主体 | 行为定位 | 关键输入 | 交易周期 |
|---|------|----------|----------|----------|
| 1 | 冶炼厂 | 供给端反身性，价涨增仓→看空增产，价跌减仓→看多 | 供给/利润/库存 | 中长线 |
| 2 | 正极厂（新增） | 需求侧：排产/采购/库存天数/原料成本占比 | 排产、采购价、库存天数 | 中长线 |
| 3 | 贸易商（合并强化） | 现货+屯库+基差+正反套+赌仓单；利润来源是价差非方向 | 基差、月差、仓单、现货价 | 中短周期 |
| 4 | 权益类资金（券商系） | 与锂矿/电池/正极 A 股指数强相关；只看研报看涨家数 | 东财研报看涨家数、A股锂矿板块指数、主力资金流 | 中长线慢变量 |
| 5 | 期货基本面资金（新增） | 基于商品基本面的期货研报观点资金 | 期货研报多空比时序 | 较短 |
| 6 | CTA 机构 | 保留现有 institution 趋势跟随模型，不重写 | 量价动量 | 中短 |
| 7 | 散户 | 决策随意，更看 K 线技术指标 | 情绪、技术指标 | 短线 |
| 8 | 游资+政策 | 全市场 80+ 品种自由选择；需中长期明显逻辑+强胜率+人气才介入 | 胜率人气比较、政策关键词 | 中长线择机 |

**已确认的拆分决定**：政策类资金与游资合并为一类；散户单独一类；锂矿（resource）删除（与冶炼厂权重完全同构，冗余），降级为上游成本因子。

## 三、研报双轨监控

- **A 轨（券商系）**：东财研报中心 `reportapi.eastmoney.com` 按"锂"筛研报统计买入/增持/推荐**看涨家数**（只写多）；叠加 akshare 锂矿板块指数（`stock_board_concept_hist_em`）+ 主力资金流（`stock_board_concept_fund_flow_em`）；作权益类资金慢变量。
- **B 轨（期货系）**：期货公司研报**多空都写**，统计偏多/偏空报告家数形成多空比时序；作期货基本面资金输入，周期可更短。
- 数据缺口：期货研报多空家数无稳定公开聚合源 → 暂定 Zhiji API / `agent_custom_data` 手动注入；spot_price 表 0 行（基差核心输入缺失）→ 先试 Zhiji API 现货报价，否则手动注入。
- 东财接口稳定性未验证，失败需退化手动注入（**待用户认可此降级策略**）。

## 四、代码级现状（关键文件）

- 核心引擎：`~/.hermes/scripts/lithium_agents/agent_daily_update.py`（944 行）
  - agent_ids：`["smelter","merchant","institution","arbitrage","retail","resource","policy"]`
  - `FUNDAMENTAL_SCALE=30`；`AGENT_CLAMP` 不对称编码性格；惯性 ±20/日；pos_change 上限 30%；止损 6% 全平、止盈 12% 减半
  - `DB_PATH = os.environ.get("LITHIUM_DB", "/home/ubuntu/lithium_calendar/lithium.db")`
- 博弈因子：`~/.hermes/scripts/lithium_agents/game_factors.py`（228 行，三层：信息传递/行为影响/极化检测）
- 前端：`/home/ubuntu/lithium_calendar/static/battlefield_agents.html`（本次已加待办 Banner）
- 配置：`/home/ubuntu/lithium_calendar/lithium_agents.py`；历史回填：`backfill_agent_history.py`

### 关键常量速查
- `FUNDAMENTAL_WEIGHTS`：smelter {supply:-0.3, profit:0.4, inventory:-0.2}；merchant {basis:0.3, inventory:0.2, profit:0.2}；institution {composite:0.5}；arbitrage {basis:0.4, profit:0.2}；retail {sentiment:0.4, composite:0.2}；policy 不参与
- `AGENT_CLAMP`：smelter(-60,80)、merchant(-50,60)、institution(-70,70)、arbitrage(-20,20)、retail(-60,60)、resource(-50,70)、policy(-60,60)
- info_sensitivities：smelter 0.3 / merchant 0.5 / institution 0.7 / arbitrage 0.6 / retail 0.9 / policy 0.2

## 五、数据库联动

- `lithium.db`（35 表）：agents(7)、agent_history(226)、agent_view_log(224)、agent_game_factors(168)、agent_interactions(751)、agent_custom_data(2)、agent_events(7)、behavior_models(8)、participants(8)、notes(5)、fundamental_indices(728)、spot_price(**0 行**)、lithium_daily_prices(32122)、contract_prices(8404)、spreads(7672)、inventory_history(67)
- `lc_position.db`（`/home/ubuntu/lc_futures_data/data/lc_position.db`）：warehouse_receipt(1496 行)、position_rank(4640 行) —— 仓单/席位数据直接复用，无需新增采集
- sqlite3 CLI 不可用，DB 操作用 python3

## 六、实施清单（确认后按长任务隔离执行）

1. 新建 agent 脚本：正极厂（需求侧）、期货基本面资金
2. 改造权益类资金（券商系：锂矿/电池/正极指数 + 研报看涨家数）；合并改造贸易商（屯库存状态机 + 正反套 + 赌仓单 + 双账户）
3. 拆分散户与游资+政策；CTA 保留 institution 模型；删除 resource
4. 研报采集：A 轨东财接口 + akshare 板块/资金流 cron；B 轨 Zhiji/手动注入
5. spot_price 数据源接入（基差交易前提）；`spread_2609_2610` 目前被用作基差 proxy，需替换为真实基差
6. 前端主体卡片 7→8 重排；`agents` 表结构扩展（贸易商双账户）

## 七、待用户拍板事项

1. 券商研报自动抓取依赖东财接口、失败退化为手动注入 —— 是否可接受？
2. 期货系研报先手动/知几注入，还是先试爬一个聚合源？
3. 确认按默认长任务隔离执行（新 agent 脚本 + 研报采集 cron + 前端卡片重排，子任务独立跑）？
4. 8 主体名单与行为逻辑最终确认（含贸易商 5 层设计、CTA 保留、锂矿删除）？

---

## 八、本次已交付文件

- `/home/ubuntu/lithium_calendar/docs/agent_behavior_model_compare_20260801.md` —— 现有 7 agent 行为模型（代码级）vs 新主体方案
- `/home/ubuntu/lithium_calendar/docs/trader_agent_design_20260801.md` —— 贸易商行为逻辑 5 层 + 仓位管理 + 数据库联动详细设计
- `/home/ubuntu/lithium_calendar/static/battlefield_agents.html` —— 已插入 📌 待办 Banner（黄色醒目提示）
