# 碳酸锂多主体博弈 — 方案：甘特图 + 博弈互动

> 2026-07-30 · 基于现有系统状态的设计方案
> 两个任务：① 逻辑甘特图 ② 主体博弈互动增强

---

## 一、交易逻辑甘特图（任务2）

### 1.1 现状分析

当前 `lithium_calendar` 缺少与锌 `zinc_review_version_B_deployed.html` 类似的**交易逻辑强度时序图**。锂盘目前有的：
- `logic_scanner.html` — 静态雷达图（前端架子，未实装）
- `battlefield_agents.html` — 按 agent 维度看观点变化（7条线，但这是"主体视角"而非"逻辑视角"）

**核心差异**：锌复盘的甘特图是**按8个交易逻辑**（如"冶炼亏损"、"国内需求"等）在时间轴上的强度变化，而锂盘的现有页面是按7个主体看评分变化。

### 1.2 设计方案

**新建文件**：`/home/ubuntu/lithium_calendar/static/lithium_logic_gantt.html`

**数据来源**：
- `agent_view_log` 表已经有每日观点记录，但它是按agent分的
- 需要从 `agent_action_log` + `agent_decisions`（需补充）中提取**逻辑评分**
- 或者：在 `agent_daily_update.py` 的 `compute_agent_score()` 中，把7个agent的评分**映射到交易逻辑维度**

**推荐方案：逻辑映射**

7个交易逻辑（参考锌复盘，适配碳酸锂产业特点）：

| 逻辑ID | 名称 | 对应agent | 评分来源 |
|--------|------|-----------|----------|
| supply_risk | 供给风险 | smelter + resource | 冶炼亏损+矿山停产 |
| demand_drop | 需求下滑 | merchant + institution | 下游采购+机构持仓变化 |
| inventory | 库存变化 | merchant + arbitrage | 社会库存+交易所库存 |
| macro_policy | 宏观/政策 | policy | 政策面事件 |
| cost_support | 成本支撑 | smelter + resource | 盐湖/矿石成本线 |
| sentiment | 情绪/小作文 | retail | 小作文情绪得分 |
| basis_trade | 基差/月差 | arbitrage + merchant | 期限结构 |

**实现**：
1. 在 `compute_agent_score()` 中增加 `logic_scores` 输出，每个agent打分映射到逻辑维度
2. 写入 `agent_view_log.key_indicators` 字段（已有，目前存的是JSON）
3. 后端新增 API `/api/logic_scores?days=30` 返回每日各逻辑评分
4. 前端用 ECharts heatmap 或 gantt 风格渲染

**渲染样式**（参考锌复盘）：
- 横轴：日期（近30/90天）
- 纵轴：7个交易逻辑
- 颜色：红色=空头逻辑主导，绿色=多头逻辑主导，深浅=强度
- 顶部：K线图（价格+成交量）
- 可交互：点击单元格显示当日详细解读

**文件结构**：
```
static/lithium_logic_gantt.html  — 新页面（约300行HTML/JS）
app.py — 新增 /api/logic_scores 端点（约30行Python）
agent_daily_update.py — compute_agent_score() 增加 logic_scores 输出（约20行）
```

**工作量**：约3小时

### 1.3 技术实现细节

```python
# agent_daily_update.py 中 compute_agent_score() 返回改为：
return score, reasons_text, delta_pos, logic_scores  # 新增

# logic_scores 是一个 dict:
{
    "supply_risk": 15,      # 供给风险评分 -100~100
    "demand_drop": -10,
    "inventory": 20,
    "macro_policy": 5,
    "cost_support": 30,
    "sentiment": -15,
    "basis_trade": 0
}
```

```python
# app.py 新增：
@app.route('/api/logic_scores')
def api_logic_scores():
    days = request.args.get('days', 30, type=int)
    # 从 agent_view_log 中提取 key_indicators 中的 logic_scores
    # 或从 agent_action_log 的 reasoning 字段解析
    # 返回每日7个逻辑的评分
```

---

## 二、主体博弈互动增强（任务3）

### 2.1 核心问题

当前7个agent**完全独立**——每个agent每天只看市场数据打分，彼此之间没有任何信息交换。

**博弈互动的本质**：agent A 的决策动作会影响 agent B 的判断。比如：
- 冶炼厂大规模增仓 → 贸易商感知到"大户在布局" → 改变自己的评分
- 散户追多 → 机构看到情绪过热 → 降低评分
- 矿商挺价 → 冶炼厂成本预期上升 → 提高多头评分

### 2.2 博弈互动设计方案

#### 2.2.1 三层互动模型

```
┌─────────────────────────────────────────────────┐
│  Layer 1: 信息传递（Information Transmission）     │
│  agent的动作/评分变化 → 作为其他agent的输入       │
├─────────────────────────────────────────────────┤
│  Layer 2: 博弈矩阵（Game Matrix）                  │
│  记录谁在逼谁：多头阵营 vs 空头阵营                │
│  以及博弈强度（持仓变化幅度）                       │
├─────────────────────────────────────────────────┤
│  Layer 3: 反馈循环（Feedback Loop）                │
│  极端博弈 → 触发 LLM 批量审核 → 动态调整评分      │
└─────────────────────────────────────────────────┘
```

#### 2.2.2 Layer 1：信息传递

**核心改动**：`compute_agent_score()` 的评分逻辑中加入"其他agent状态"作为因子。

现有逻辑（只看市场数据）：
```python
# 冶炼厂评分只看：库存变化、成本、价格
if inventory_decreasing: score += 30
if price_above_cost: score += 25
```

新增逻辑（加入博弈因子）：
```python
# 冶炼厂评分：看市场 + 看别人怎么动
if inventory_decreasing: score += 30
if price_above_cost: score += 25

# 【新增】博弈因子
if retail.position > 50:  # 散户持仓过高 → 可能过热 → 利好
    score += 10
if policy.view_score > 30:  # 政策面偏多 → 确认 → 利好
    score += 5
```

**每个agent的博弈因子权重**：

| Agent | 受谁影响 | 影响方向 | 权重 |
|-------|----------|----------|------|
| smelter（冶炼厂） | resource（矿商成本） | 正相关 | +10 |
| smelter | policy（政策面） | 正相关 | +5 |
| merchant（贸易商） | retail（散户情绪） | 负相关（反向） | -15 |
| merchant | smelter（大户动作） | 正相关 | +10 |
| institution（机构） | retail + merchant | 负相关（反向） | -20 |
| institution | smelter + resource | 正相关 | +10 |
| retail（散户） | institution + policy | 正相关 | +10 |
| resource（矿商） | policy + smelter | 正相关 | +5 |
| arbitrage（套利） | 所有agent | 无（独立） | 0 |
| policy（政策） | 无（外部输入） | 无 | 0 |

**实现**：在 `agent_daily_update.py` 的 `run_daily_update()` 函数中，先计算一轮规则评分，再叠加博弈因子。

#### 2.2.3 Layer 2：博弈矩阵

**新增数据表**：`agent_interactions`

```sql
CREATE TABLE agent_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    source_agent TEXT NOT NULL,   -- 动作发起者
    target_agent TEXT NOT NULL,   -- 受影响者
    interaction_type TEXT NOT NULL,  -- 'position_change' / 'score_change' / 'event'
    source_action REAL NOT NULL,  -- 动作幅度
    score_impact INTEGER NOT NULL, -- 评分影响（-20~20）
    direction TEXT,               -- 'bull' / 'bear' / 'neutral'
    description TEXT
);
```

**计算逻辑**：
```python
def compute_interactions(agents_dict, today, market):
    """计算当日agent之间的互动"""
    interactions = []
    
    for src_id, src in agents_dict.items():
        for tgt_id, tgt in agents_dict.items():
            if src_id == tgt_id:
                continue
            
            # 看持仓变化幅度
            pos_change = abs(src['position'] - src.get('prev_position', src['position']))
            if pos_change > 10:  # 变化超过阈值
                impact = 0
                direction = 'bull' if src['position'] > 0 else 'bear'
                
                # 查博弈矩阵
                if tgt_id == 'merchant' and src_id == 'retail':
                    impact = -15  # 散户追多→贸易商反向
                elif tgt_id == 'institution' and src_id == 'retail':
                    impact = -20  # 散户过热→机构反向
                elif tgt_id == 'merchant' and src_id == 'smelter':
                    impact = 10   # 大户增仓→贸易商跟风
                
                if impact != 0:
                    interactions.append({
                        'date': today,
                        'source_agent': src_id,
                        'target_agent': tgt_id,
                        'interaction_type': 'position_change',
                        'source_action': pos_change,
                        'score_impact': impact,
                        'direction': direction,
                        'description': f"{AGENT_NAME[src_id]}{direction}持仓变化{pos_change}→{AGENT_NAME[tgt_id]}评分{impact}"
                    })
    
    return interactions
```

**前端展示**：在 `battlefield_agents.html` 新增一个 "博弈矩阵" 区域
- 7x7 热力图矩阵，横轴=source，纵轴=target
- 颜色深浅 = 互动强度
- 鼠标悬停显示互动描述

#### 2.2.4 Layer 3：反馈循环 + LLM 审核

当博弈互动积累到一定程度时触发：
- 某agent的评分被博弈因子修正超过15分
- 或持仓变化超过历史80分位

这时标记 `needs_review=1`，触发 Phase 4 的 LLM 批量审核。

**LLM Prompt 中加入博弈上下文**：
```
市场快照：
- 今日收盘 X, 涨跌 Y%
- 持仓变化: 冶炼厂 +50, 散户 +30

博弈矩阵（今日互动）：
- 散户追多 → 机构反向修正 -20（机构原本看多5→-15）
- 冶炼厂增仓 → 贸易商跟风 +10

请基于以上信息重新评估各agent：
1. 是否有过度反应需要纠正？
2. 博弈信号是否可信？
3. 操作建议调整？
```

### 2.3 前端新增模块

在 `battlefield_agents.html` 中新增：

1. **博弈矩阵热力图**（ECharts heatmap）
   - 7x7 矩阵
   - 显示互动强度和方向

2. **互动时间线**（ECharts timeline）
   - 每日互动事件列表
   - 显示"谁影响了谁"

3. **博弈轮次总结**
   - 每日一张卡片：
     - 今日主导博弈：散户追多 vs 机构反向
     - 最大互动：冶炼厂增仓→贸易商跟风（+15分）
     - 博弈强度评分：0-100

### 2.4 工作量估算

| 模块 | 文件 | 行数 | 时间 |
|------|------|------|------|
| Layer 1：博弈因子 | `agent_daily_update.py` | +40行 | 30分钟 |
| Layer 2：博弈矩阵表+API | `lithium_api.py` + `app.py` | +60行 | 1小时 |
| Layer 3：LLM Prompt扩展 | `lithium_agents.py` review接口 | +30行 | 30分钟 |
| 前端：博弈矩阵图 | `battlefield_agents.html` | +120行 | 2小时 |
| 前端：互动时间线 | `battlefield_agents.html` | +80行 | 1小时 |
| 前端：博弈轮次卡片 | `battlefield_agents.html` | +60行 | 1小时 |
| **合计** | | **+390行** | **~6小时** |

### 2.5 迭代计划

**第一版（MVP）**：只做 Layer 1（博弈因子）+ 前端展示
- 不需要新表
- 评分逻辑中加几个if-else
- 前端加一个热力图
- 2小时可完成

**第二版**：加入 Layer 2（博弈矩阵表）
- 新增 `agent_interactions` 表
- 后端计算互动记录
- 前端热力图渲染真实数据

**第三版**：加入 Layer 3（LLM审核联动）
- 博弈触发审核
- Prompt加入博弈上下文
- 这才是真正的"博弈"

---

## 三、实施建议

### 推荐顺序

1. **先做甘特图**（3小时，立即可用，对交易决策直接有帮助）
2. **再做博弈因子Layer 1**（2小时，改动最小，立竿见影）
3. **最后做博弈矩阵+LLM联动**（4小时+，锦上添花）

### 风险点

1. **博弈因子权重**：初始权重是经验值，需要跑一段时间数据后调参
2. **回测困难**：博弈互动改变了历史评分，但历史数据没有交互记录，需要先跑几天的Layer 1再回算
3. **LLM审核延迟**：如果博弈触发太多LLM调用，成本可能失控——建议设上限（每天最多1次）

### 关键API设计

```
GET  /api/logic_scores?days=30   — 交易逻辑评分时序（甘特图数据）
GET  /api/interactions?days=7    — 近期博弈互动
GET  /api/game_matrix            — 当前博弈矩阵（7x7）
GET  /api/battlefield            — 已有，增强返回博弈字段
POST /api/agents/review          — 已有（占位），加入博弈上下文
```

---

## 四、最终页面结构

```
http://124.221.113.37:8766/          — 主页
├── /battlefield_agents.html         — 多主体博弈（增强版）
│   ├── Summary Bar（现有）
│   ├── 7 Agent Cards（现有）
│   ├── 持仓对比 + 多空饼图（现有）
│   ├── 观点评分分布（现有）
│   ├── 历史观点甘特图（现有）
│   ├── [新增] 博弈矩阵热力图
│   ├── [新增] 互动时间线
│   └── [新增] 每日博弈轮次卡片
│
├── /lithium_logic_gantt.html        — 交易逻辑甘特图（新页面）
│   ├── K线 + 成交量（顶部）
│   ├── 7逻辑强度时序图（中间）
│   ├── 逻辑强度热力图
│   └── 主导逻辑轮动条带
│
└── /lithium_balance_v2.html         — 供需平衡（已有）
    └── [待增强] 历史平衡表转折点
```

**估算百分比：~30%**
