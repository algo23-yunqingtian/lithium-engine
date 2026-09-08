# 7 主体逻辑 + 阈值速查

> Agent 决策逻辑参考文档。覆盖 7 主体参数、公共参数、博弈因子体系、逻辑评分维度、信号互动机制。

---

## 一、7 主体速查表

| 主体 | agent_id | clamp 区间 | 信息敏感度 | 行为敏感度 | 核心逻辑 |
|------|----------|-----------|------------|------------|----------|
| 冶炼厂 | smelter | (-60, +80) | 0.3 | 0.2 | 成本线下方减产挺价，上方套保增库 |
| 矿商 | resource | (-50, +70) | 0.3 | 0.2 | 价格跌破成本线减少外采，上方套保 |
| 贸易商 | merchant | (-50, +60) | 0.5 | 0.5 | 低买高卖赚价差，库存周转驱动 |
| 机构 | institution | (-70, +70) | 0.7 | 0.6 | 基本面+技术面综合判断，趋势跟踪 |
| 套利商 | arbitrage | (-20, +20) | 0.6 | 0.4 | 月差/基差套利，仓位小且稳定 |
| 散户 | retail | (-60, +60) | 0.9 | 0.9 | 情绪驱动追涨杀跌，信息最不透明 |
| 政策 | policy | (-60, +60) | 0.2 | 0.1 | 价格锚定区间外才出手，反应最慢 |

### clamp 说明

- `view_score` 经博弈修正后，用 `clamp(min, max)` 限制在区间内
- 区间不对称者（如冶炼厂 -60/+80）反映非对称风险偏好：下方减产空间 < 上方套保空间
- 套利商区间最窄（±20），天然低波动

### 敏感度说明

- **信息敏感度**：主体对市场信息的反应速度（0~1，越高越快响应）
- **行为敏感度**：主体将观点转化为持仓变动的力度（0~1，越高调仓越激进）
- 散户最高（0.9/0.9）：情绪传导快、调仓激进
- 政策最低（0.2/0.1）：信息迟钝、行动克制

---

## 二、公共参数表

| 参数 | 值 | 说明 |
|------|-----|------|
| 惯性 | ±20/日 | 每日观点变化上限 ±20 分 |
| 止损 | 8% 全平 | 亏损达 8% 全部平仓 |
| 止盈 | 15% 减半 | 盈利达 15% 减半仓位 |
| 仓位上限 | 30%/次 | 单次调仓不超过总仓位 30% |
| 成本线 | 125,000 | 行业平均成本线（元/吨） |
| 政策锚上沿 | 160,000 | 政策干预上限价格 |
| 政策锚下沿 | 130,000 | 政策干预下限价格 |
| 博弈修正 | ±10 | 单次博弈修正最大幅度 |

### 参数使用场景

```
view_score_change = clamp(raw_change, -20, +20)          # 惯性限制
view_score = clamp(base + correction, min, max)          # clamp 区间
position_change = view_score * behavior_sensitivity * 0.3 # 行为敏感度 × 仓位上限
if loss_ratio >= 8%: close_all()                          # 止损
if profit_ratio >= 15%: reduce_half()                     # 止盈
if price < 130000: policy_view -= adjust                 # 政策锚下沿
if price > 160000: policy_view += adjust                 # 政策锚上沿
```

---

## 三、博弈因子体系

### 因子结构

| 因子 | 字段 | 取值范围 | 说明 |
|------|------|----------|------|
| 基础得分 | base_score | -100~+100 | 信息敏感度 × 基本面信号 |
| 信息传递修正 | info_transfer | -10~+10 | 主体间信息流动导致的观点修正 |
| 行为影响修正 | behavioral_impact | -10~+10 | 其他主体持仓变化导致的跟风修正 |
| 总修正量 | total_correction | -20~+20 | info_transfer + behavioral_impact |
| 修正后得分 | corrected_score | clamp 后 | base_score + total_correction，经 clamp |

### 修正流程

```
1. 各主体独立计算 base_score（基于基本面+价格+自身信息敏感度）
2. 计算 info_transfer：高信息敏感度主体→低敏感度主体的观点溢出
3. 计算 behavioral_impact：大仓位主体调仓→其他主体跟风修正
4. total_correction = clamp(info_transfer + behavioral_impact, -10, +10)
5. corrected_score = clamp(base_score + total_correction, min, max)
6. 写入 agent_game_factors 表
```

### 因子明细（factors_detail JSON 结构）

```json
{
  "base_signals": ["supply↑", "inventory↓", "basis>0"],
  "info_from": ["institution→retail: +5", "smelter→merchant: +3"],
  "behavior_follow": ["institution 减仓→merchant 跟随: -4"],
  "correction_breakdown": {"info": 2, "behavior": -4, "total": -2}
}
```

---

## 四、7 维逻辑评分维度

`logic_scores` 表记录每日各逻辑维度的评分，7 个维度如下：

| 维度 | 逻辑名称 | 评分依据 | 影响主体 |
|------|----------|----------|----------|
| 1 | 供给逻辑 | 产量/开工率/进口量变化 | 矿商、冶炼厂 |
| 2 | 需求逻辑 | 下游排产/正极材料产量 | 贸易商、机构 |
| 3 | 库存逻辑 | 显性库存/隐性库存/仓单 | 机构、贸易商 |
| 4 | 利润逻辑 | 冶炼利润/矿山利润/成本线 | 冶炼厂、矿商 |
| 5 | 情绪逻辑 | 持仓变化/成交/散户情绪 | 散户、机构 |
| 6 | 基差逻辑 | 现货-期货价差/月差结构 | 套利商、贸易商 |
| 7 | 政策逻辑 | 收储/抛储/产业政策/价格锚 | 政策、全部主体 |

### 评分规则

- 每个维度 0~100 分（50 为中性，>50 偏多，<50 偏空）
- `avg_score`：当日所有参评主体的平均分
- `min_score` / `max_score`：最低/最高评分（反映分歧度）
- `agent_count`：参评主体数（通常 7）

---

## 五、信号互动机制

### 互动链路

```
基本面信号（fundamental_indices）
    │
    ├──→ 供给↑/↓ ──→ 矿商减/增产 ──→ 冶炼厂成本变动
    │                              ──→ 贸易商采购策略调整
    │
    ├──→ 需求↑/↓ ──→ 贸易商囤/抛货 ──→ 机构趋势判断
    │                               ──→ 散户跟风
    │
    ├──→ 库存↑/↓ ──→ 机构加/减仓  ──→ 散户追涨/杀跌
    │                              ──→ 套利商基差调整
    │
    ├──→ 利润↓ ──→ 冶炼厂减产挺价 ──→ 贸易商看多
    │
    ├──→ 基差扩大 ──→ 套利商开仓 ──→ 机构关注期现结构
    │
    └──→ 价格触及政策锚 ──→ 政策主体出手 ──→ 全部主体修正
```

### agent_interactions 互动类型

| influence_type | 说明 | 典型链路 |
|----------------|------|----------|
| info_spillover | 信息溢出 | institution→retail（机构观点传导给散户） |
| behavior_follow | 行为跟随 | institution→merchant（贸易商跟随机构调仓） |
| cost_pressure | 成本压力 | smelter→resource（冶炼厂减产传导至矿商） |
| policy_signal | 政策信号 | policy→all（政策干预影响全部主体） |
| arbitrage_pressure | 套利压力 | arbitrage→institution（套利商改变期现结构影响机构） |
| sentiment_contagion | 情绪传染 | retail→merchant（散户情绪影响贸易商短期行为） |

### magnitude（影响幅度）规则

- 范围 0~1，表示对目标主体 view_score 的修正比例
- `actual_correction = magnitude × source_view_change × target_info_sensitivity`
- 高信息敏感度主体接受更多修正（散户 0.9 > 机构 0.7 > 贸易商 0.5）

---

## 六、每日推演流程（18:30 cron）

```
Step 1: 读取最新价格（prices.close）+ 基本面指数（fundamental_indices）
Step 2: 各主体独立计算 base_score
         ├── 冶炼厂: 价格 vs 成本线(125000) → 减产/套保倾向
         ├── 矿商: 价格 vs 开采成本 → 外采量调整
         ├── 贸易商: 价差/库存 → 囤货/抛货
         ├── 机构: 基本面综合 → 趋势方向
         ├── 套利商: 基差/月差 → 套利空间
         ├── 散户: 价格动量/成交 → 追涨杀跌
         └── 政策: 价格 vs 锚定区间(130K~160K) → 干预力度
Step 3: 惯性修正（±20/日限制）
Step 4: 博弈因子修正（info_transfer + behavioral_impact，±10限制）
Step 5: clamp 到各主体区间
Step 6: 止损/止盈检查
Step 7: 计算新持仓（view_score × behavior_sensitivity × 30%仓位上限）
Step 8: 写入 agent_history / agent_action_log / agent_game_factors / agent_interactions
Step 9: 更新 agents 表当前状态
```

---

## 七、心理状态表（agent_psychology）

| 字段 | 说明 |
|------|------|
| state | 当前心理状态（贪婪/恐惧/观望/犹豫/决断） |
| anchor_price | 锚定价格（影响买卖决策的参照点） |
| buy_zone_low/high | 买入区间（价格落入此区间触发买入） |
| trigger_desc | 触发当前状态的描述 |
| score | 心理得分（0~100，50=中性） |
| state_color | 颜色标签（红=贪婪/绿=恐惧/灰=观望/黄=犹豫/蓝=决断） |

- **行数**：336 行（7 主体 × ~48 天）
- 心理状态每日更新，影响 base_score 的情绪调整项
