# 碳酸锂基本面六维综合指数系统设计文档

> 版本: v1.0 | 日期: 2026-08-01
> 项目: /home/ubuntu/lithium_calendar (端口 8766)
> 相关脚本:
>   - 指数计算: /home/ubuntu/.hermes/scripts/lithium_agents/fundamental_indices.py
>   - Agent 评分: /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py
> 数据表: `fundamental_indices` (lithium.db)

---

## 1. 背景与目标

原博弈系统 7 个 agent 的 view_score 完全由量价规则引擎驱动（日涨跌、持仓变化、月差、库存趋势、小作文情绪），
缺少对基本面供需/利润/基差等慢变量的系统化引用。本系统新增六维基本面滚动 z-score 综合指数，
作为**独立的慢变量信号层**，按各 agent 角色对维度的敏感度加权融入 view_score 与 7 维逻辑评分。

设计约束：
1. **防透视（无未来函数）**：所有标准化只用当日及之前的数据，禁止全样本标准化。
2. **向后兼容**：fundamental_indices 表不存在或读取失败时，Agent 评分完全回退原逻辑。
3. **分层隔离**：指数计算（纯客观数学）与产业解读（权重/角色映射）分离。

---

## 2. 六大基本面维度

| 维度 | 含义 | 数据源 (sheet, col_idx) | 原始方向 | 表内符号 |
|------|------|------------------------|----------|----------|
| supply 供应 | 产量/进口/开工供给强度 | 月度1: 1(月产总量), 6(进口总量); 周度: 12(周产量), 17(开工率) | 供应↑=利空 | 已翻转, +值=利多 |
| demand 需求 | 下游电池/材料需求 | 月度1: 10(磷酸铁锂), 9(三元), 14(动力电芯), 15(储能电芯); 周度: 25(周电芯) | 需求↑=利多 | +值=利多 |
| inventory 库存 | 社会库存水平 | 周度: 2(大样本), 6(样本), 11(四地社会) | 库存↑=利空 | 已翻转, +值=利多 |
| profit 利润 | 冶炼/矿端生产利润 | 日度价: 15(锂辉石利润), 16(锂云母利润) | 利润↑=利多 | +值=利多 |
| sentiment 情绪 | 现货成交/上下游情绪 | 日度价: 10(成交情绪), 11(出货情绪上游), 12(购货情绪下游) | 情绪↑=利多 | +值=利多 |
| basis 基差 | 期现价差/升贴水 | 日度价: 1(期现价差), 2(升贴水低幅), 3(升贴水高幅) | 基差强=利多 | +值=利多 |

**方向归一化原则**：供应、库存为"利空方向"指标（数值越高越利空），计算时乘 -1；
需求、利润、情绪、基差为"利多方向"指标（数值越高越利多），乘 +1。
因此**表内所有维度正值=利多，负值=利空**。

## 3. 防透视标准化方法

1. **滚动窗口 z-score**：对每个原始指标序列，窗口=250 交易日，只含当日及之前的数据：
   ```
   z_t = (x_t - mean(x_{t-249..t})) / std(x_{t-249..t})
   ```
   要求窗口内样本 ≥20 且 std>1e-9，否则该日不输出（避免短序列/常量序列噪音）。
2. **频率对齐**：周度/月度指标先算 z-score，再按交易日历 **forward-fill** 对齐到日度；
   只填充过去已发布值（不填充未来），维持无未来函数。
3. **方向翻转**：利空方向指标 z × (-1)。
4. **维度聚合**：维度内多指标 z-score 等权平均 → 该维度 z。
5. **综合指数 composite**：当日 ≥3 个维度有值时，取各维度 z 的均值；`n_dims` 记录实际参与维度数。
6. 写入 `fundamental_indices(date, supply, demand, inventory, profit, sentiment, basis, composite, n_dims)`，
   另输出近 180 日 JSON 摘要至 /home/ubuntu/analysis/output/lc_fundamental_indices.json。

## 4. 综合指数解读

- **z-score 语义**：|z| 越大偏离历史常态越远。z≈0 = 中性；z>0 = 利多（优于历史中枢）；z<0 = 利空。
- **composite**：六维等权平均，衡量整体基本面强弱。composite>0.5 偏强，<-0.5 偏弱，>1.5 显著过热/<-1.5 显著超跌。
- **维度分叉**：维度间方向矛盾是常态（如供给偏紧利多 vs 库存偏高利空），
  这正是多空分歧与博弈互动的来源（与 Phase 5.5 信号矛盾同理）。
- **使用场景**：
  - 多主体博弈打分（见第 5 节）：按角色加权融入 view_score；
  - 7 维逻辑评分修正：supply_risk 引用 supply+profit、demand_drop 引用 demand+sentiment 等；
  - 前端异动预警：|z|>2 时提示"基本面维度显著偏离常态"。

## 5. 多主体博弈打分映射

每个 agent 只对与其经营/交易角色最相关的维度敏感。权重公式（z × 权重 × SCALE → 分数贡献）：

| Agent | 角色 | 敏感维度与权重 | 逻辑依据 |
|-------|------|---------------|----------|
| smelter 冶炼厂 | 生产端 | supply×(-0.3) + profit×(0.4) + inventory×(-0.2) | 供给与库存是自身产销对手盘；利润影响增产/减产意愿 |
| resource 矿商 | 上游资源 | supply×(-0.3) + profit×(0.4) + inventory×(-0.2) | 同冶炼厂，供给+利润为核心 |
| merchant 贸易商 | 流通环节 | basis×(0.3) + inventory×(0.2) + profit×(0.2) | 基差结构决定持货/抛售；库存与利润影响购销决策 |
| institution 纯投机构 | 趋势资金 | composite×(0.5) | 最关心整体基本面强弱，单一维度噪音大 |
| arbitrage 套利商 | 价差交易 | basis×(0.4) + profit×(0.2) | 基差/升贴水是套利核心变量 |
| retail 散户/游资 | 情绪资金 | sentiment×(0.4) + composite×(0.2) | 情绪驱动为主，兼顾整体方向 |
| policy 政策/宏观 | 政策博弈 | （不参与）保持原逻辑 | 政策面由新闻事件独立驱动 |

实现参数（agent_daily_update.py）：
- `FUNDAMENTAL_SCALE = 30`：z-score → 分数贡献的放大系数（可调，约 1 个标准差 ≈ ±30 分量级，相对规则引擎 ±60~80 为次级修正）。
- `AGENT_CLAMP`：沿用各 agent 原有分数区间，基本面修正后仍按原区间 clamp，保证不越界。

**注意（符号约定）**：权重公式直接作用于表内"正值=利多"的 z 值。
例：smelter 的 supply×(-0.3) 含义为"供给偏紧(z>0)时冶炼厂反向偏空"（原料紧张压缩自身利润弹性），
如需调整方向仅需改权重符号，勿改表内数据符号。

---

## 6. 变更摘要（2026-08-01, Phase 6）

### 修改文件
`/home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py`

### 修改内容
1. **新增模块常量**（DB_PATH 之后）：
   - `FUNDAMENTAL_SCALE = 30`
   - `FUNDAMENTAL_WEIGHTS`：7 agent → 维度权重映射（policy 缺席）
   - `AGENT_CLAMP`：各 agent 分数 clamp 区间
2. **`compute_agent_score()`**：
   - 函数开头 try/except 读取 `fundamental_indices` 最新一行（supply/demand/inventory/profit/sentiment/basis/composite）；
     表不存在/读失败 → `fund=None` → 完全回退原逻辑（向后兼容）。
   - `fund` 作为新参数传入 `_compute_logic_scores()`。
   - return 前：按 `FUNDAMENTAL_WEIGHTS` 计算加权贡献 × `FUNDAMENTAL_SCALE` 加入 score
     （policy 跳过），并按 `AGENT_CLAMP` 再次 clamp；贡献 ≥0.5 时追加 reason "基本面加权+x.x"。
3. **`_compute_logic_scores()`**：签名追加 `fund=None`（向后兼容），各维度引用基本面：
   - supply_risk（smelter/resource）：`+ (-supply + profit) × 10`（供给偏紧→风险低；利润高→增产预期→风险高）
   - demand_drop（merchant/institution）：`+ (-demand × 10 - sentiment × 5)`（需求/情绪好→需求风险低）
   - inventory（merchant/arbitrage）：`- inventory × 10`（库存低→累库风险低）
   - cost_support（smelter/resource）：`- profit × 10`（利润高→离成本远→支撑弱）
   - sentiment（retail）：`+ sentiment × 10`
   - basis_trade（arbitrage/merchant）：`+ basis × 10`（基差强=现货升水→近强远弱→套利风险上升）
   - macro_policy（policy）：**不变**（保持原逻辑）
4. **回退机制**：fundamental_indices 表缺失/读取异常 → fund=None → 评分与逻辑评分均按原逻辑执行，不抛异常。

### 测试
运行 `/home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py` 无报错（见执行记录）。
